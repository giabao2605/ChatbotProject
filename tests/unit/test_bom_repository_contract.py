from __future__ import annotations

from decimal import Decimal
import json

import pytest

from mech_chatbot.db import registry_ports
from mech_chatbot.db.repositories import bom

from ._small_repository_fakes import Engine, Result


pytestmark = pytest.mark.unit


def _install(monkeypatch, outcomes=(), **engine_kwargs):
    fake = Engine(outcomes, **engine_kwargs)
    monkeypatch.setattr(bom, "_ensure_engine", lambda: None)
    monkeypatch.setattr(bom, "engine", fake)
    return fake


def _row(*, quantity=2, raw=None):
    return (
        41,
        3,
        "P-100",
        "Pump",
        "SUS304",
        quantity,
        "note",
        "manual.pdf",
        4,
        "internal",
        "HQ",
        "all_external",
        301,
        "piece",
        raw,
    )


def test_material_normalization_uses_registered_domain_port(monkeypatch):
    monkeypatch.setattr(registry_ports, "normalize_material", lambda raw: f"canonical:{raw}")

    assert bom.normalize_material_name("SUS304") == "canonical:SUS304"
    assert bom.normalize_material_name(None) is None


def test_material_normalization_has_deterministic_fallback_when_registry_fails(monkeypatch):
    def unavailable(_raw):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(registry_ports, "normalize_material", unavailable)

    assert bom.normalize_material_name("  INOX   SS304  ") == "stainless steel sus 304"


def test_save_records_rejects_missing_document_or_empty_batch_without_database_access(monkeypatch):
    fake = _install(monkeypatch)

    assert bom.save_bom_records(None, 1, [{"ma_hang": "P-1"}]) == 0
    assert bom.save_bom_records(1, 1, []) == 0
    assert fake.connection.calls == []


def test_save_records_bulk_inserts_sanitized_rows(monkeypatch):
    fake = _install(monkeypatch, (Result(),))
    monkeypatch.setattr(registry_ports, "normalize_material", lambda raw: raw.strip().lower())

    count = bom.save_bom_records(
        41,
        3,
        [
            {
                "ma_hang": "P-100",
                "ten_vat_tu": "Pump",
                "vat_lieu": " SUS304 ",
                "so_luong": "2",
                "ghi_chu": "note",
                "don_vi": "piece",
                "confidence": 0.9,
                "raw_row_json": "{}",
                "source_table_index": 4,
            },
            {"ma_hang": "P-200", "so_luong": "bad"},
        ],
    )

    assert count == 2
    rows = fake.connection.calls[0][1]
    assert rows[0]["normalized_material"] == "sus304"
    assert rows[0]["sl"] == 2
    assert rows[1]["sl"] is None
    assert all(row["doc_id"] == 41 and row["trang_so"] == 3 for row in rows)


def test_save_records_returns_zero_on_database_failure(monkeypatch):
    _install(monkeypatch, begin_error=RuntimeError("offline"))
    assert bom.save_bom_records(41, 1, [{"ma_hang": "P-1"}]) == 0


def test_fact_search_requires_scope_and_authenticated_roles(monkeypatch):
    fake = _install(monkeypatch)

    assert bom.search_bom_facts(user_roles=["admin"]) == []
    assert bom.search_bom_facts(part_codes=["P-1"], user_roles=[]) == []
    assert fake.connection.calls == []


def test_admin_code_search_uses_fulltext_and_returns_typed_provenance(monkeypatch):
    raw = json.dumps({"quantity_decimal": "2.50", "source_row_id": "BOM-SOURCE-1"})
    fake = _install(monkeypatch, (Result(scalar_value=1), Result(rows=(_row(raw=raw),))))

    result = bom.search_bom_facts(part_codes=[" P-100 "], user_roles=[" Admin "])

    assert result == [
        bom.BomSearchRow(
            41,
            3,
            "P-100",
            "Pump",
            "SUS304",
            Decimal("2.50"),
            "note",
            "manual.pdf",
            4,
            "internal",
            "HQ",
            "all_external",
            301,
            "piece",
            "BOM-SOURCE-1",
        )
    ]
    sql, params = fake.connection.calls[1]
    assert "CONTAINS(b.MaHang, :m0)" in sql
    assert params == {"m0": '"P-100*"', "ml0": "%P-100%"}


def test_code_search_falls_back_to_like_without_fulltext(monkeypatch):
    fake = _install(monkeypatch, (Result(scalar_value=0), Result(rows=())))

    assert bom.search_bom_facts(part_codes=["P-100"], user_roles=["admin"]) == []
    sql, params = fake.connection.calls[1]
    assert "b.MaHang LIKE :m0" in sql
    assert params["m0"] == "%P-100%"


