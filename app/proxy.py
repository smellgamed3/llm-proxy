from __future__ import annotations

import logging
import socket
import time
from urllib.parse import urlsplit, urlunsplit

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
    "proxy-connection", "te", "trailers", "transfer-encoding", "upgrade",
})


def _connection_header_tokens(headers: list[tuple[str, str]]) -> set[str]:
    tokens: set[str] = set()
    for key, value in headers:
        if key.lower() != "connection":
            continue
        tokens.update(
            token.strip().lower()
            for token in value.split(",")
            if token.strip()
        )
    return tokens


def _filter_header_pairs(headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Remove hop-by-hop headers, including Connection-declared extension headers."""
    excluded = HOP_BY_HOP | _connection_header_tokens(headers)
    return [
        (key, value)
        for key, value in headers
        if key.lower() not in excluded
    ]


def _remove_headers(headers: list[tuple[str, str]], names: set[str]) -> list[tuple[str, str]]:
    return [(key, value) for key, value in headers if key.lower() not in names]


def _format_forwarded_for(request: Request) -> str | None:
    if not request.client:
        return None
    host = request.client.host
    if ":" in host and not host.startswith("["):
        return f'"[{host}]"'
    return f'"{host}"'


def _append_forwarded_headers(request: Request, headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Add standard reverse-proxy headers for upstream apps/UI redirects."""
    forwarded = _remove_headers(
        headers,
        {
            "x-forwarded-proto",
            "x-forwarded-host",
            "x-forwarded-port",
            "x-forwarded-server",
            "x-real-ip",
            "forwarded",
        },
    )

    client_host = request.client.host if request.client else None
    existing_xff = None
    for key, value in headers:
        if key.lower() == "x-forwarded-for":
            existing_xff = value
            break

    if client_host:
        xff_value = (
            f"{existing_xff}, {client_host}" if existing_xff else client_host
        )
        forwarded = _remove_headers(forwarded, {"x-forwarded-for"})
        forwarded.append(("X-Forwarded-For", xff_value))
        forwarded.append(("X-Real-IP", client_host))

    host_value = request.headers.get("host", request.url.netloc)
    forwarded.append(("X-Forwarded-Proto", request.url.scheme))
    forwarded.append(("X-Forwarded-Host", host_value))
    forwarded.append(("X-Forwarded-Server", socket.gethostname()))

    if request.url.port is not None:
        forwarded.append(("X-Forwarded-Port", str(request.url.port)))
    elif request.url.scheme == "https":
        forwarded.append(("X-Forwarded-Port", "443"))
    else:
        forwarded.append(("X-Forwarded-Port", "80"))

    forwarded_for = _format_forwarded_for(request)
    if forwarded_for:
        forwarded.append(
            (
                "Forwarded",
                f"for={forwarded_for};proto={request.url.scheme};host={host_value}",
            )
        )

    return forwarded


def _rewrite_location_header(request: Request, upstream_url: str, location: str) -> str:
    """Rewrite absolute upstream redirects back to the proxy-visible origin."""
    if not location:
        return location

    location_parts = urlsplit(location)
    if not location_parts.scheme or not location_parts.netloc:
        return location

    upstream_parts = urlsplit(upstream_url)
    if (
        location_parts.scheme != upstream_parts.scheme
        or location_parts.netloc != upstream_parts.netloc
    ):
        return location

    request_host = request.headers.get("host", request.url.netloc)
    return urlunsplit(
        (
            request.url.scheme,
            request_host,
            location_parts.path,
            location_parts.query,
            location_parts.fragment,
        )
    )


def _request_target(request: Request) -> tuple[str, str, str]:
    raw_path = request.scope.get("raw_path", b"") or request.url.path.encode("ascii")
    path = raw_path.decode("ascii")
    query_bytes = request.scope.get("query_string", b"")
    query_string = query_bytes.decode("ascii") if query_bytes else ""
    if query_string:
        return request.url.path, query_string, f"{path}?{query_string}"
    return request.url.path, "", path


def _response_header_pairs(request: Request, upstream_url: str, response: httpx.Response) -> list[tuple[str, str]]:
    pairs = _filter_header_pairs(list(response.headers.multi_items()))
    rewritten: list[tuple[str, str]] = []
    for key, value in pairs:
        if key.lower() == "location":
            value = _rewrite_location_header(request, upstream_url, value)
        rewritten.append((key, value))
    return rewritten


def _response_headers_for_recording(headers: list[tuple[str, str]]) -> dict[str, str]:
    return dict(headers)


def _response_with_raw_headers(
    *,
    content: bytes,
    status_code: int,
    headers: list[tuple[str, str]],
) -> Response:
    response = Response(content=content, status_code=status_code)
    response.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers]
    return response


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

        path, query_string, upstream_path = _request_target(request)

        # Read request body
        body = await request.body()

        # Forward headers
        fwd_headers = _append_forwarded_headers(
            request,
            _filter_header_pairs(
                [
                    (k.decode("latin-1"), v.decode("latin-1"))
                    for k, v in request.headers.raw
                ]
            ),
        )
        fwd_headers = _remove_headers(fwd_headers, {"host"})
        if self.config.preserve_host and request.headers.get("host"):
            fwd_headers.append(("Host", request.headers["host"]))

        # Check if we should record this request
        should_record = self.config.recording_filter.should_record(path)

        # Full upstream URL for recording (no JSON parsing, just metadata)
        full_upstream_url = f"{self.config.upstream_url}{upstream_path}"
        client_ip = request.client.host if request.client else None
        client_port = request.client.port if request.client else None

        if should_record:
            self.recorder.record_request(
                request_id=request_id,
                method=request.method,
                path=path,
                query_string=query_string or "",
                headers=dict(fwd_headers),
                body=body if body else None,
                client_ip=client_ip,
                client_port=client_port,
                upstream_url=full_upstream_url,
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
        resp_header_pairs = _response_header_pairs(
            request,
            self.config.upstream_url,
            upstream_resp,
        )

        content_type = upstream_resp.headers.get("content-type", "")
        is_stream = is_sse_response(content_type)

        if is_stream:
            return await self._stream_response(
                request_id, upstream_resp, resp_header_pairs, start, should_record,
            )
        else:
            return await self._buffered_response(
                request_id, upstream_resp, resp_header_pairs, start, should_record,
            )

    async def _buffered_response(
        self,
        request_id: str,
        upstream_resp: httpx.Response,
        resp_headers: list[tuple[str, str]],
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
                headers=_response_headers_for_recording(resp_headers),
                body=body,
                is_stream=False,
                duration_ms=duration_ms,
            )

        logger.info(
            "%s %d %.1fms (buffered)",
            request_id[:8], upstream_resp.status_code, duration_ms,
        )
        return _response_with_raw_headers(
            content=body,
            status_code=upstream_resp.status_code,
            headers=resp_headers,
        )

    async def _stream_response(
        self,
        request_id: str,
        upstream_resp: httpx.Response,
        resp_headers: list[tuple[str, str]],
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
                        headers=_response_headers_for_recording(resp_headers),
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
                        headers=_response_headers_for_recording(resp_headers),
                        body=accumulated_text,
                        is_stream=True,
                        duration_ms=duration_ms,
                    )
                logger.info(
                    "%s %d %.1fms (streamed, %d bytes)",
                    request_id[:8], upstream_resp.status_code,
                    duration_ms, len(accumulated_text),
                )

        response = StreamingResponse(
            content=generate(),
            status_code=upstream_resp.status_code,
        )
        response.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in resp_headers]
        return response
