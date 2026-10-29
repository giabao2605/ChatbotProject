"""Canonical source identifiers shared by serving and evaluation."""

import re


def extract_source_ids(value):
    """Extract canonical page and structured-locator identifiers."""
    text = str(value or "")
    source_ids = re.findall(
        r"source[_\s-]?id\s*[:#]?\s*(D\d+(?:P\d+|L[a-f0-9]{16}))(?![a-z0-9])",
        text, flags=re.IGNORECASE,
    )
    source_ids += re.findall(
        r"\[SRC:\s*(D\d+(?:P\d+|L[a-f0-9]{16}))(?![a-z0-9])\]",
        text, flags=re.IGNORECASE,
    )
    return {value.upper() for value in source_ids}
