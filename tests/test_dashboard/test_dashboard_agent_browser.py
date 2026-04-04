from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "api" / "static"
ADMIN_HASH = "abcdef0123456789abcdef0123456789"
RAW_API_KEY = "sk-local-secret"


@dataclass
class RequestRecord:
    path: str
    hashes: list[str]


@dataclass
class DashboardMockState:
    requests: list[RequestRecord] = field(default_factory=list)

    def record(self, path: str, hashes: list[str]) -> None:
        self.requests.append(RequestRecord(path=path, hashes=hashes))

    def latest_hashes(self, path: str) -> list[str]:
        for record in reversed(self.requests):
            if record.path == path:
                return record.hashes
        return []


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _build_handler(state: DashboardMockState):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._handle_api(parsed)
                return
            self._handle_static(parsed.path)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _handle_api(self, parsed) -> None:
            query = parse_qs(parsed.query)
            hashes = []
            for raw_value in query.get("key_hashes", []):
                for item in raw_value.split(","):
                    normalized = item.strip()
                    if normalized and normalized not in hashes:
                        hashes.append(normalized)
            state.record(parsed.path, hashes)
            if not hashes:
                _json_response(self, HTTPStatus.UNAUTHORIZED, {"detail": "API key hash required"})
                return

            is_admin = ADMIN_HASH in hashes

            if parsed.path == "/api/overview":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "total_requests": 999 if is_admin else 111,
                        "success_rate": 0.98,
                        "total_cost_usd": 12.3456 if is_admin else 1.2345,
                        "avg_duration_ms": 245.5,
                        "total_tokens": 43210 if is_admin else 3210,
                    },
                )
                return

            if parsed.path == "/api/overview/daily":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {"date": "2026-04-04", "requests": 12, "cost_usd": 1.1, "total_tokens": 1200},
                        {"date": "2026-04-05", "requests": 24, "cost_usd": 2.2, "total_tokens": 2400},
                    ],
                )
                return

            if parsed.path == "/api/models/usage":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {
                            "model": "gpt-4o-mini",
                            "provider": "openai",
                            "request_count": 24 if is_admin else 10,
                            "success_count": 24 if is_admin else 10,
                            "error_count": 0,
                            "total_tokens": 2400 if is_admin else 1000,
                            "cost_usd": 2.2 if is_admin else 0.8,
                            "avg_duration_ms": 210.2,
                        }
                    ],
                )
                return

            if parsed.path == "/api/admin/status":
                if not is_admin:
                    _json_response(self, HTTPStatus.FORBIDDEN, {"detail": "Admin access required"})
                    return
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "raw_db": {
                            "path": "/tmp/raw.db",
                            "file_size_bytes": 4096,
                            "total_rows": 999,
                            "finalized_rows": 980,
                            "pending_rows": 9,
                            "backlog_rows": 10,
                            "error_rows": 0,
                            "last_timestamp": "2026-04-05T10:00:00Z",
                            "avg_duration_ms": 245.5,
                            "payload_bytes": 8192,
                        },
                        "analytics_db": {
                            "path": "/tmp/analytics.db",
                            "file_size_bytes": 4096,
                            "conversation_count": 777,
                            "template_count": 55,
                            "daily_stats_rows": 18,
                            "watermark_seq": 999,
                        },
                        "worker": {
                            "status": "idle",
                            "is_running": False,
                            "progress": 0.0,
                            "processed_rows": 0,
                            "total_rows": 0,
                            "remaining_rows": 0,
                            "current_seq": 0,
                            "target_seq": 0,
                            "last_timestamp": None,
                            "started_at": None,
                            "finished_at": None,
                            "error": None,
                        },
                    },
                )
                return

            _json_response(self, HTTPStatus.NOT_FOUND, {"detail": "Not found"})

        def _handle_static(self, raw_path: str) -> None:
            relative_path = "index.html" if raw_path in ("", "/") else raw_path.lstrip("/")
            file_path = STATIC_DIR / relative_path
            if not file_path.exists() or not file_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            if relative_path == "index.html":
                html = file_path.read_text(encoding="utf-8")
                html = html.replace(
                    '<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>',
                    '<script>window.Chart=function(){this.destroy=function(){};};</script>',
                )
                payload = html.encode("utf-8")
                content_type = "text/html; charset=utf-8"
            else:
                payload = file_path.read_bytes()
                if relative_path.endswith(".js"):
                    content_type = "application/javascript; charset=utf-8"
                elif relative_path.endswith(".css"):
                    content_type = "text/css; charset=utf-8"
                else:
                    content_type = "text/plain; charset=utf-8"

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return DashboardHandler


