from __future__ import annotations

import logging
from typing import AsyncIterator

logger = logging.getLogger("llm-proxy.sse")


async def stream_and_record(
    raw_stream: AsyncIterator[bytes],
) -> AsyncIterator[tuple[bytes, str]]:
    """遍历 httpx 流式响应，yield (原始chunk, 累积文本)。

    每次迭代产出原始 chunk（用于转发给客户端）和累积的完整 SSE 文本（流结束后用于记录）。
    """
    # 使用列表收集 chunk 文本，仅在最后一次性拼接，避免每个 chunk 都 O(n) 拷贝
    # 参考：https://docs.python.org/3/library/stdtypes.html#str.join
    accumulated_parts: list[str] = []
    # 维护字节长度计数器，避免每次 yield 时的 O(n) len() 计算
    _total_bytes = 0

    async for chunk in raw_stream:
        text = chunk.decode("utf-8", errors="replace")
        accumulated_parts.append(text)
        _total_bytes += len(chunk)
        yield chunk, text  # 只 yield 当前 chunk，不拼接全部


def is_sse_response(content_type: str | None) -> bool:
    """Check if the response content-type indicates SSE."""
    if not content_type:
        return False
    return "text/event-stream" in content_type
