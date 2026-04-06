"""Tests for Anthropic Messages API extractor."""
from __future__ import annotations

import json

import pytest

from analyzer.extractors.anthropic import AnthropicExtractor


@pytest.fixture
def extractor():
    return AnthropicExtractor()


class TestCanHandle:
    def test_messages_path(self, extractor):
        assert extractor.can_handle("/v1/messages", "POST", {})

    def test_message_path(self, extractor):
        assert extractor.can_handle("/v1/message", "POST", {})

    def test_messages_subpath(self, extractor):
        assert extractor.can_handle("/v1/messages/count_tokens", "POST", {})

    def test_other_path(self, extractor):
        assert not extractor.can_handle("/v1/chat/completions", "POST", {})

    def test_models_path(self, extractor):
        assert not extractor.can_handle("/v1/models", "GET", {})


class TestSuccessExtraction:
    def test_message_path_extracts_chat_request_type(self, extractor):
        req = json.dumps({
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "Hello from new-api"},
            ],
        })
        resp = json.dumps({
            "content": [{"type": "text", "text": "Hi there"}],
            "usage": {"input_tokens": 11, "output_tokens": 3},
        })
        result = extractor.extract({"status_code": 200, "path": "/v1/message"}, req, resp)
        assert result.provider == "anthropic"
        assert result.request_type == "chat"
        assert result.model == "claude-sonnet-4-6"
        assert result.user_prompt == "Hello from new-api"

    def test_count_tokens_path_extracts_tokens_request_type(self, extractor):
        req = json.dumps({
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "Count my tokens"},
            ],
        })
        resp = json.dumps({
            "usage": {"input_tokens": 42},
        })
        result = extractor.extract({"status_code": 200, "path": "/v1/messages/count_tokens"}, req, resp)
        assert result.request_type == "tokens"
        assert result.model == "claude-sonnet-4-6"
        assert result.user_prompt == "Count my tokens"

    def test_basic_chat(self, extractor):
        req = json.dumps({
            "model": "claude-opus-4-5",
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": "Hello, how are you?"},
            ],
        })
        resp = json.dumps({
            "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "I'm doing well, thank you!"}],
            "model": "claude-opus-4-5",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 10},
        })
        result = extractor.extract(
            {"status_code": 200},
            req,
            resp,
        )
        assert result.provider == "anthropic"
        assert result.request_type == "chat"
        assert result.model == "claude-opus-4-5"
        assert result.max_tokens == 1024
        assert result.messages_count == 1
        assert result.user_prompt == "Hello, how are you?"
        assert result.assistant_response == "I'm doing well, thank you!"
        assert result.finish_reason == "end_turn"
        assert result.prompt_tokens == 12
        assert result.completion_tokens == 10
        assert result.total_tokens == 22
        assert result.status == "success"

    def test_system_prompt_extracted(self, extractor):
        req = json.dumps({
            "model": "claude-opus-4-5",
            "max_tokens": 512,
            "system": "You are a helpful assistant.",
            "messages": [
                {"role": "user", "content": "What's 2+2?"},
            ],
        })
        resp = json.dumps({
            "content": [{"type": "text", "text": "4"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 20, "output_tokens": 2},
        })
        result = extractor.extract({"status_code": 200}, req, resp)
        assert result.system_prompt == "You are a helpful assistant."
        assert result.user_prompt == "What's 2+2?"

    def test_last_user_message_extracted(self, extractor):
        """When there are multiple user messages, extracts the last one."""
        req = json.dumps({
            "model": "claude-opus-4-5",
            "max_tokens": 256,
            "messages": [
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First answer"},
                {"role": "user", "content": "Follow-up question"},
            ],
        })
        resp = json.dumps({
            "content": [{"type": "text", "text": "Follow-up answer"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 30, "output_tokens": 5},
        })
        result = extractor.extract({"status_code": 200}, req, resp)
        assert result.user_prompt == "Follow-up question"
        assert result.messages_count == 3

    def test_content_block_list_user_message(self, extractor):
        """User messages with content as a list of blocks are joined."""
        req = json.dumps({
            "model": "claude-opus-4-5",
            "max_tokens": 256,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Look at this image:"},
                        {"type": "image", "source": {"type": "url", "url": "https://example.com/img.png"}},
                        {"type": "text", "text": "What do you see?"},
                    ],
                },
            ],
        })
        resp = json.dumps({
            "content": [{"type": "text", "text": "I see a diagram."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 50, "output_tokens": 6},
        })
        result = extractor.extract({"status_code": 200}, req, resp)
        assert "Look at this image:" in result.user_prompt
        assert "What do you see?" in result.user_prompt

    def test_multiple_text_blocks_in_response(self, extractor):
        """Multiple text content blocks in response are concatenated."""
        req = json.dumps({
            "model": "claude-opus-4-5",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": "Write two paragraphs."}],
        })
        resp = json.dumps({
            "content": [
                {"type": "text", "text": "First paragraph."},
                {"type": "text", "text": "Second paragraph."},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 8},
        })
        result = extractor.extract({"status_code": 200}, req, resp)
        assert result.assistant_response == "First paragraph.Second paragraph."

    def test_no_response_body(self, extractor):
        """Handles missing response body gracefully."""
        req = json.dumps({
            "model": "claude-opus-4-5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hi"}],
        })
        result = extractor.extract({"status_code": 200}, req, None)
        assert result.status == "success"
        assert result.prompt_tokens is None
        assert result.completion_tokens is None