@pytest.fixture
def dashboard_server() -> tuple[str, DashboardMockState]:
    state = DashboardMockState()
    port = _pick_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _build_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class AgentBrowser:
    def __init__(self) -> None:
        self.session = f"llm-proxy-{uuid.uuid4().hex}"

    def run(self, *args: str) -> str:
        result = subprocess.run(
            ["agent-browser", "--session", self.session, *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def close(self) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self.run("close")


def _normalize_scalar(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith('"') and normalized.endswith('"'):
        return normalized[1:-1]
    return normalized


def _wait_until(assertion, timeout: float = 8.0, interval: float = 0.2) -> None:
    deadline = time.time() + timeout
    last_error: AssertionError | None = None
    while time.time() < deadline:
        try:
            assertion()
            return
        except AssertionError as error:
            last_error = error
            time.sleep(interval)
    if last_error is not None:
        raise last_error
    raise AssertionError("condition not met before timeout")


def _expect_text(browser: AgentBrowser, selector: str, expected: str) -> None:
    assert _normalize_scalar(browser.run("get", "text", selector)) == expected


def _expect_count(browser: AgentBrowser, selector: str, expected: int) -> None:
    assert int(_normalize_scalar(browser.run("get", "count", selector))) == expected


def _expect_eval(browser: AgentBrowser, expression: str, expected: str) -> None:
    assert _normalize_scalar(browser.run("eval", expression)) == expected


@pytest.mark.skipif(shutil.which("agent-browser") is None, reason="agent-browser CLI is required")
def test_dashboard_key_manager_theme_and_admin_scope_with_agent_browser(
    dashboard_server: tuple[str, DashboardMockState],
) -> None:
    base_url, state = dashboard_server
    browser = AgentBrowser()
    scoped_hash = hashlib.sha256(RAW_API_KEY.encode("utf-8")).hexdigest()[:32]

    try:
        browser.run("open", f"{base_url}/")
        browser.run("wait", "#key-modal")
        browser.run("fill", "#key-modal-input", RAW_API_KEY)
        browser.run("click", ".key-modal-actions .btn-primary")

        _wait_until(lambda: _expect_count(browser, ".key-item", 1))
        _wait_until(lambda: _expect_text(browser, "#total-requests", "111"))

        stored_hashes = browser.run("eval", 'localStorage.getItem("llm_proxy_key_hashes")')
        assert RAW_API_KEY not in stored_hashes
        assert scoped_hash in stored_hashes
        assert state.latest_hashes("/api/overview") == [scoped_hash]

        browser.run("eval", "setKeyManagerExpanded(true); showKeyModal();")
        browser.run("wait", "#key-modal")
        browser.run("fill", "#key-modal-hash-input", ADMIN_HASH)
        browser.run("fill", "#key-modal-label", "Admin Key")
        browser.run("click", ".key-modal-actions .btn-primary")

        _wait_until(lambda: _expect_count(browser, ".key-item", 2))
        _wait_until(lambda: _expect_text(browser, "#total-requests", "999"))
        assert state.latest_hashes("/api/overview") == [scoped_hash, ADMIN_HASH]

        browser.run("eval", f"setKeyManagerExpanded(true); showEditKeyModal('{scoped_hash}');")
        browser.run("wait", "#key-modal")
        browser.run("fill", "#key-modal-label", "Scoped Alias")
        browser.run("click", ".key-modal-actions .btn-primary")
        _wait_until(lambda: _expect_text(browser, ".key-item:first-child .key-item-label", "Scoped Alias"))

        browser.run("eval", "setKeyManagerExpanded(true)")
        browser.run("uncheck", ".key-item:nth-child(2) .key-item-toggle input")
        _wait_until(lambda: _expect_text(browser, "#total-requests", "111"))
        assert state.latest_hashes("/api/overview") == [scoped_hash]

        browser.run("click", "#theme-toggle")
        _wait_until(lambda: _expect_eval(browser, 'document.documentElement.dataset.theme', "dark"))
        assert _normalize_scalar(browser.run("eval", 'localStorage.getItem("llm_proxy_theme")')) == "dark"
    finally:
        browser.close()