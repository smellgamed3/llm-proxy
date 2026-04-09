"""Tests for OpenAI compatible extractor."""
from __future__ import annotations

import json
import pytest

from analyzer.extractors.openai_compat import OpenAICompatExtractor


@pytest.fixture
def extractor():
    return OpenAICompatExtractor()


class TestCanHandle:
    def test_chat_completions(self, extractor):
        assert extractor.can_handle("/v1/chat/completions", "POST", {})

    def test_completions(self, extractor):
        assert extractor.can_handle("/v1/completions", "POST", {})

    def test_embeddings(self, extractor):
        assert extractor.can_handle("/v1/embeddings", "POST", {})

    def test_other_path(self, extractor):
        assert not extractor.can_handle("/v1/models", "GET", {})


class TestChatCompletion:
    def test_basic_extraction(self, extractor):
        req = json.dumps({
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello!"},
            ],
            "temperature": 0.7,
            "max_tokens": 100,
        })
        resp = json.dumps({
            "id": "chatcmpl-1",
            "choices": [{
                "message": {"role": "assistant", "content": "Hi there!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        })
        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 200, "is_stream": 0},
            req, resp,
        )
        assert result.provider == "openai"
        assert result.model == "gpt-4o"
        assert result.request_type == "chat"
        assert result.system_prompt == "You are a helpful assistant."
        assert result.user_prompt == "Hello!"
        assert result.assistant_response == "Hi there!"
        assert result.finish_reason == "stop"
        assert result.prompt_tokens == 20
        assert result.completion_tokens == 5
        assert result.total_tokens == 25
        assert result.temperature == 0.7
        assert result.max_tokens == 100
        assert result.messages_count == 2
        assert result.status == "success"

    def test_tools_extracted(self, extractor):
        req = json.dumps({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "What's the weather?"}],
            "tools": [
                {"type": "function", "function": {"name": "get_weather", "description": "..."}},
            ],
        })
        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 200, "is_stream": 0},
            req, None,
        )
        assert result.has_tools is True
        assert result.tools_list == ["get_weather"]

    def test_error_response(self, extractor):
        req = json.dumps({"model": "gpt-4o", "messages": []})
        resp = json.dumps({"error": {"type": "rate_limit_exceeded", "message": "Too many requests"}})
        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 429, "is_stream": 0},
            req, resp,
        )
        assert result.status == "rate_limited"
        assert result.error_type == "rate_limit_exceeded"
        assert "Too many requests" in (result.error_message or "")

    def test_timeout_response_classified(self, extractor):
        req = json.dumps({"model": "gpt-4o", "messages": []})
        resp = json.dumps({"error": {"type": "request_timeout", "message": "Request timed out"}})
        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 504, "is_stream": 0},
            req, resp,
        )
        assert result.status == "timeout"


class TestStreamingResponse:
    def test_sse_extraction(self, extractor):
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": " world"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}},
        ]
        sse_body = "\n".join(f"data: {json.dumps(c)}" for c in chunks) + "\ndata: [DONE]\n"

        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 200, "is_stream": 1},
            json.dumps({"model": "gpt-4o", "messages": [], "stream": True}),
            sse_body,
        )
        assert result.assistant_response == "Hello world"
        assert result.finish_reason == "stop"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 2
        assert result.total_tokens == 12

    def test_sse_no_usage_chunk(self, extractor):
        """SSE response without usage chunk still extracts content."""
        chunks = [
            {"choices": [{"delta": {"content": "Hi"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        sse_body = "\n".join(f"data: {json.dumps(c)}" for c in chunks) + "\ndata: [DONE]\n"
        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 200, "is_stream": 1},
            json.dumps({"model": "gpt-4o", "messages": []}),
            sse_body,
        )
        assert result.assistant_response == "Hi"
        assert result.prompt_tokens is None

    def test_sse_detected_even_when_is_stream_flag_missing(self, extractor):
        chunks = [
            {"choices": [{"delta": {"content": "A"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "B"}, "finish_reason": "stop"}]},
        ]
        sse_body = "\n".join(f"data: {json.dumps(c)}" for c in chunks) + "\ndata: [DONE]\n"
        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 200, "is_stream": 0},
            json.dumps({"model": "gpt-4o", "messages": []}),
            sse_body,
        )
        assert result.assistant_response == "AB"
        assert result.finish_reason == "stop"

    def test_sse_reasoning_content_fallback(self, extractor):
        chunks = [
            {"choices": [{"delta": {"reasoning_content": "think "}, "finish_reason": None}]},
            {"choices": [{"delta": {"reasoning_content": "done"}, "finish_reason": "stop"}]},
        ]
        sse_body = "\n".join(f"data: {json.dumps(c)}" for c in chunks) + "\ndata: [DONE]\n"
        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 200, "is_stream": 1},
            json.dumps({"model": "zai/glm-5-turbo", "messages": []}),
            sse_body,
        )
        assert result.assistant_response == "think done"


