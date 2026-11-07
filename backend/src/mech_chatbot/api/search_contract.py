"""Search fields shared by the browser and service chat boundaries."""

from collections.abc import Mapping

from pydantic import BaseModel, Field, model_validator

from mech_chatbot.rag.search_mode import SearchMode, resolve_search_mode, validate_continuation


class SearchRequest(BaseModel):
    search_mode: SearchMode = "quick"
    continuation_cursor: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="before")
    @classmethod
    def resolve_mode(cls, values):
        if not isinstance(values, Mapping):
            return values
        question = values.get("user_question", values.get("question", ""))
        if not isinstance(question, str):
            return values
        requested = values.get("search_mode", "quick")
        typed_action = any(values.get(key) is not None for key in (
            'scope_total', 'scope_comparison', 'graph_query', 'bom_rollup', 'query_plan'
        )) or bool(values.get('text_query'))
        mode = resolve_search_mode(requested, '' if typed_action and requested == 'quick' else question)
        if mode == 'comprehensive' and (typed_action or values.get('image_path') or values.get('image_token')):
            raise ValueError('Comprehensive search requires a standalone text request')
        validate_continuation(mode, values.get("continuation_cursor"))
        if "search_mode" not in values and mode == "quick":
            return values  # keep the default out of model_fields_set; signed pilot replays hash only sent fields
        return {**values, "search_mode": mode}
