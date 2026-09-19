"""Hash the current SQL/Qdrant serving state without persisting source data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
for import_root in (ROOT, ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from sqlalchemy import text

from mech_chatbot.adapters.qdrant_runtime import build_qdrant_admin_runtime
from mech_chatbot.config.settings import (
    QdrantSettings,
    SqlSettings,
    load_settings,
)
from mech_chatbot.db.engine import build_database_runtime
from scripts.eval.verify_failure_family_rollback import clean_git_sha


_SQL_SERVING_TABLES = (
    "_SchemaVersions",
    "DocumentFamily",
    "TaiLieu",
    "TaiLieuKyThuat",
    "DocumentPages",
    "BangKeVatTu",
    "TechnicalAttributes",
    "DocumentAttributes",
    "MaterialDictionary",
    "MaterialSynonym",
    "PhongBanChiaSe",
    "Departments",
    "Sites",
    "Roles",
    "UserRoles",
    "UserDepartments",
    "UserSecurityClearance",
    "UserSites",
    "DepartmentDomainProfile",
    "DepartmentKnowledgeGovernance",
    "DomainGlossary",
    "KnowledgeGraphNode",
    "KnowledgeGraphEdge",
    "GraphCommunityVersion",
    "GraphCommunityMembership",
    "GraphCommunitySummary",
)
_SQL_SERVING_QUERIES = (
    *(
        (table, f"SELECT * FROM dbo.[{table}]")
        for table in _SQL_SERVING_TABLES
    ),
    (
        "UsersServingMetadata",
        "SELECT UserID, Username, Department, IsActive "
        "FROM dbo.[Users]",
    ),
)


def _json_bytes(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _hash_rows(rows) -> dict[str, object]:
    digests = sorted(
        hashlib.sha256(_json_bytes(dict(row))).digest()
        for row in rows
    )
    aggregate = hashlib.sha256()
    for digest in digests:
        aggregate.update(digest)
    return {"row_count": len(digests), "sha256": aggregate.hexdigest()}


def _model_dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _qdrant_identity(client, collection: str) -> dict[str, object]:
    point_digests = []
    offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        point_digests.extend(
            hashlib.sha256(_json_bytes({
                "id": point.id,
                "payload": point.payload,
                "vector": point.vector,
            })).digest()
            for point in points
        )
        if next_offset is None:
            break
        if next_offset == offset:
            raise RuntimeError("Qdrant scroll offset did not advance")
        offset = next_offset
    expected = int(client.count(
        collection_name=collection,
        exact=True,
    ).count)
    if len(point_digests) != expected:
        raise RuntimeError("Qdrant point count changed during state capture")
    aggregate = hashlib.sha256()
    for digest in sorted(point_digests):
        aggregate.update(digest)
    info = client.get_collection(collection)
    collection_config = {
        "config": _model_dump(info.config),
        "payload_schema": {
            str(field): _model_dump(schema)
            for field, schema in info.payload_schema.items()
        },
    }
    return {
        "point_count": expected,
        "sha256": aggregate.hexdigest(),
        "config_sha256": hashlib.sha256(
            _json_bytes(collection_config)
        ).hexdigest(),
    }


def _sql_identity(connection) -> dict[str, dict[str, object]]:
    return {
        name: _hash_rows(
            connection.execute(text(query)).mappings()
        )
        for name, query in _SQL_SERVING_QUERIES
    }


def build_runtime_state_identity(
    connection,
    client,
    *,
    git_sha: str,
    database: str,
    collection: str,
) -> dict[str, object]:
    sql = _sql_identity(connection)
    qdrant = _qdrant_identity(client, collection)
    if (
        qdrant != _qdrant_identity(client, collection)
        or sql != _sql_identity(connection)
    ):
        raise RuntimeError("serving data changed during state capture")
    identity = {
        "schema": "rag-runtime-state-v1",
        "git_sha": git_sha,
        "database": database,
        "collection": collection,
        "sql": sql,
        "qdrant": qdrant,
    }
    return {
        **identity,
        "snapshot_fingerprint": hashlib.sha256(
            _json_bytes(identity)
        ).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-sha")
    args = parser.parse_args()
    if args.git_sha and not re.fullmatch(r"[0-9a-f]{40}-dirty-demo", args.git_sha):
        parser.error("--git-sha must be a commit SHA suffixed with -dirty-demo")

    settings = load_settings()
    sql_runtime = build_database_runtime(
        SqlSettings.from_settings(settings)
    )
    qdrant_runtime = build_qdrant_admin_runtime(
        QdrantSettings.from_settings(settings)
    )
    try:
        with sql_runtime.engine.connect() as raw_connection:
            connection = raw_connection.execution_options(
                isolation_level="SERIALIZABLE"
            )
            with connection.begin():
                identity = build_runtime_state_identity(
                    connection,
                    qdrant_runtime.client,
                    git_sha=args.git_sha or clean_git_sha(ROOT),
                    database=settings.SQL_DATABASE,
                    collection=settings.QDRANT_COLLECTION,
                )
    finally:
        qdrant_runtime.close()
        sql_runtime.close()
    print(identity["snapshot_fingerprint"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
