from __future__ import annotations

import logging
from typing import AsyncIterator

logger = logging.getLogger("llm-proxy.sse")


async def stream_and_record(
    raw_stream: AsyncIterator[bytes],
) -> AsyncIterator[tuple[bytes, str]]:
    """Iterate over an httpx streaming response, yielding (chunk, accumulated_text).

    Each iteration yields the raw chunk (for forwarding) and the accumulated
    full SSE text so far (for recording after stream ends).
    """
    accumulated = []

    async for chunk in raw_stream:
        text = chunk.decode("utf-8", errors="replace")
        accumulated.append(text)
        yield chunk, "".join(accumulated)


def is_sse_response(content_type: str | None) -> bool:
    """Check if the response content-type indicates SSE."""
    if not content_type:
        return False
    return "text/event-stream" in content_type
