"""
WebSocket proxy integration tests.

Uses Starlette TestClient's WebSocket support for the client side.
A lightweight asyncio WebSocket server (via websockets library) acts
as the upstream so we can test bidirectional message passing.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
import websockets
import websockets.asyncio.server
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute, Route
from starlette.responses import JSONResponse
from starlette.requests import Request
from starlette.testclient import TestClient
from starlette.websockets import WebSocket

from app.config import Config, RecordingFilter, FilterRule
from app.recorder import Recorder
from app.ws import WSProxyHandler, _upstream_ws_url
from tests.conftest import db_rows


# ── WS URL conversion ─────────────────────────────────────────────────────────

class TestUpstreamWsUrl:
    def test_http_to_ws(self):
        assert _upstream_ws_url("http://host:8080", "/path", "") == "ws://host:8080/path"

    def test_https_to_wss(self):
        assert _upstream_ws_url("https://api.openai.com", "/v1/realtime", "") == "wss://api.openai.com/v1/realtime"

    def test_query_string_appended(self):
        url = _upstream_ws_url("http://host", "/ws", "model=gpt-4o")
        assert url == "ws://host/ws?model=gpt-4o"

    def test_trailing_slash_not_doubled(self):
        url = _upstream_ws_url("http://host/", "/ws", "")
        assert not url.startswith("ws://host//")


# ── in-process upstream WS server fixture ────────────────────────────────────

class _UpstreamWsServer:
    """Minimal asyncio WebSocket server running in a background thread."""

    def __init__(self):
        self.host = "127.0.0.1"
        self.port: int | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self.received_messages: list[str] = []

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    async def _handler(self, ws):
        """Echo server: send 'connected', then echo all messages, then close."""
        await ws.send("connected")
        async for msg in ws:
            self.received_messages.append(msg)
            await ws.send(f"echo:{msg}")

    async def _serve(self):
        self._server = await websockets.asyncio.server.serve(
            self._handler, self.host, 0
        )
        self.port = self._server.sockets[0].getsockname()[1]
        await self._server.serve_forever()

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        future = asyncio.run_coroutine_threadsafe(self._serve(), self._loop)
        # Wait for port to be assigned
        import time
        deadline = time.monotonic() + 5
        while self.port is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert self.port is not None, "Upstream WS server did not start"

    def stop(self):
        if self._server:
            self._server.close()


@pytest.fixture
def upstream_ws_server():
    srv = _UpstreamWsServer()
    srv.start()
    yield srv
    srv.stop()


def make_ws_proxy_app(upstream_url: str, tmp_path: Path,
                      recording_filter: RecordingFilter | None = None) -> tuple[Starlette, Recorder]:
    cfg = Config(
        upstream_url=upstream_url,
        log_dir=str(tmp_path / "logs"),
        recording_filter=recording_filter or RecordingFilter(),
    )
    rec = Recorder(cfg)
    ws_handler = WSProxyHandler(cfg, rec)

    async def health(req: Request):
        return JSONResponse({"status": "ok"})

    async def ws_catch(ws: WebSocket):
        await ws_handler.handle(ws)

    app = Starlette(routes=[
        Route("/health", health, methods=["GET"]),
        WebSocketRoute("/{path:path}", ws_catch),
        WebSocketRoute("/", ws_catch),
    ])
    return app, rec


# ── tests ─────────────────────────────────────────────────────────────────────

class TestWSProxyForwarding:
    def test_connect_and_receive_initial_message(self, upstream_ws_server, tmp_path: Path):
        app, _ = make_ws_proxy_app(upstream_ws_server.url, tmp_path)
        client = TestClient(app)
        with client.websocket_connect("/ws/chat") as ws:
            msg = ws.receive_text()
            assert msg == "connected"

    def test_echo_message(self, upstream_ws_server, tmp_path: Path):
        app, _ = make_ws_proxy_app(upstream_ws_server.url, tmp_path)
        client = TestClient(app)
        with client.websocket_connect("/ws/echo") as ws:
            ws.receive_text()  # "connected"
            ws.send_text("hello proxy")
            reply = ws.receive_text()
            assert reply == "echo:hello proxy"

    def test_multiple_messages(self, upstream_ws_server, tmp_path: Path):
        app, _ = make_ws_proxy_app(upstream_ws_server.url, tmp_path)
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.receive_text()  # connected
            for i in range(3):
                ws.send_text(f"msg{i}")
                assert ws.receive_text() == f"echo:msg{i}"


class TestWSRecording:
    def test_connect_recorded(self, upstream_ws_server, tmp_path: Path):
        app, rec = make_ws_proxy_app(upstream_ws_server.url, tmp_path)
        client = TestClient(app)
        with client.websocket_connect("/ws/realtime") as ws:
            ws.receive_text()
        rec.flush()
        rows = db_rows(rec, "raw_ws_connections")
        assert len(rows) == 1
        assert rows[0]["path"] == "/ws/realtime"

    def test_messages_recorded(self, upstream_ws_server, tmp_path: Path):
        app, rec = make_ws_proxy_app(upstream_ws_server.url, tmp_path)
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.receive_text()  # connected (server→client)
            ws.send_text("test-message")
            ws.receive_text()  # echo (server→client)

        rec.flush()
        msgs = db_rows(rec, "raw_ws_messages")
        directions = {m["direction"] for m in msgs}
        assert "client_to_server" in directions
        assert "server_to_client" in directions
        texts = [m["data"] for m in msgs]
        assert any("test-message" in t for t in texts)

    def test_close_updates_duration(self, upstream_ws_server, tmp_path: Path):
        app, rec = make_ws_proxy_app(upstream_ws_server.url, tmp_path)
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.receive_text()

        rec.flush()
        rows = db_rows(rec, "raw_ws_connections")
        assert rows[0]["duration_ms"] is not None
        assert rows[0]["duration_ms"] >= 0
        assert rows[0]["closed_at"] is not None

    def test_ws_not_recorded_when_excluded(self, upstream_ws_server, tmp_path: Path):
        filt = RecordingFilter(exclude=[FilterRule("/ws/internal")])
        app, rec = make_ws_proxy_app(upstream_ws_server.url, tmp_path, recording_filter=filt)
        client = TestClient(app)
        with client.websocket_connect("/ws/internal") as ws:
            ws.receive_text()

        assert db_rows(rec, "raw_ws_connections") == []

    def test_ws_recorded_when_included(self, upstream_ws_server, tmp_path: Path):
        filt = RecordingFilter(include=[FilterRule("/ws/realtime")])
        app, rec = make_ws_proxy_app(upstream_ws_server.url, tmp_path, recording_filter=filt)
        client = TestClient(app)

        # This path is in include → should record
        with client.websocket_connect("/ws/realtime") as ws:
            ws.receive_text()

        # This path is NOT in include → should not record
        with client.websocket_connect("/ws/other") as ws:
            ws.receive_text()

        rows = db_rows(rec, "raw_ws_connections")
        assert len(rows) == 1
        assert rows[0]["path"] == "/ws/realtime"
