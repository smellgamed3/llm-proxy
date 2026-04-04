from __future__ import annotations

import json


def parse_sse_chunks(text: str) -> list[dict]:
    chunks = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            continue
        try:
            chunks.append(json.loads(payload))
        except json.JSONDecodeError:
            pass
    return chunks


def looks_like_sse_payload(text: str) -> bool:
    return text.lstrip().startswith("data:")


def to_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        joined = "".join(parts).strip()
        return joined or None
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        if isinstance(text, str):
            return text
    return None


def content_blocks_to_text(content: object, separator: str = " ") -> str | None:
    if isinstance(content, list):
        text_parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        joined = separator.join(part for part in text_parts if part).strip()
        return joined or None
    return to_text(content)


def extract_last_role_text(messages: list[dict], role: str) -> str | None:
    for message in reversed(messages):
        if message.get("role") == role:
            return content_blocks_to_text(message.get("content"))
    return None
