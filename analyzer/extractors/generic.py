from __future__ import annotations

from .base import BaseExtractor, ExtractionResult, classify_status


class GenericExtractor(BaseExtractor):
    """Fallback extractor that only extracts HTTP-level info."""

    def can_handle(self, path: str, method: str, request_headers: dict) -> bool:
        return True

    def extract(
        self,
        raw_record: dict,
        request_body: str | None,
        response_body: str | None,
    ) -> ExtractionResult:
        result = ExtractionResult()
        status_code = raw_record.get("status_code")
        error_message = raw_record.get("error")
        if status_code is not None and status_code >= 400:
            result.error_type = f"http_{status_code}"
        result.error_message = error_message
        result.status = classify_status(status_code, result.error_type, error_message)
        return result
