"""Explicit database adapters for shared registry policy.

The old module was a mutable callback registry with a dynamic import fallback.
This module now owns the database lookups directly and delegates deterministic
fallback and normalization to ``domain.registry_policy``.
"""

from __future__ import annotations

import threading
import time

from sqlalchemy import text

from mech_chatbot.domain import registry_policy


_CACHE_TTL = 300
_material_cache_lock = threading.Lock()
_material_cache = {"ts": 0.0, "materials": None}


def _repository():
    from mech_chatbot.db import repository

    return repository


def _department_domain_security_row(department):
    if not department:
        return None
    try:
        repository = _repository()
        repository._ensure_engine()
        if repository.engine is None:
            return None
        with repository.engine.connect() as connection:
            try:
                return connection.execute(
                    text(
                        "SELECT Domain, DefaultSecurity "
                        "FROM dbo.Departments "
                        "WHERE DeptCode = :department OR FolderGoc = :department"
                    ),
                    {"department": department},
                ).fetchone()
            except Exception:
                row = connection.execute(
                    text(
                        "SELECT Domain, NULL FROM dbo.Departments "
                        "WHERE DeptCode = :department"
                    ),
                    {"department": department},
                ).fetchone()
                return row
    except Exception:
        return None


def _department_site(department):
    if not department:
        return None
    try:
        repository = _repository()
        repository._ensure_engine()
        if repository.engine is None:
            return None
        with repository.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT Site FROM dbo.Departments "
                    "WHERE DeptCode = :department OR FolderGoc = :department"
                ),
                {"department": department},
            ).fetchone()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None


def resolve_domain_by_department(department):
    row = _department_domain_security_row(department)
    if row:
        domain = registry_policy.normalize_domain_value(row[0])
        if domain:
            return domain
    return registry_policy.resolve_domain_fallback(department)


def resolve_security_by_department(department):
    row = _department_domain_security_row(department)
    if row and len(row) > 1 and row[1]:
        return str(row[1]).strip().lower()
    domain = registry_policy.normalize_domain_value(row[0]) if row else None
    return registry_policy.resolve_security_fallback(department, domain)


def resolve_site_by_department(department, db_site=None):
    if db_site:
        return str(db_site)
    site = _department_site(department)
    if site:
        return site
    return registry_policy.resolve_site_fallback(department)


canonical_label = registry_policy.canonical_label


def _load_materials_from_db():
    try:
        repository = _repository()
        repository._ensure_engine()
        if repository.engine is None:
            return None
        materials = {}
        with repository.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT MaterialID, CanonicalCode, DisplayName, Category "
                    "FROM dbo.MaterialDictionary WHERE IsActive = 1"
                )
            ).fetchall()
            for material_id, code, display, category in rows:
                materials[material_id] = {
                    "code": code,
                    "display": display,
                    "category": category,
                    "synonyms": [],
                }
            if not materials:
                return None
            synonym_rows = connection.execute(
                text(
                    "SELECT MaterialID, Synonym "
                    "FROM dbo.MaterialSynonym WHERE IsActive = 1"
                )
            ).fetchall()
            for material_id, synonym in synonym_rows:
                if material_id in materials and synonym:
                    materials[material_id]["synonyms"].append(synonym)
        return list(materials.values())
    except Exception as exc:
        from mech_chatbot.config.logging import logger

        logger.warning(
            "material_registry: khong doc duoc tu DB, dung mac dinh. (%s)",
            type(exc).__name__,
        )
        return None


def _get_materials(force_refresh=False):
    now = time.time()
    if not force_refresh:
        with _material_cache_lock:
            cached = _material_cache["materials"]
            if cached is not None and now - _material_cache["ts"] < _CACHE_TTL:
                return cached
    materials = _load_materials_from_db() or registry_policy.default_materials()
    with _material_cache_lock:
        _material_cache["materials"] = materials
        _material_cache["ts"] = now
    return materials


def refresh_cache():
    _get_materials(force_refresh=True)


def get_known_materials():
    return registry_policy.known_materials(_get_materials())


def get_material_patterns():
    return registry_policy.material_patterns(_get_materials())


def normalize_material(raw):
    if not raw:
        return None
    return registry_policy.normalize_material(raw, _get_materials())


__all__ = [
    "canonical_label",
    "get_known_materials",
    "get_material_patterns",
    "normalize_material",
    "refresh_cache",
    "resolve_domain_by_department",
    "resolve_security_by_department",
    "resolve_site_by_department",
]
