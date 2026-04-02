"""
HTTP proxy integration tests.

Uses Starlette TestClient.  A mock upstream ASGI app is served alongside
the proxy so no real network calls are made.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.config import Config, RecordingFilter, FilterRule
from app.recorder import Recorder
from tests.conftest import db_rows, jsonl_bodies


# ── mock upstream helpers ─────────────────────────────────────────────────────

def make_upstream(routes: list[Route]) -> Starlette:
    return Starlette(routes=routes)


def make_proxy_with_upstream(
    tmp_path: Path,
    upstream_app: Starlette,
    recording_filter: RecordingFilter | None = None,
) -> tuple[TestClient, TestClient, Recorder]:
    """Return (proxy_client, upstream_client, recorder)."""
    upstream_client = TestClient(upstream_app, raise_server_exceptions=True)

    log_dir = str(tmp_path / "logs")
    cfg = Config(
        upstream_url="http://testserver",
        log_dir=log_dir,
        recording_filter=recording_filter or RecordingFilter(),
    )
    recorder = Recorder(cfg)

    # Patch httpx transport to route to the mock upstream
    from app.proxy import ProxyHandler
    from app.ws import WSProxyHandler
    from starlette.routing import WebSocketRoute
    import httpx

    proxy_handler = ProxyHandler(cfg, recorder)
    # Replace the httpx client's transport with a transport backed by the upstream TestClient
    proxy_handler.client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=upstream_app),  # type: ignore[arg-type]
        base_url="http://testserver",
        timeout=httpx.Timeout(connect=5, read=30, write=10, pool=5),
        follow_redirects=False,
    )

    ws_handler = WSProxyHandler(cfg, recorder)

    from starlette.applications import Starlette as _S
    from starlette.responses import JSONResponse as _J

    async def health(req: Request):
        return _J({"status": "ok"})

    async def http_catch(req: Request):
        return await proxy_handler.handle(req)

    HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
    proxy_app = _S(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/{path:path}", http_catch, methods=HTTP_METHODS),
            Route("/", http_catch, methods=HTTP_METHODS),
        ],
    )
    proxy_client = TestClient(proxy_app, raise_server_exceptions=False)
    return proxy_client, upstream_client, recorder


# ── tests ─────────────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_returns_ok(self, tmp_path: Path):
        upstream = make_upstream([])
        client, _, _ = make_proxy_with_upstream(tmp_path, upstream)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_health_not_recorded(self, tmp_path: Path):
        upstream = make_upstream([])
        client, _, rec = make_proxy_with_upstream(
            tmp_path, upstream,
            recording_filter=RecordingFilter(),
        )
        client.get("/health")
        # /health is handled locally, never reaches proxy handler
        assert db_rows(rec, "requests") == []


class TestHTTPProxyForwarding:
    def test_get_forwarded(self, tmp_path: Path):
        async def echo(req: Request):
            return JSONResponse({"path": req.url.path})

        upstream = make_upstream([Route("/{path:path}", echo, methods=["GET"]), Route("/", echo, methods=["GET"])])
        client, _, rec = make_proxy_with_upstream(tmp_path, upstream)

        resp = client.get("/v1/models")
        assert resp.status_code == 200
        assert resp.json()["path"] == "/v1/models"

    def test_post_with_json_body_forwarded(self, tmp_path: Path):
        async def echo_body(req: Request):
            body = await req.json()
            return JSONResponse({"received": body})

        upstream = make_upstream([
            Route("/{path:path}", echo_body, methods=["POST"]),
            Route("/", echo_body, methods=["POST"]),
        ])
        client, _, rec = make_proxy_with_upstream(tmp_path, upstream)

        payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
        resp = client.post("/v1/chat/completions", json=payload)
        assert resp.status_code == 200
        assert resp.json()["received"]["model"] == "gpt-4o"

    def test_query_string_forwarded(self, tmp_path: Path):
        async def echo_qs(req: Request):
            return JSONResponse({"qs": str(req.query_params)})

        upstream = make_upstream([Route("/{path:path}", echo_qs, methods=["GET"]), Route("/", echo_qs, methods=["GET"])])
        client, _, _ = make_proxy_with_upstream(tmp_path, upstream)

        resp = client.get("/v1/models?limit=10&offset=0")
        assert "limit" in resp.json()["qs"]

    def test_upstream_status_code_preserved(self, tmp_path: Path):
        async def not_found(req: Request):
            return Response(status_code=404, content=b"not found")

        upstream = make_upstream([
            Route("/{path:path}", not_found, methods=["GET", "POST"]),
            Route("/", not_found, methods=["GET", "POST"]),
        ])
        client, _, _ = make_proxy_with_upstream(tmp_path, upstream)
        resp = client.get("/no/such/path")
        assert resp.status_code == 404

    def test_upstream_custom_header_forwarded(self, tmp_path: Path):
        async def echo_header(req: Request):
            return JSONResponse({}, headers={"x-custom": "value123"})

        upstream = make_upstream([
            Route("/{path:path}", echo_header, methods=["GET"]),
            Route("/", echo_header, methods=["GET"]),
        ])
        client, _, _ = make_proxy_with_upstream(tmp_path, upstream)
        resp = client.get("/v1/test")
        assert resp.headers.get("x-custom") == "value123"


class TestHTTPRecording:
    def test_request_recorded(self, tmp_path: Path):
        async def ok(req: Request):
            return JSONResponse({"ok": True})

        upstream = make_upstream([Route("/{path:path}", ok, methods=["POST"]), Route("/", ok, methods=["POST"])])
        client, _, rec = make_proxy_with_upstream(tmp_path, upstream)

        client.post("/v1/chat/completions", json={"model": "gpt-4"})
        rows = db_rows(rec, "requests")
        assert len(rows) == 1
        assert rows[0]["path"] == "/v1/chat/completions"
        assert rows[0]["method"] == "POST"
        assert rows[0]["model"] == "gpt-4"

    def test_response_recorded(self, tmp_path: Path):
        async def ok(req: Request):
            return JSONResponse({"id": "cmpl-1"}, status_code=200)

        upstream = make_upstream([Route("/{path:path}", ok, methods=["POST"]), Route("/", ok, methods=["POST"])])
        client, _, rec = make_proxy_with_upstream(tmp_path, upstream)

        client.post("/v1/chat", json={"model": "m"})
        rows = db_rows(rec, "requests")
        assert rows[0]["status_code"] == 200
        assert rows[0]["duration_ms"] > 0

    def test_request_body_in_jsonl(self, tmp_path: Path):
        async def ok(req: Request):
            return JSONResponse({"ok": True})

        upstream = make_upstream([Route("/{path:path}", ok, methods=["POST"]), Route("/", ok, methods=["POST"])])
        client, _, rec = make_proxy_with_upstream(tmp_path, upstream)

        client.post("/v1/chat", json={"model": "claude-3"})
        bodies = jsonl_bodies(rec)
        req_bodies = [b for b in bodies if b["ref"].endswith(":request")]
        assert any("claude-3" in b["data"] for b in req_bodies)


class TestRecordingFilter:
    def test_excluded_path_not_recorded(self, tmp_path: Path):
        async def ok(req: Request):
            return JSONResponse({"ok": True})

        upstream = make_upstream([Route("/{path:path}", ok, methods=["GET"]), Route("/", ok, methods=["GET"])])
        filt = RecordingFilter(exclude=[FilterRule("/v1/models")])
        client, _, rec = make_proxy_with_upstream(tmp_path, upstream, recording_filter=filt)

        client.get("/v1/models")
        assert db_rows(rec, "requests") == []

    def test_excluded_path_still_forwarded(self, tmp_path: Path):
        """Even excluded paths are forwarded — just not recorded."""
        async def ok(req: Request):
            return JSONResponse({"ok": True})

        upstream = make_upstream([Route("/{path:path}", ok, methods=["GET"]), Route("/", ok, methods=["GET"])])
        filt = RecordingFilter(exclude=[FilterRule("/v1/models")])
        client, _, _ = make_proxy_with_upstream(tmp_path, upstream, recording_filter=filt)

        resp = client.get("/v1/models")
        assert resp.status_code == 200  # forwarded

    def test_include_limits_recording(self, tmp_path: Path):
        async def ok(req: Request):
            return JSONResponse({"ok": True})

        upstream = make_upstream([Route("/{path:path}", ok, methods=["GET"]), Route("/", ok, methods=["GET"])])
        filt = RecordingFilter(include=[FilterRule("/v1/chat")])
        client, _, rec = make_proxy_with_upstream(tmp_path, upstream, recording_filter=filt)

        client.get("/v1/chat/completions")
        client.get("/v1/models")  # not in include
        rows = db_rows(rec, "requests")
        assert len(rows) == 1
        assert rows[0]["path"] == "/v1/chat/completions"


class TestSSEProxy:
    def test_sse_response_streamed(self, tmp_path: Path):
        async def sse_endpoint(req: Request):
            async def gen():
                for chunk in [b"data: hello\n\n", b"data: world\n\n", b"data: [DONE]\n\n"]:
                    yield chunk

            return StreamingResponse(gen(), media_type="text/event-stream")

        upstream = make_upstream([
            Route("/{path:path}", sse_endpoint, methods=["POST"]),
            Route("/", sse_endpoint, methods=["POST"]),
        ])
        client, _, rec = make_proxy_with_upstream(tmp_path, upstream)

        resp = client.post("/v1/chat/completions", json={"model": "gpt-4", "stream": True})
        assert resp.status_code == 200
        body = resp.text
        assert "data: hello" in body
        assert "data: [DONE]" in body

    def test_sse_response_recorded(self, tmp_path: Path):
        async def sse_endpoint(req: Request):
            async def gen():
                for chunk in [b"data: {\"delta\": \"Hi\"}\n\n", b"data: [DONE]\n\n"]:
                    yield chunk

            return StreamingResponse(gen(), media_type="text/event-stream")

        upstream = make_upstream([
            Route("/{path:path}", sse_endpoint, methods=["POST"]),
            Route("/", sse_endpoint, methods=["POST"]),
        ])
        client, _, rec = make_proxy_with_upstream(tmp_path, upstream)

        client.post("/v1/chat/completions", json={"model": "gpt-4", "stream": True})
        rows = db_rows(rec, "requests")
        assert rows[0]["is_stream"] == 1
        bodies = jsonl_bodies(rec)
        resp_bodies = [b for b in bodies if b["ref"].endswith(":response")]
        assert any("[DONE]" in b["data"] for b in resp_bodies)