class TestEmbedding:
    def test_embedding_extraction(self, extractor):
        req = json.dumps({"model": "text-embedding-3-small", "input": "Hello"})
        resp = json.dumps({
            "data": [{"embedding": [0.1, 0.2]}],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        })
        result = extractor.extract(
            {"path": "/v1/embeddings", "status_code": 200, "is_stream": 0},
            req, resp,
        )
        assert result.request_type == "embedding"
        assert result.model == "text-embedding-3-small"
        assert result.prompt_tokens == 5


class TestCompletion:
    def test_completion_extraction(self, extractor):
        req = json.dumps({"model": "gpt-3.5-turbo-instruct", "prompt": "Say hello"})
        resp = json.dumps({
            "choices": [{"text": "Hello!", "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        })
        result = extractor.extract(
            {"path": "/v1/completions", "status_code": 200, "is_stream": 0},
            req, resp,
        )
        assert result.request_type == "completion"
        assert result.user_prompt == "Say hello"
        assert result.assistant_response == "Hello!"


class TestToolsExtraction:
    """Tests for OpenAI tools/function_call response extraction."""

    def test_tool_calls_response_captured(self, extractor):
        req = json.dumps({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [
                {"type": "function", "function": {"name": "get_weather", "description": "Get weather", "parameters": {"type": "object"}}},
            ],
        })
        resp = json.dumps({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"NYC"}'}},
                    ],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        })
        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 200, "is_stream": 0},
            req, resp,
        )
        assert result.has_tools is True
        assert result.tools_list == ["get_weather"]
        assert result.finish_reason == "tool_calls"
        assert result.assistant_response is not None
        assert "get_weather" in result.assistant_response
        assert "NYC" in result.assistant_response

    def test_function_call_response_captured(self, extractor):
        req = json.dumps({
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Calculate 2+3"}],
            "functions": [
                {"name": "calculator", "description": "Do math", "parameters": {"type": "object"}},
            ],
        })
        resp = json.dumps({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "function_call": {"name": "calculator", "arguments": '{"a":2,"b":3}'},
                },
                "finish_reason": "function_call",
            }],
            "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
        })
        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 200, "is_stream": 0},
            req, resp,
        )
        assert result.has_tools is True
        assert result.tools_list == ["calculator"]
        assert "calculator" in result.assistant_response
        assert "function_call" in result.assistant_response

    def test_multiple_tool_calls(self, extractor):
        req = json.dumps({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Weather and time?"}],
            "tools": [
                {"type": "function", "function": {"name": "get_weather", "parameters": {}}},
                {"type": "function", "function": {"name": "get_time", "parameters": {}}},
            ],
        })
        resp = json.dumps({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}},
                        {"id": "call_2", "type": "function", "function": {"name": "get_time", "arguments": "{}"}},
                    ],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 60, "completion_tokens": 30, "total_tokens": 90},
        })
        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 200, "is_stream": 0},
            req, resp,
        )
        assert result.has_tools is True
        assert set(result.tools_list) == {"get_weather", "get_time"}
        assert "get_weather" in result.assistant_response
        assert "get_time" in result.assistant_response

    def test_text_content_preferred_over_tool_calls(self, extractor):
        """When assistant has both content and tool_calls, text content takes priority."""
        req = json.dumps({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [{"type": "function", "function": {"name": "noop", "parameters": {}}}],
        })
        resp = json.dumps({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Sure, let me help.",
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "noop", "arguments": "{}"}},
                    ],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        })
        result = extractor.extract(
            {"path": "/v1/chat/completions", "status_code": 200, "is_stream": 0},
            req, resp,
        )
        assert result.assistant_response == "Sure, let me help."
