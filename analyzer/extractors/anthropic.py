from __future__ import annotations

import json
import logging

from .base import BaseExtractor, ExtractionResult, classify_status
from .utils import content_blocks_to_text

logger = logging.getLogger("analyzer.extractors.anthropic")


ANTHROPIC_PATHS = {
    "/v1/message",
    "/v1/messages",
}


def _classify_anthropic_status(request_type: str, status_code: int | None, error_type: str | None, error_message: str | None) -> str:
    if request_type == "tokens" and status_code == 404:
        return "unsupported"
    return classify_status(status_code, error_type, error_message)


class AnthropicExtractor(BaseExtractor):
    """Handles Anthropic/new-api message endpoints."""

    def can_handle(self, path: str, method: str, request_headers: dict) -> bool:
        return path in ANTHROPIC_PATHS or path.startswith("/v1/messages/")

    def extract(
        self,
        raw_record: dict,
        request_body: str | None,
        response_body: str | None,
    ) -> ExtractionResult:
        path = raw_record.get("path") or ""
        request_type = "tokens" if path.endswith("/count_tokens") else "chat"
        result = ExtractionResult(provider="anthropic", request_type=request_type)

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
        result.system_prompt = content_blocks_to_text(req_data.get("system"))
        for msg in reversed(messages):
            if msg.get("role") == "user":
                result.user_prompt = content_blocks_to_text(msg.get("content"))
                break

        # Extract tools
        tools = req_data.get("tools")
        if tools and isinstance(tools, list):
            result.has_tools = True
            result.tools_list = [
                t.get("name", "") for t in tools if isinstance(t, dict)
            ]

        status_code = raw_record.get("status_code")
        if status_code and status_code >= 400:
            if response_body:
                try:
                    err_data = json.loads(response_body)
                    error_obj = err_data.get("error") if isinstance(err_data.get("error"), dict) else {}
                    result.error_type = error_obj.get("type") or err_data.get("type")
                    result.error_message = error_obj.get("message") or err_data.get("message")
                except json.JSONDecodeError:
                    result.error_message = response_body[:500]
            result.status = _classify_anthropic_status(request_type, status_code, result.error_type, result.error_message)
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

            content = data.get("content")
            text_response = content_blocks_to_text(content, separator="")
            if text_response:
                result.assistant_response = text_response
            elif isinstance(content, list):
                # Summarise tool_use blocks when no text content present
                tool_uses = [
                    b for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                ]
                if tool_uses:
                    parts = []
                    for tu in tool_uses:
                        name = tu.get("name", "unknown")
                        inp = tu.get("input")
                        if inp:
                            import json as _json
                            try:
                                inp_str = _json.dumps(inp, ensure_ascii=False)
                            except Exception:
                                inp_str = str(inp)
                            parts.append(f"[tool_use] {name}({inp_str})")
                        else:
                            parts.append(f"[tool_use] {name}()")
                    result.assistant_response = "\n".join(parts)

        return result
