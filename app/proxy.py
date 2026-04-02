from __future__ import annotations

import logging
import time
from urllib.parse import urlencode, parse_qsl

import httpx
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from .config import Config
from .recorder import Recorder
from .sse import is_sse_response, stream_and_record

logger = logging.getLogger("llm-proxy.proxy")

# Headers that should not be forwarded
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
})


def _filter_headers(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    """Convert header list to dict, removing hop-by-hop headers."""
    return {
        k.decode("latin-1"): v.decode("latin-1")
        for k, v in headers
        if k.decode("latin-1").lower() not in HOP_BY_HOP
    }


class ProxyHandler:
    """Core reverse proxy logic."""

    def __init__(self, config: Config, recorder: Recorder):
        self.config = config
        self.recorder = recorder
        self.client = httpx.AsyncClient(
            base_url=config.upstream_url,
            timeout=httpx.Timeout(connect=10, read=300, write=30, pool=10),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            follow_redirects=False,
            http2=True,
        )

    async def close(self):
        await self.client.aclose()

    async def handle(self, request: Request) -> Response:
        start = time.monotonic()
        request_id = self.recorder.new_request_id()

        path = request.url.path
        query_string = request.url.query

        # Build upstream URL
        upstream_path = path
        if query_string:
            upstream_path = f"{path}?{query_string}"

        # Read request body
        body = await request.body()

        # Forward headers
        fwd_headers = _filter_headers(request.headers.raw)
        # Set correct Host for upstream
        fwd_headers.pop("host", None)

        # Check if we should record this request
        should_record = self.config.recording_filter.should_record(path)

        if should_record:
            self.recorder.record_request(
                request_id=request_id,
                method=request.method,
                path=path,
                query_string=query_string or "",
                headers=fwd_headers,
                body=body if body else None,
            )

        try:
            upstream_req = self.client.build_request(
                method=request.method,
                url=upstream_path,
                headers=fwd_headers,
                content=body if body else None,
            )
            upstream_resp = await self.client.send(upstream_req, stream=True)

        except httpx.ConnectError as e:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("Upstream connect error: %s", e)
            if should_record:
                self.recorder.record_response(
                    request_id=request_id,
                    status_code=502,
                    headers={},
                    body=str(e),
                    is_stream=False,
                    duration_ms=duration_ms,
                    error=str(e),
                )
            return Response(
                content=f'{{"error": "upstream connect error: {e}"}}',
                status_code=502,
                media_type="application/json",
            )
        except httpx.TimeoutException as e:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("Upstream timeout: %s", e)
            if should_record:
                self.recorder.record_response(
                    request_id=request_id,
                    status_code=504,
                    headers={},
                    body=str(e),
                    is_stream=False,
                    duration_ms=duration_ms,
                    error=str(e),
                )
            return Response(
                content=f'{{"error": "upstream timeout: {e}"}}',
                status_code=504,
                media_type="application/json",
            )

        # Build response headers (filter hop-by-hop)
        resp_headers = {
            k: v for k, v in upstream_resp.headers.items()
            if k.lower() not in HOP_BY_HOP
        }

        content_type = upstream_resp.headers.get("content-type", "")
        is_stream = is_sse_response(content_type)

        if is_stream:
            return await self._stream_response(
                request_id, upstream_resp, resp_headers, start, should_record,
            )
        else:
            return await self._buffered_response(
                request_id, upstream_resp, resp_headers, start, should_record,
            )

    async def _buffered_response(
        self,
        request_id: str,
        upstream_resp: httpx.Response,
        resp_headers: dict,
        start: float,
        should_record: bool,
    ) -> Response:
        """Handle non-streaming response: buffer full body then forward."""
        body = await upstream_resp.aread()
        await upstream_resp.aclose()
        duration_ms = (time.monotonic() - start) * 1000

        if should_record:
            self.recorder.record_response(
                request_id=request_id,
                status_code=upstream_resp.status_code,
                headers=resp_headers,
                body=body,
                is_stream=False,
                duration_ms=duration_ms,
            )

        logger.info(
            "%s %d %.1fms (buffered)",
            request_id[:8], upstream_resp.status_code, duration_ms,
        )
        return Response(
            content=body,
            status_code=upstream_resp.status_code,
            headers=resp_headers,
        )

    async def _stream_response(
        self,
        request_id: str,
        upstream_resp: httpx.Response,
        resp_headers: dict,
        start: float,
        should_record: bool,
    ) -> StreamingResponse:
        """Handle SSE streaming response: forward chunks while accumulating for recording."""
        accumulated_text = ""

        async def generate():
            nonlocal accumulated_text
            try:
                async for chunk, acc in stream_and_record(upstream_resp.aiter_bytes()):
                    accumulated_text = acc
                    yield chunk
            except Exception as e:
                logger.error("Stream error for %s: %s", request_id[:8], e)
                if should_record:
                    self.recorder.record_response(
                        request_id=request_id,
                        status_code=upstream_resp.status_code,
                        headers=resp_headers,
                        body=accumulated_text,
                        is_stream=True,
                        duration_ms=(time.monotonic() - start) * 1000,
                        error=str(e),
                    )
                raise
            finally:
                await upstream_resp.aclose()
                duration_ms = (time.monotonic() - start) * 1000
                if should_record:
                    self.recorder.record_response(
                        request_id=request_id,
                        status_code=upstream_resp.status_code,
                        headers=resp_headers,
                        body=accumulated_text,
                        is_stream=True,
                        duration_ms=duration_ms,
                    )
                logger.info(
                    "%s %d %.1fms (streamed, %d bytes)",
                    request_id[:8], upstream_resp.status_code,
                    duration_ms, len(accumulated_text),
                )

        return StreamingResponse(
            content=generate(),
            status_code=upstream_resp.status_code,
            headers=resp_headers,
        )
