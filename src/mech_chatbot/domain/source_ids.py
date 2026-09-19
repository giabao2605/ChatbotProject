"""Canonical source identifiers shared by serving and evaluation."""

import re

def extract_source_ids(value):
    """Extract canonical source identifiers emitted by the RAG prompt."""
    text = str(value or "")
    matches = re.findall(
        r"(?:source[_\s-]?id\s*[:#]?\s*|\[src:\s*)(D\d+P\d+)",
        text,
        flags=re.IGNORECASE,
    )
    return {match.upper() for match in matches}


