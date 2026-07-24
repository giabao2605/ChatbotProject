"""Compatibility interface for domain-owned number normalization."""

from mech_chatbot.domain.number_normalization import (
    NUMBER_PATTERN,
    normalize_number_token,
    normalize_numbers_in_text,
    normalized_number_values,
)


__all__ = [
    "NUMBER_PATTERN",
    "normalize_number_token",
    "normalize_numbers_in_text",
    "normalized_number_values",
]
