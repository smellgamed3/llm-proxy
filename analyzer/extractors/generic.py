from __future__ import annotations

from .base import BaseExtractor, ExtractionResult


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
        if status_code is not None:
            if status_code >= 500:
                result.status = "error"
                result.error_type = f"http_{status_code}"
            elif status_code >= 400:
                result.status = "client_error"
                result.error_type = f"http_{status_code}"
            else:
                result.status = "success"
        return result
