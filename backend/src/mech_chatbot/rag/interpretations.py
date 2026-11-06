"""Render verified interpretations without inferring branch identity from shared sources."""
from collections import Counter
from dataclasses import replace

from mech_chatbot.rag.evidence_gate import make_insufficient_evidence_message


def render_verified_interpretations(draft, checked, branches, *, question="", lang="vi"):
    supported = [branch for branch in branches if branch.get("outcome") == "full_answer"]
    headings = {f"### {branch['subquery']}": index for index, branch in enumerate(supported)}
    grouped = [[] for _ in supported]
    occurrences = Counter(line.strip() for line in draft.splitlines() if line.strip())
    verified = Counter(claim.text.strip() for claim in checked.claims)
    accepted = {claim.text.strip(): claim for claim in checked.claims
                if verified[claim.text.strip()] == occurrences[claim.text.strip()]}
    current = None
    for line in draft.splitlines():
        text = line.strip()
        if text.startswith("#"):
            current = headings.get(text)
            continue
        claim = accepted.get(text)
        if current is None or claim is None:
            continue
        sources = {str(card.get("source_id") or "").upper()
                   for card in supported[current].get("citations", ())}
        if claim.sources and set(claim.sources).issubset(sources) and claim not in grouped[current]:
            grouped[current].append(claim)
    retained = tuple(claim for claims in grouped for claim in claims)
    sections = [f"### {branch['subquery']}\n" + "\n".join(claim.text.strip() for claim in claims)
                for branch, claims in zip(supported, grouped) if claims]
    source_ids = {source for claim in retained for source in claim.sources}
    answer = "\n\n".join(sections) if sections else make_insufficient_evidence_message(
        question, "no_verified_interpretation", lang=lang)
    return replace(checked, answer=answer, claims=retained,
                   status=(checked.status if retained else "insufficient"),
                   source_groups=tuple(group for group in checked.source_groups if group.source_id in source_ids))
