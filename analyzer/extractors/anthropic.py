from __future__ import annotations

import json
import logging

from .base import BaseExtractor, ExtractionResult, classify_status
from .utils import content_blocks_to_text

logger = logging.getLogger("analyzer.extractors.anthropic")


class AnthropicExtractor(BaseExtractor):
    """Handles Anthropic Messages API (/v1/messages)."""

    def can_handle(self, path: str, method: str, request_headers: dict) -> bool:
        return path == "/v1/messages"

    def extract(
        self,
        raw_record: dict,
        request_body: str | None,
        response_body: str | None,
    ) -> ExtractionResult:
        result = ExtractionResult(provider="anthropic", request_type="chat")

        req_data: dict = {}
        if request_body:
            try:
                req_data = json.loads(request_body)
            except json.JSONDecodeError:
                pass

        result.model = req_data.get("model")
        result.max_tokens = req_data.get("max_tokens")

        messages = req_data.get("messages", [])
        result.messages_count = len(messages)
        result.system_prompt = req_data.get("system")
        for msg in reversed(messages):
            if msg.get("role") == "user":
                result.user_prompt = content_blocks_to_text(msg.get("content"))
                break

        status_code = raw_record.get("status_code")
        if status_code and status_code >= 400:
            if response_body:
                try:
                    err_data = json.loads(response_body)
                    result.error_type = err_data.get("type")
                    result.error_message = err_data.get("error", {}).get("message")
                except json.JSONDecodeError:
                    result.error_message = response_body[:500]
            result.status = classify_status(status_code, result.error_type, result.error_message)
            return result

        result.status = "success"

        if response_body:
            try:
                data = json.loads(response_body)
            except json.JSONDecodeError:
                return result

            usage = data.get("usage", {})
            result.prompt_tokens = usage.get("input_tokens")
            result.completion_tokens = usage.get("output_tokens")
            if result.prompt_tokens and result.completion_tokens:
                result.total_tokens = result.prompt_tokens + result.completion_tokens

            result.finish_reason = data.get("stop_reason")

            result.assistant_response = content_blocks_to_text(data.get("content"), separator="")

        return result
