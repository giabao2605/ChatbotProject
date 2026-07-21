"""Small transport helpers shared by browser API routers."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from fastapi import HTTPException, status

from mech_chatbot.auth.authorization import role_allows


def _role_checker():
    app_server = sys.modules.get("mech_chatbot.api.app_server")
    if app_server is not None and hasattr(app_server, "role_allows"):
        return getattr(app_server, "role_allows")
    return role_allows


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_json_obj(raw: str | None, field_name: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} không hợp lệ (JSON)",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail=f"{field_name} phải là object JSON")
    return {k: v for k, v in parsed.items() if v not in (None, "")} or None


def parse_json_list(raw: str | None, field_name: str) -> list[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} không hợp lệ (JSON)",
        ) from exc
    if not isinstance(parsed, list):
        raise HTTPException(status_code=400, detail=f"{field_name} phải là array JSON")
    return parsed


def split_csv(value: Any) -> list[str]:
    if isinstance(value, list):
        source = value
    elif isinstance(value, str):
        source = re.split(r"[\s,]+", value)
    else:
        source = []
    return [str(item).strip() for item in source if str(item).strip()]


def parse_json_or_csv_list(raw: str | None, field_name: str) -> list[str]:
    if not raw:
        return []
    stripped = raw.strip()
    if stripped.startswith("["):
        return split_csv(parse_json_list(stripped, field_name))
    return split_csv(stripped)


def row_to_json(row: Any) -> Any:
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    if isinstance(row, dict):
        return row
    if isinstance(row, (list, tuple)):
        return list(row)
    return row


def rows_to_json(rows: Any) -> list[Any]:
    return [row_to_json(row) for row in (rows or [])]


def assert_any_role(profile: dict[str, Any], *roles: str) -> None:
    if not _role_checker()(profile.get("roles"), *roles):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


__all__ = [
    "assert_any_role",
    "parse_json_list",
    "parse_json_obj",
    "parse_json_or_csv_list",
    "row_to_json",
    "rows_to_json",
    "safe_int",
    "split_csv",
]
