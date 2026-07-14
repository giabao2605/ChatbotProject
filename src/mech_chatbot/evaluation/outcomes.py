"""Outcome labels and confusion metrics for answer/refusal evaluation."""

from __future__ import annotations

from collections import Counter
import re
import unicodedata


ANSWER_OUTCOMES = {"full_answer", "partial_answer", "clarification_required"}
REFUSAL_OUTCOMES = {"insufficient_evidence", "access_denied"}
VALID_OUTCOMES = ANSWER_OUTCOMES | REFUSAL_OUTCOMES


def expected_outcome(case: dict) -> str:
    explicit = case.get("expected_outcome")
    if explicit in VALID_OUTCOMES:
        return explicit
    return "insufficient_evidence" if case.get("should_refuse") else "full_answer"


def outcome_matches_expected(expected: str, actual: str) -> bool:
    """Require the evaluator's five-state outcome contract to match exactly."""
    return expected in VALID_OUTCOMES and actual == expected


def classify_outcome(expected: str, actual: str, *, answer_correct: bool, leaked: bool) -> str:
    if leaked:
        return "leakage"
    if expected in ANSWER_OUTCOMES and actual in REFUSAL_OUTCOMES:
        return "wrong_refusal"
    if expected in REFUSAL_OUTCOMES and actual in REFUSAL_OUTCOMES:
        return "correct_refusal" if expected == actual else "wrong_refusal_type"
    if expected in REFUSAL_OUTCOMES and actual in ANSWER_OUTCOMES:
        return "wrong_answer"
    if expected != actual:
        return "wrong_answer"
    return "correct_answer" if answer_correct else "wrong_answer"


def classify_actual_outcome(answer: str) -> str:
    normalized = str(answer or "").lower()
    folded = "".join(
        char for char in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.combining(char)
    ).replace("đ", "d")
    if any(marker in folded for marker in [
        "chua du quyen", "chinh sach truy cap", "protected by access control", "access request"
    ]):
        return "access_denied"
    if any(marker in folded for marker in ["vui long chi dinh", "ban muon so sanh", "which version"]):
        return "clarification_required"
    if any(marker in folded for marker in ["tra loi duoc mot phan", "phan con lai", "partial answer"]):
        return "partial_answer"
    no_approved_bom_total = re.search(
        r"khong co tong (?:so luong )?bom duoc phe duyet", folded,
    )
    derived_total_claim = re.search(
        r"\b(?:tong(?: so luong)?|cong lai|ket qua (?:tong|cong))\b[^;\n|.]{0,100}"
        r"(?:la|duoc|=)\s*[-+]?\d",
        folded,
    ) or re.search(r"\b\d+(?:[.,]\d+)?\s*[+*/-]\s*\d+(?:[.,]\d+)?\s*=\s*\d", folded)
    total_table_row = re.search(
        r"\|\s*(?:tong|tong cong|sum|total|grand total)\b[^|]*\|\s*[-+]?\d",
        folded,
    )
    if derived_total_claim or total_table_row:
        return "full_answer"
    if no_approved_bom_total:
        return "insufficient_evidence"
    if any(marker in folded for marker in [
        "khong ghi thong tin", "tai lieu hien tai khong", "khong cong bo",
        "khong du", "thieu du kien", "khong tu uoc luong", "khong the ho tro yeu cau nay",
    ]):
        return "insufficient_evidence"
    if re.fullmatch(
        r"tai lieu .* khong co tong (?:so luong )?bom duoc phe duyet trong tai lieu nay\."
        r"\s*(?:\[nguon:[^\]]+\])?",
        folded.strip(),
    ) or re.fullmatch(
        r"khong co tong bom duoc phe duyet trong tai lieu nay\.\s*"
        r"(?![\s\S]*\|\s*(?:tong|sum|total|grand)\b[^|]*\|)"
        r"\| ma \| so luong \|\s*\|[-:| ]+\|\s*"
        r"(?:\|[^\n]+\|\s*)+\[nguon:[^\]]+\]",
        folded.strip(),
    ) or re.fullmatch(
        r"rat tiec,\s*(?:minh|toi) khong tim thay ma so .+ nao trong he thong ban ve hien tai\."
        r"\s*vui long kiem tra lai ma hoac mo ta ro hon\.",
        folded.strip(),
    ):
        return "insufficient_evidence"
    return "full_answer"


def summarize_outcomes(rows: list[dict]) -> dict[str, int]:
    counts = Counter(
        classify_outcome(
            row["expected"],
            row["actual"],
            answer_correct=bool(row.get("answer_correct")),
            leaked=bool(row.get("leaked")),
        )
        for row in rows
    )
    counts["legacy_admin_exception"] = sum(
        1 for row in rows if row.get("legacy_admin_bypass")
    )
    return dict(sorted(counts.items()))
