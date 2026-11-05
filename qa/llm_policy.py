"""Request-local server policy: never store credentials in Streamlit session state."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class LLMPolicy:
    api_key: str = field(repr=False)
    model: str
    max_output_tokens: int
    authorize: Callable = field(repr=False)


policy: ContextVar[LLMPolicy | None] = ContextVar('llm_policy', default=None)
