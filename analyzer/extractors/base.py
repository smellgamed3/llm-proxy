from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ExtractionResult:
    provider: str | None = None
    model: str | None = None
    request_type: str | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    messages_count: int | None = None
    has_tools: bool = False
    tools_list: list[str] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    assistant_response: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    status: str = "success"
    error_type: str | None = None
    error_message: str | None = None


def classify_status(
    status_code: int | None,
    error_type: str | None = None,
    error_message: str | None = None,
    default: str = "error",
) -> str:
    """Normalize provider/http failures into documented status buckets."""
    text = " ".join(part for part in (error_type, error_message) if part).lower()
    if status_code in {408, 504} or "timeout" in text or "timed out" in text:
        return "timeout"
    if status_code == 429 or "rate_limit" in text or "rate limit" in text:
        return "rate_limited"
    if status_code is not None and status_code < 400:
        return "success"
    return default


class BaseExtractor(ABC):
    @abstractmethod
    def can_handle(self, path: str, method: str, request_headers: dict) -> bool: ...

    @abstractmethod
    def extract(
        self,
        raw_record: dict,
        request_body: str | None,
        response_body: str | None,
    ) -> ExtractionResult: ...