@pytest.mark.security
def test_non_admin_search_applies_department_clearance_and_strict_site(monkeypatch):
    fake = _install(monkeypatch, (Result(scalar_value=0), Result(rows=())))

    bom.search_bom_facts(
        part_codes=["P-100"],
        version_policy="specific_version",
        detected_versions=[4],
        user_roles=["viewer"],
        allowed_departments=["Technical"],
        max_security_level="internal",
        allowed_sites=["HQ"],
    )

    sql, params = fake.connection.calls[1]
    assert "t.VersionNo = 4" in sql
    assert "pbc.DeptCode = :dept0" in sql
    assert "t.SecurityLevel IS NOT NULL" in sql
    assert "t.Site IS NOT NULL" in sql
    assert params["dept0"] == "Technical"
    assert params["dept1"] == "CHUNG"
    assert params["sec0"] == "public"
    assert params["sec1"] == "internal"
    assert params["site0"] == "HQ"


@pytest.mark.security
def test_confidential_clearance_allows_legacy_empty_security_and_compat_empty_site(monkeypatch):
    monkeypatch.setenv("RBAC_STRICT_SITE_FILTER", "false")
    fake = _install(monkeypatch, (Result(scalar_value=0), Result(rows=())))

    bom.search_bom_facts(
        part_codes=["P-100"],
        version_policy="compare_versions",
        detected_versions=[2, 4],
        user_roles=["viewer"],
        allowed_departments=[],
        max_security_level="confidential",
        allowed_sites=["HQ"],
    )

    sql, params = fake.connection.calls[1]
    assert "t.VersionNo IN (2,4)" in sql
    assert "t.SecurityLevel IS NULL" in sql
    assert "t.Site IS NULL" in sql
    assert params["dept0"] == "CHUNG"
    assert params["sec2"] == "confidential"


@pytest.mark.security
def test_non_admin_without_allowed_sites_is_forced_to_empty_result(monkeypatch):
    fake = _install(monkeypatch, (Result(scalar_value=0), Result(rows=())))

    bom.search_bom_facts(
        document_ids=[42, "41", 41],
        user_roles=["viewer"],
        allowed_departments=["Technical"],
        max_security_level="public",
        allowed_sites=[],
    )

    sql, params = fake.connection.calls[1]
    assert "AND 1 = 0" in sql
    assert json.loads(params["document_ids"]) == [41, 42]


def test_document_scope_narrows_code_search_and_unknown_policy_defaults_current(monkeypatch):
    fake = _install(monkeypatch, (Result(scalar_value=0), Result(rows=())))

    bom.search_bom_facts(
        part_codes=["P-100"],
        document_ids=[41],
        version_policy="unknown",
        user_roles=["admin"],
    )

    sql, params = fake.connection.calls[1]
    assert "1=1 AND b.DocID IN" in sql
    assert "t.IsCurrent = 1" in sql
    assert params["document_ids"] == "[41]"


def test_fact_search_degrades_invalid_quantity_and_source_metadata(monkeypatch):
    fake = _install(monkeypatch, (Result(scalar_value=0), Result(rows=(_row(quantity="bad", raw="not-json"),))))

    result = bom.search_bom_facts(part_codes=["P-100"], user_roles=["admin"])

    assert result[0].quantity is None
    assert result[0].source_row_id == "BOM-301"


def test_fact_search_fails_closed_on_database_error(monkeypatch):
    _install(monkeypatch, connect_error=RuntimeError("offline"))
    assert bom.search_bom_facts(part_codes=["P-100"], user_roles=["admin"]) == []


def test_legacy_code_search_forwards_public_scope(monkeypatch):
    observed = {}

    def capture(**kwargs):
        observed.update(kwargs)
        return ["row"]

    monkeypatch.setattr(bom, "search_bom_facts", capture)

    result = bom.search_bom_by_code(
        ["P-1"],
        version_policy="specific_version",
        detected_versions=[4],
        user_department="Technical",
        user_roles=["viewer"],
        allowed_departments=["Technical"],
        max_security_level="internal",
        allowed_sites=["HQ"],
    )

    assert result == ["row"]
    assert observed == {
        "part_codes": ["P-1"],
        "version_policy": "specific_version",
        "detected_versions": [4],
        "user_department": "Technical",
        "user_roles": ["viewer"],
        "allowed_departments": ["Technical"],
        "max_security_level": "internal",
        "allowed_sites": ["HQ"],
    }
