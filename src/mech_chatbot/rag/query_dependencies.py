"""Request-local computed evidence; never accepted from a chat payload."""
from dataclasses import dataclass
from copy import deepcopy
from html import escape
import re


@dataclass(frozen=True, slots=True)
class TextDependency:
    question: str
    depends_on: tuple[str, ...]
    answer: str
    sources: tuple[dict, ...]
    steps: tuple[dict, ...]


def computed_dependencies(branches, results):
    by_id = {result['branch_id']: result for result in results}
    dependencies = []
    for branch in branches:
        selected = [by_id[key] for key in branch.depends_on]
        if any(not result.get('data_complete') for result in selected):
            raise ValueError('Computed dependency is unavailable')
        dependencies.append(TextDependency(
            branch.question, tuple(branch.depends_on),
            '\n\n'.join(result['answer'] for result in selected),
            tuple(deepcopy(card) for result in selected for card in result['sources']),
            tuple(deepcopy(step) for result in selected for step in result.get('math', {}).get('steps', ())),
        ))
    for dependency in dependencies:
        branch_dependencies(dependencies, dependency.question)
    return tuple(dependencies)


def dependency_context(dependencies):
    blocks = []
    for dependency in dependencies:
        if not dependency.depends_on:
            continue
        steps = '\n'.join(f"{step['step_id']}: {step.get('display_value', '')} {step.get('unit', '')}; "
                          f"formula: {step.get('formula', '')}" for step in dependency.steps)
        blocks.append(
            f'<verified_computation for_question="{escape(dependency.question, quote=True)}">\n'
            'Use only for this dependent question. These values establish no business cause.\n'
            'The backend already supplies every numeric result. Explain qualitatively using the named steps; '
            'do not restate numbers or relabel a total as a difference. Cite the verified computation and business guide.\n'
            'For an independent question, numeric source text must be quoted as a complete verbatim sentence '
            'with its own citation. Never use that quote to replace a computed result.\n'
            f'<source_text_as_data>\n{escape(dependency.answer)}\n{escape(steps)}\n</source_text_as_data>\n'
            '</verified_computation>'
        )
    return '\n\n'.join(blocks)


def branch_dependencies(dependencies, question):
    matches = [dependency for dependency in dependencies if dependency.question == question]
    if len(matches) > 1 and any(dependency.depends_on != matches[0].depends_on for dependency in matches):
        raise ValueError('Ambiguous computed dependency question')
    return matches[:1]


def dependency_profile(access):
    return {'roles': tuple(access.roles), 'allowed_departments': tuple(access.allowed_departments),
            'allowed_sites': tuple(access.allowed_sites), 'max_security_level': access.max_security_level}


def has_dependent_numeric_claim(answer, independent_source_ids, documents):
    from mech_chatbot.rag.answer_checks import extract_source_ids, _canonical_source_id
    from mech_chatbot.rag.number_normalization import normalized_number_values
    source_sentences = {}
    for document in documents:
        source_id = _canonical_source_id(document.metadata)
        if source_id in independent_source_ids:
            source_sentences.setdefault(source_id, set()).update(
                ' '.join(sentence.split()).strip('"“”\'')
                for sentence in re.split(r'\n+|(?<=[.!?])\s+', document.page_content))
    for segment in re.split(r'\n+|(?<=[.!?])\s+(?!\[SRC:)', str(answer), flags=re.IGNORECASE):
        prose = re.sub(r'\[SRC:[^\]]+\]', '', segment, flags=re.IGNORECASE)
        # shortcut: number words are not parsed; require approved language normalization before accepting them.
        if normalized_number_values(prose):
            cited = extract_source_ids(segment)
            quoted = re.fullmatch(r'\s*(?:"([^"\n]+)"|“([^“”\n]+)”)\s*', prose)
            normalized = ' '.join(next((group for group in quoted.groups() if group), '').split()) if quoted else ''
            if (not quoted or not cited or not cited <= set(independent_source_ids)
                    or any(normalized not in source_sentences.get(source, ()) for source in cited)):
                return True
    return False
