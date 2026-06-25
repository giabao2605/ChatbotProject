r"""P1.7 - Doi soat SQL <-> Qdrant.

Muc tieu: phat hien va don dep lech du lieu giua SQL (bang TaiLieu) va vector store (Qdrant):
  1. Orphan vectors: vector trong Qdrant nhung DocID khong con trong SQL.
  2. Tai lieu ket o trang thai 'deleting' qua lau -> hard-delete an toan.

MAC DINH la DRY-RUN (chi bao cao, KHONG xoa gi). Them --fix de thuc su don dep.

Cach chay (Windows PowerShell):
    $env:PYTHONPATH="src"; python scripts\danger_ops\reconcile_sql_qdrant.py
    $env:PYTHONPATH="src"; python scripts\danger_ops\reconcile_sql_qdrant.py --fix
    $env:PYTHONPATH="src"; python scripts\danger_ops\reconcile_sql_qdrant.py --stuck-hours 6 --fix
"""
import os
import sys
import argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import text
from mech_chatbot.db import repository as repo

COLLECTION = os.getenv("QDRANT_COLLECTION", "TaiLieuKyThuat_v2")


def _get_sql_doc_ids():
    repo._ensure_engine()
    with repo.engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DocID FROM TaiLieu WHERE ISNULL(LifecycleStatus, '') <> 'deleting'"
        )).fetchall()
    return {str(r[0]) for r in rows}


def _get_stuck_deleting(stuck_hours):
    repo._ensure_engine()
    with repo.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DocID, TenFile
            FROM TaiLieu
            WHERE LifecycleStatus = 'deleting'
              AND DATEDIFF(hour, ISNULL(NgayTaiLen, GETDATE()), GETDATE()) >= :h
        """), {"h": int(stuck_hours)}).fetchall()
    return [(str(r[0]), r[1]) for r in rows]


def _scroll_qdrant_doc_ids(client):
    from collections import defaultdict
    counts = defaultdict(int)
    next_offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=COLLECTION,
            with_payload=True,
            with_vectors=False,
            limit=512,
            offset=next_offset,
        )
        for p in points:
            payload = p.payload or {}
            meta = payload.get("metadata", payload)
            doc_id = meta.get("doc_id") or payload.get("doc_id")
            if doc_id is not None:
                counts[str(doc_id)] += 1
        if not next_offset:
            break
    return counts


def main():
    parser = argparse.ArgumentParser(description="Doi soat SQL <-> Qdrant (P1.7)")
    parser.add_argument("--fix", action="store_true", help="Thuc su don dep (mac dinh chi dry-run).")
    parser.add_argument("--stuck-hours", type=int, default=6, help="Nguong gio coi la 'deleting' bi ket.")
    args = parser.parse_args()

    mode = "FIX" if args.fix else "DRY-RUN"
    print(f"=== Doi soat SQL <-> Qdrant [{mode}] | collection={COLLECTION} ===")

    try:
        client = repo._get_qdrant_client()
    except Exception as e:
        print(f"[LOI] Khong ket noi duoc Qdrant: {e}")
        return 1

    sql_ids = _get_sql_doc_ids()
    print(f"SQL: {len(sql_ids)} tai lieu hop le.")
    qdrant_counts = _scroll_qdrant_doc_ids(client)
    print(f"Qdrant: {len(qdrant_counts)} doc_id, tong {sum(qdrant_counts.values())} vector.")

    orphan_ids = [d for d in qdrant_counts if d not in sql_ids]
    orphan_vec = sum(qdrant_counts[d] for d in orphan_ids)
    print(f"\n[1] Orphan vectors: {len(orphan_ids)} doc_id ({orphan_vec} vector) co trong Qdrant nhung khong co trong SQL.")
    for d in orphan_ids[:50]:
        print(f"    - doc_id={d} ({qdrant_counts[d]} vector)")
    if len(orphan_ids) > 50:
        print(f"    ... va {len(orphan_ids) - 50} doc_id khac.")

    stuck = _get_stuck_deleting(args.stuck_hours)
    print(f"\n[2] Tai lieu ket 'deleting' > {args.stuck_hours}h: {len(stuck)}")
    for doc_id, ten in stuck:
        print(f"    - DocID={doc_id} | {ten}")

    if not args.fix:
        print("\n(DRY-RUN) Khong xoa gi. Them --fix de don dep.")
        return 0

    from qdrant_client import models as qmodels
    fixed_vec = 0
    for d in orphan_ids:
        try:
            client.delete(
                collection_name=COLLECTION,
                points_selector=qmodels.FilterSelector(filter=qmodels.Filter(
                    must=[qmodels.FieldCondition(
                        key="metadata.doc_id",
                        match=qmodels.MatchValue(value=d),
                    )]
                )),
            )
            fixed_vec += qdrant_counts[d]
        except Exception as e:
            print(f"    [LOI] Khong xoa duoc orphan doc_id={d}: {e}")
    print(f"\n[FIX-1] Da xoa orphan vectors cua {len(orphan_ids)} doc_id (~{fixed_vec} vector).")

    fixed_docs = 0
    for doc_id, ten in stuck:
        try:
            if repo.delete_document_completely(doc_id, reviewer="reconcile_job"):
                fixed_docs += 1
        except Exception as e:
            print(f"    [LOI] Khong xoa duoc DocID={doc_id}: {e}")
    print(f"[FIX-2] Da hard-delete {fixed_docs}/{len(stuck)} tai lieu ket 'deleting'.")

    print("\n=== Hoan tat doi soat ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
