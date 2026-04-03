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
        assert result.status == "error"
        assert result.error_type == "rate_limit_exceeded"
        assert "Too many requests" in (result.error_message or "")


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