class TestErrorExtraction:
    def test_count_tokens_404_marked_unsupported(self, extractor):
        req = json.dumps({
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Count my tokens"}],
        })
        resp = json.dumps({
            "error": {"type": "not_found_error", "message": "No such route"},
        })
        result = extractor.extract({"status_code": 404, "path": "/v1/messages/count_tokens"}, req, resp)
        assert result.status == "unsupported"
        assert result.error_type == "not_found_error"

    def test_count_tokens_invalid_request_404_marked_unsupported(self, extractor):
        req = json.dumps({
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Count my tokens"}],
        })
        resp = json.dumps({
            "error": {"type": "invalid_request_error", "message": "Invalid URL (POST /v1/messages/count_tokens)"},
        })
        result = extractor.extract({"status_code": 404, "path": "/v1/messages/count_tokens"}, req, resp)
        assert result.status == "unsupported"
        assert result.error_type == "invalid_request_error"

    def test_nested_error_type_extracted(self, extractor):
        req = json.dumps({
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
        })
        resp = json.dumps({
            "error": {"type": "not_found_error", "message": "No such route"},
        })
        result = extractor.extract({"status_code": 404, "path": "/v1/message"}, req, resp)
        assert result.error_type == "not_found_error"
        assert result.error_message == "No such route"
        assert result.model == "claude-sonnet-4-6"

    def test_rate_limit_error(self, extractor):
        req = json.dumps({
            "model": "claude-opus-4-5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hi"}],
        })
        resp = json.dumps({
            "type": "error",
            "error": {"type": "rate_limit_error", "message": "Rate limit exceeded"},
        })
        result = extractor.extract({"status_code": 429}, req, resp)
        assert result.status == "rate_limited"
        assert result.error_type == "rate_limit_error"
        assert "Rate limit exceeded" in (result.error_message or "")

    def test_timeout_error(self, extractor):
        req = json.dumps({
            "model": "claude-opus-4-5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hi"}],
        })
        resp = json.dumps({
            "type": "error",
            "error": {"type": "request_timeout", "message": "Request timed out"},
        })
        result = extractor.extract({"status_code": 504}, req, resp)
        assert result.status == "timeout"

    def test_authentication_error(self, extractor):
        req = json.dumps({
            "model": "claude-opus-4-5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hi"}],
        })
        resp = json.dumps({
            "type": "error",
            "error": {"type": "authentication_error", "message": "Invalid API key"},
        })
        result = extractor.extract({"status_code": 401}, req, resp)
        assert result.status == "error"
        assert result.error_message == "Invalid API key"

    def test_invalid_response_json(self, extractor):
        """Invalid JSON in response body does not raise; returns partial result."""
        req = json.dumps({
            "model": "claude-opus-4-5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hi"}],
        })
        result = extractor.extract({"status_code": 200}, req, "not-json")
        assert result.status == "success"
        assert result.prompt_tokens is None

    def test_invalid_request_json(self, extractor):
        """Invalid JSON in request body extracts what it can."""
        result = extractor.extract({"status_code": 200}, "not-json", None)
        assert result.model is None
        assert result.messages_count == 0
