from __future__ import annotations

import json
import logging

from .base import BaseExtractor, ExtractionResult, classify_status
from .utils import extract_last_role_text, looks_like_sse_payload, parse_sse_chunks, to_text

logger = logging.getLogger("analyzer.extractors.openai_compat")

OPENAI_PATHS = {
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/embeddings",
}

def _extract_text_from_messages(messages: list[dict]) -> tuple[str | None, str | None]:
    """Extract system prompt and last user message from messages list."""
    system_prompt: str | None = None
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Handle content blocks (vision etc.)
            text_parts = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            content = " ".join(text_parts)
        if role == "system":
            system_prompt = content
    return system_prompt, extract_last_role_text(messages, "user")


class OpenAICompatExtractor(BaseExtractor):
    """Handles /v1/chat/completions, /v1/completions, /v1/embeddings."""

    def can_handle(self, path: str, method: str, request_headers: dict) -> bool:
        return path in OPENAI_PATHS

    def extract(
        self,
        raw_record: dict,
        request_body: str | None,
        response_body: str | None,
    ) -> ExtractionResult:
        result = ExtractionResult(provider="openai")
        path = raw_record.get("path", "")

        # Parse request
        req_data: dict = {}
        if request_body:
            try:
                req_data = json.loads(request_body)
            except json.JSONDecodeError:
                pass

        result.model = req_data.get("model")
        result.temperature = req_data.get("temperature")
        result.max_tokens = req_data.get("max_tokens")

        if path == "/v1/chat/completions":
            result.request_type = "chat"
            messages = req_data.get("messages", [])
            result.messages_count = len(messages)
            result.system_prompt, result.user_prompt = _extract_text_from_messages(messages)
            tools = req_data.get("tools") or req_data.get("functions")
            if tools:
                result.has_tools = True
                result.tools_list = [
                    t.get("function", {}).get("name") or t.get("name", "")
                    for t in tools
                    if isinstance(t, dict)
                ]
        elif path == "/v1/completions":
            result.request_type = "completion"
            prompt = req_data.get("prompt", "")
            result.user_prompt = prompt if isinstance(prompt, str) else None
        elif path == "/v1/embeddings":
            result.request_type = "embedding"

        # Parse response
        status_code = raw_record.get("status_code")
        is_stream = bool(raw_record.get("is_stream"))

        if status_code and status_code >= 400:
            if response_body:
                try:
                    err_data = json.loads(response_body)
                    err = err_data.get("error", {})
                    result.error_type = err.get("type") if isinstance(err, dict) else str(err)
                    result.error_message = err.get("message") if isinstance(err, dict) else None
                except json.JSONDecodeError:
                    result.error_message = response_body[:500]
            result.status = classify_status(status_code, result.error_type, result.error_message)
            return result

        result.status = "success"

        if response_body:
            if is_stream or looks_like_sse_payload(response_body):
                self._parse_stream_response(result, response_body, path)
            else:
                self._parse_sync_response(result, response_body, path)

        return result

    def _parse_sync_response(self, result: ExtractionResult, body: str, path: str) -> None:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return

        usage = data.get("usage", {})
        if usage:
            result.prompt_tokens = usage.get("prompt_tokens")
            result.completion_tokens = usage.get("completion_tokens")
            result.total_tokens = usage.get("total_tokens")

        choices = data.get("choices", [])
        if choices and path in ("/v1/chat/completions", "/v1/completions"):
            choice = choices[0]
            result.finish_reason = choice.get("finish_reason")
            msg = choice.get("message") or {}
            if isinstance(msg, dict):
                result.assistant_response = to_text(msg.get("content"))
                # Capture tool_calls / function_call when no text content
                if not result.assistant_response:
                    tool_calls = msg.get("tool_calls")
                    function_call = msg.get("function_call")
                    if isinstance(tool_calls, list) and tool_calls:
                        parts = []
                        for tc in tool_calls:
                            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                            name = fn.get("name", "unknown") if isinstance(fn, dict) else "unknown"
                            args = fn.get("arguments", "") if isinstance(fn, dict) else ""
                            parts.append(f"[tool_call] {name}({args})")
                        result.assistant_response = "\n".join(parts)
                    elif isinstance(function_call, dict):
                        name = function_call.get("name", "unknown")
                        args = function_call.get("arguments", "")
                        result.assistant_response = f"[function_call] {name}({args})"
            if not result.assistant_response:
                result.assistant_response = to_text(choice.get("text"))

    def _parse_stream_response(self, result: ExtractionResult, body: str, path: str) -> None:
        chunks = parse_sse_chunks(body)
        if not chunks:
            return

        # Accumulate assistant response from deltas
        content_parts: list[str] = []
        last_usage: dict = {}

        for chunk in chunks:
            usage = chunk.get("usage")
            if usage:
                last_usage = usage

            choices = chunk.get("choices", [])
            if choices:
                choice = choices[0]
                result.finish_reason = choice.get("finish_reason") or result.finish_reason
                delta = choice.get("delta", {})
                content = to_text(delta.get("content"))
                if content:
                    content_parts.append(content)
                reasoning = to_text(delta.get("reasoning_content") or delta.get("reasoning"))
                if reasoning:
                    content_parts.append(reasoning)

        if content_parts:
            result.assistant_response = "".join(content_parts)

        if last_usage:
            result.prompt_tokens = last_usage.get("prompt_tokens")
            result.completion_tokens = last_usage.get("completion_tokens")
            result.total_tokens = last_usage.get("total_tokens")
