"""Locale-tolerant number normalization shared by RAG checks and evaluation."""

from __future__ import annotations

import re


NUMBER_PATTERN = re.compile(r"(?<![\w.])\d+(?:[\.,]\d+)*(?!\w)")


def normalize_number_token(raw) -> str:
    value = str(raw).strip()
    separators = [char for char in value if char in ".,"]
    if not separators:
        return str(int(value)) if value.isdigit() else value
    if len(set(separators)) == 2:
        decimal_separator = "." if value.rfind(".") > value.rfind(",") else ","
        grouping_separator = "," if decimal_separator == "." else "."
        value = value.replace(grouping_separator, "").replace(decimal_separator, ".")
    else:
        separator = separators[0]
        parts = value.split(separator)
        if len(parts) > 2 or (len(parts[-1]) == 3 and parts[0] != "0"):
            value = "".join(parts)
        else:
            value = ".".join(parts)
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value.lstrip("0") or "0"


def normalized_number_values(text) -> set[str]:
    return {
        normalize_number_token(match.group(0))
        for match in NUMBER_PATTERN.finditer(str(text or ""))
    }


def normalize_numbers_in_text(text) -> str:
    return NUMBER_PATTERN.sub(
        lambda match: normalize_number_token(match.group(0)), str(text or "")
    )
