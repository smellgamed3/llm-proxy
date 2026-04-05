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
SCOPED_HASH = hashlib.sha256(RAW_API_KEY.encode("utf-8")).hexdigest()[:32]


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

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._handle_post(parsed)

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

            if parsed.path == "/api/costs/summary":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "total_cost_usd": 3.1415,
                        "total_tokens": 15432,
                        "total_requests": 88,
                    },
                )
                return

            if parsed.path == "/api/costs/daily":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {"date": "2026-04-04", "cost_usd": 1.25},
                        {"date": "2026-04-05", "cost_usd": 1.89},
                    ],
                )
                return

            if parsed.path == "/api/costs/by-model":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {
                            "model": "gpt-4o-mini",
                            "request_count": 44,
                            "total_tokens": 9000,
                            "cost_usd": 1.75,
                        },
                        {
                            "model": "claude-3-7-sonnet",
                            "request_count": 22,
                            "total_tokens": 4200,
                            "cost_usd": 0.92,
                        },
                    ],
                )
                return

            if parsed.path == "/api/latency/summary":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "p50": 210.5,
                        "p95": 880.1,
                        "p99": 1240.8,
                        "avg": 342.7,
                        "count": 88,
                    },
                )
                return

            if parsed.path == "/api/latency/daily":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {"date": "2026-04-04", "avg_ms": 310.2},
                        {"date": "2026-04-05", "avg_ms": 356.8},
                    ],
                )
                return

            if parsed.path == "/api/latency/by-model":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {"model": "claude-3-7-sonnet", "avg_ms": 512.3, "count": 12},
                        {"model": "gpt-4o-mini", "avg_ms": 208.4, "count": 44},
                    ],
                )
                return

            if parsed.path == "/api/latency/distribution":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {"bucket": "0-250", "count": 20},
                        {"bucket": "250-500", "count": 15},
                        {"bucket": "500-1000", "count": 5},
                    ],
                )
                return

            if parsed.path == "/api/conversations":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "items": [
                            {
                                "id": "conv-1",
                                "timestamp": "2026-04-05T10:20:30Z",
                                "model": "gpt-4o-mini",
                                "status": "success",
                                "request_type": "chat",
                                "user_prompt_preview": "Summarize the deployment plan",
                                "assistant_response_preview": "Here is the rollout summary",
                                "total_tokens": 1200,
                                "cost_usd": 0.018,
                                "duration_ms": 320.5,
                            },
                            {
                                "id": "conv-2",
                                "timestamp": "2026-04-05T11:00:00Z",
                                "model": "claude-3-7-sonnet",
                                "status": "error",
                                "request_type": "chat",
                                "user_prompt_preview": "Retry the failed worker sync",
                                "assistant_response_preview": "The upstream request timed out",
                                "total_tokens": 640,
                                "cost_usd": 0.009,
                                "duration_ms": 1500.0,
                            },
                        ],
                        "total": 2,
                        "page": 1,
                        "page_size": 50,
                    },
                )
                return

            if parsed.path == "/api/conversations/conv-1":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "id": "conv-1",
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "status": "success",
                        "template_id": "tpl-ops-summary",
                        "finish_reason": "stop",
                        "duration_ms": 320.5,
                        "cost_usd": 0.018,
                        "system_prompt": "You are an operations assistant.",
                        "user_prompt": "Summarize the deployment plan",
                        "assistant_response": "Rollout can proceed in three steps.",
                        "prompt_tokens": 800,
                        "completion_tokens": 400,
                        "total_tokens": 1200,
                        "rating": 4,
                        "rating_comment": "Useful summary",
                        "tags": '["ops","release"]',
                    },
                )
                return

            if parsed.path == "/api/conversations/conv-1/raw":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "request_body": json.dumps(
                            {
                                "messages": [
                                    {"role": "system", "content": "You are an operations assistant."},
                                    {"role": "user", "content": "Summarize the deployment plan"},
                                ],
                                "tools": [],
                            }
                        ),
                        "response_body": json.dumps(
                            {
                                "choices": [
                                    {
                                        "message": {"role": "assistant", "content": "Rollout can proceed in three steps."}
                                    }
                                ],
                                "usage": {
                                    "prompt_tokens": 800,
                                    "completion_tokens": 400,
                                    "total_tokens": 1200,
                                },
                            }
                        ),
                        "request_headers": json.dumps({"content-type": "application/json"}),
                        "response_headers": json.dumps({"content-type": "application/json"}),
                        "request_body_size": 256,
                        "response_body_size": 384,
                    },
                )
                return

            if parsed.path == "/api/prompts/templates":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "items": [
                            {
                                "template_id": "tpl-ops-summary",
                                "use_count": 42,
                                "avg_cost_usd": 0.0042,
                                "total_cost_usd": 0.1764,
                                "last_seen": "2026-04-05T10:20:30Z",
                                "system_prompt_preview": "You are an operations assistant focused on release summaries.",
                            }
                        ]
                    },
                )
                return

            if parsed.path == "/api/prompts/templates/tpl-ops-summary":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "template_id": "tpl-ops-summary",
                        "system_prompt": "You are an operations assistant focused on release summaries.",
                    },
                )
                return

            if parsed.path == "/api/prompts/templates/tpl-ops-summary/stats":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "quality_score": 88,
                        "total_conversations": 42,
                        "success_rate": 0.95,
                        "avg_duration_ms": 210.4,
                        "total_cost_usd": 0.1764,
                        "avg_rating": 4.5,
                        "rated_count": 12,
                    },
                )
                return

            if parsed.path == "/api/prompts/templates/tpl-ops-summary/daily":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {"date": "2026-04-04", "requests": 10, "cost_usd": 0.04},
                        {"date": "2026-04-05", "requests": 12, "cost_usd": 0.05},
                    ],
                )
                return

            if parsed.path == "/api/prompts/templates/tpl-ops-summary/conversations":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "items": [
                            {
                                "timestamp": "2026-04-05T10:20:30Z",
                                "model": "gpt-4o-mini",
                                "status": "success",
                                "total_tokens": 1200,
                                "cost_usd": 0.018,
                                "duration_ms": 320.5,
                                "rating": 4,
                                "user_prompt_preview": "Summarize the deployment plan",
                            }
                        ]
                    },
                )
                return

            if parsed.path == "/api/prompts/similar/tpl-ops-summary":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {
                            "template_id": "tpl-release-audit",
                            "similarity": 0.82,
                            "use_count": 18,
                            "avg_cost_usd": 0.0031,
                        }
                    ],
                )
                return

            if parsed.path == "/api/errors/summary":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "total_requests": 120,
                        "error_count": 3,
                        "error_rate": 0.025,
                    },
                )
                return

            if parsed.path == "/api/errors/recent":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {
                            "timestamp": "2026-04-05T09:00:00Z",
                            "model": "gpt-4o-mini",
                            "error_type": "timeout",
                            "status_code": 504,
                            "status": "error",
                            "error_message": "upstream timeout after 30s",
                        },
                        {
                            "timestamp": "2026-04-05T09:10:00Z",
                            "model": "claude-3-7-sonnet",
                            "error_type": "rate_limit",
                            "status_code": 429,
                            "status": "error",
                            "error_message": "rate limit exceeded",
                        },
                    ],
                )
                return

            if parsed.path == "/api/errors/daily":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {"date": "2026-04-04", "error_count": 1},
                        {"date": "2026-04-05", "error_count": 2},
                    ],
                )
                return

            if parsed.path == "/api/errors/by-type":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {"error_type": "timeout", "count": 2},
                        {"error_type": "rate_limit", "count": 1},
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

            if parsed.path == "/api/admin/analyzer/history":
                if not is_admin:
                    _json_response(self, HTTPStatus.FORBIDDEN, {"detail": "Admin access required"})
                    return
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {
                            "job_id": 18,
                            "status": "completed",
                            "mode": "incremental",
                            "processed_rows": 120,
                            "total_rows": 120,
                            "since": None,
                            "until": None,
                            "started_at": "2026-04-05T08:00:00Z",
                            "finished_at": "2026-04-05T08:00:18Z",
                        }
                    ],
                )
                return

            if parsed.path == "/api/admin/backups":
                if not is_admin:
                    _json_response(self, HTTPStatus.FORBIDDEN, {"detail": "Admin access required"})
                    return
                _json_response(
                    self,
                    HTTPStatus.OK,
                    [
                        {
                            "name": "analytics-20260405-080000.db",
                            "size_bytes": 40960,
                            "modified_at": "2026-04-05T08:00:19Z",
                        }
                    ],
                )
                return

            _json_response(self, HTTPStatus.NOT_FOUND, {"detail": "Not found"})

        def _handle_post(self, parsed) -> None:
            query = parse_qs(parsed.query)
            hashes = []
            for raw_value in query.get("key_hashes", []):
                for item in raw_value.split(","):
                    normalized = item.strip()
                    if normalized and normalized not in hashes:
                        hashes.append(normalized)
            state.record(parsed.path, hashes)

            is_admin = ADMIN_HASH in hashes
            if not hashes:
                _json_response(self, HTTPStatus.UNAUTHORIZED, {"detail": "API key hash required"})
                return

            if parsed.path == "/api/admin/backup":
                if not is_admin:
                    _json_response(self, HTTPStatus.FORBIDDEN, {"detail": "Admin access required"})
                    return
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "files": [
                            "raw-20260405-090000.db",
                            "analytics-20260405-090000.db",
                        ]
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

            if relative_path.endswith(".html"):
                html = file_path.read_text(encoding="utf-8")
                html = html.replace(
                    '<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>',
                    '<script>window.Chart=function(){this.destroy=function(){};};</script>',
                )
                html = html.replace(
                    '<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>',
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


def _seed_scoped_auth(browser: AgentBrowser, base_url: str) -> None:
    payload = json.dumps([
        {
            "hash": SCOPED_HASH,
            "label": "Scoped Alias",
            "active": True,
            "addedAt": "2026-04-05T00:00:00Z",
        }
    ])
    browser.run("open", f"{base_url}/")
    browser.run("wait", "#key-modal")
    browser.run("eval", f'localStorage.setItem("llm_proxy_key_hashes", {json.dumps(payload)})')


def _seed_admin_auth(browser: AgentBrowser, base_url: str) -> None:
    payload = json.dumps([
        {
            "hash": ADMIN_HASH,
            "label": "Admin Key",
            "active": True,
            "addedAt": "2026-04-05T00:00:00Z",
        }
    ])
    browser.run("open", f"{base_url}/")
    browser.run("wait", "#key-modal")
    browser.run("eval", f'localStorage.setItem("llm_proxy_key_hashes", {json.dumps(payload)})')


def _open_seeded_page(browser: AgentBrowser, base_url: str, page_path: str) -> None:
    _seed_scoped_auth(browser, base_url)
    browser.run("open", f"{base_url}{page_path}")


def _open_admin_page(browser: AgentBrowser, base_url: str, page_path: str) -> None:
    _seed_admin_auth(browser, base_url)
    browser.run("open", f"{base_url}{page_path}")


@pytest.mark.skipif(shutil.which("agent-browser") is None, reason="agent-browser CLI is required")
def test_key_manager_trigger_click_opens_popover(
    dashboard_server: tuple[str, DashboardMockState],
) -> None:
    """回归测试：点击 Key 管理按钮必须真正展开弹窗（不被 document click 监听器误关）。

    之前的 bug：renderKeyManager() 重渲染后 evt.target 已离开 DOM，
    document click 监听器误判为「外部点击」立即调用 setKeyManagerExpanded(false)。
    修复方案：改用 evt.composedPath() 检测触发路径是否在 #key-manager 内。
    """
    base_url, _ = dashboard_server
    browser = AgentBrowser()

    try:
        _open_seeded_page(browser, base_url, "/")
        browser.run("open", f"{base_url}/")
        _wait_until(lambda: _expect_eval(
            browser,
            'document.querySelector(".key-manager-trigger") ? "found" : "not found"',
            "found",
        ))
        # 直接点击按钮（而非 eval 注入），复现之前的 composedPath bug 场景
        browser.run("click", ".key-manager-trigger")
        _wait_until(lambda: _expect_eval(
            browser,
            # 弹窗不含 hidden 属性即为可见
            'String(document.querySelector(".key-manager-popover")?.hidden ?? "missing")',
            "false",
        ))
    finally:
        browser.close()


@pytest.mark.skipif(shutil.which("agent-browser") is None, reason="agent-browser CLI is required")
def test_key_manager_copy_hash_button(
    dashboard_server: tuple[str, DashboardMockState],
) -> None:
    """回归测试：复制按钮点击后显示成功 toast（clipboard API 或 execCommand 降级）。"""
    base_url, _ = dashboard_server
    browser = AgentBrowser()

    try:
        _open_seeded_page(browser, base_url, "/")
        browser.run("open", f"{base_url}/")
        _wait_until(lambda: _expect_eval(
            browser,
            'document.querySelector(".key-manager-trigger") ? "found" : "not found"',
            "found",
        ))
        browser.run("eval", "setKeyManagerExpanded(true)")
        _wait_until(lambda: _expect_eval(
            browser,
            'String(document.querySelector(".key-manager-popover")?.hidden ?? "missing")',
            "false",
        ))
        browser.run("click", ".key-item-btn[onclick^='copyHash']")
        # 成功 toast 出现在 DOM 中（不含"失败"字样）
        _wait_until(lambda: _expect_eval(
            browser,
            'document.querySelector("#toast-container .toast-success") ? "ok" : "no-toast"',
            "ok",
        ))
        toast_text = browser.run("eval", 'document.querySelector("#toast-container .toast-success")?.textContent ?? ""')
        assert "失败" not in _normalize_scalar(toast_text)
    finally:
        browser.close()


@pytest.mark.skipif(shutil.which("agent-browser") is None, reason="agent-browser CLI is required")
def test_dashboard_key_manager_theme_and_admin_scope_with_agent_browser(
    dashboard_server: tuple[str, DashboardMockState],
) -> None:
    base_url, state = dashboard_server
    browser = AgentBrowser()

    try:
        browser.run("open", f"{base_url}/")
        browser.run("wait", "#key-modal")
        browser.run("fill", "#key-modal-input", RAW_API_KEY)
        browser.run("click", ".key-modal-actions .btn-primary")

        _wait_until(lambda: _expect_count(browser, ".key-item", 1))
        _wait_until(lambda: _expect_text(browser, "#total-requests", "111"))

        stored_hashes = browser.run("eval", 'localStorage.getItem("llm_proxy_key_hashes")')
        assert RAW_API_KEY not in stored_hashes
        assert SCOPED_HASH in stored_hashes
        assert state.latest_hashes("/api/overview") == [SCOPED_HASH]

        browser.run("eval", "setKeyManagerExpanded(true); showKeyModal();")
        browser.run("wait", "#key-modal")
        browser.run("fill", "#key-modal-hash-input", ADMIN_HASH)
        browser.run("fill", "#key-modal-label", "Admin Key")
        browser.run("click", ".key-modal-actions .btn-primary")

        _wait_until(lambda: _expect_count(browser, ".key-item", 2))
        _wait_until(lambda: _expect_text(browser, "#total-requests", "999"))
        assert state.latest_hashes("/api/overview") == [SCOPED_HASH, ADMIN_HASH]

        browser.run("eval", f"setKeyManagerExpanded(true); showEditKeyModal('{SCOPED_HASH}');")
        browser.run("wait", "#key-modal")
        browser.run("fill", "#key-modal-label", "Scoped Alias")
        browser.run("click", ".key-modal-actions .btn-primary")
        _wait_until(lambda: _expect_text(browser, ".key-item:first-child .key-item-label", "Scoped Alias"))

        browser.run("eval", "setKeyManagerExpanded(true)")
        browser.run("uncheck", ".key-item:nth-child(2) .key-item-toggle input")
        _wait_until(lambda: _expect_text(browser, "#total-requests", "111"))
        assert state.latest_hashes("/api/overview") == [SCOPED_HASH]

        browser.run("click", "#theme-toggle")
        _wait_until(lambda: _expect_eval(browser, 'document.documentElement.dataset.theme', "dark"))
        assert _normalize_scalar(browser.run("eval", 'localStorage.getItem("llm_proxy_theme")')) == "dark"
    finally:
        browser.close()


@pytest.mark.skipif(shutil.which("agent-browser") is None, reason="agent-browser CLI is required")
def test_conversations_page_detail_flow_with_agent_browser(
    dashboard_server: tuple[str, DashboardMockState],
) -> None:
    base_url, state = dashboard_server
    browser = AgentBrowser()

    try:
        _open_seeded_page(browser, base_url, "/conversations.html")

        _wait_until(lambda: _expect_count(browser, "#conv-tbody tr", 2))
        _wait_until(lambda: _expect_text(browser, "#insight-loaded", "2"))
        browser.run("click", "#conv-tbody tr:first-child")
        _wait_until(lambda: _expect_eval(browser, 'String(document.getElementById("conv-modal-overlay").hidden)', "false"))
        _wait_until(lambda: _expect_eval(browser, 'document.getElementById("detail-meta").textContent.includes("conv-1") ? "yes" : "no"', "yes"))
        assert state.latest_hashes("/api/conversations") == [SCOPED_HASH]
        assert state.latest_hashes("/api/conversations/conv-1") == [SCOPED_HASH]
        assert state.latest_hashes("/api/conversations/conv-1/raw") == [SCOPED_HASH]
    finally:
        browser.close()


@pytest.mark.skipif(shutil.which("agent-browser") is None, reason="agent-browser CLI is required")
def test_prompts_page_detail_flow_with_agent_browser(
    dashboard_server: tuple[str, DashboardMockState],
) -> None:
    base_url, state = dashboard_server
    browser = AgentBrowser()

    try:
        _open_seeded_page(browser, base_url, "/prompts.html")

        _wait_until(lambda: _expect_count(browser, "#prompts-tbody tr", 1))
        _wait_until(lambda: _expect_eval(browser, 'document.getElementById("prompts-tbody").textContent.includes("tpl-ops-summary") ? "yes" : "no"', "yes"))
        browser.run("click", "#prompts-tbody tr:first-child")
        _wait_until(lambda: _expect_eval(browser, 'String(document.getElementById("template-detail").hidden)', "false"))
        _wait_until(lambda: _expect_text(browser, "#tmpl-detail-id", "tpl-ops-summary"))
        _wait_until(lambda: _expect_eval(browser, 'document.getElementById("tmpl-system-prompt").textContent.includes("operations assistant") ? "yes" : "no"', "yes"))
        assert state.latest_hashes("/api/prompts/templates") == [SCOPED_HASH]
        assert state.latest_hashes("/api/prompts/templates/tpl-ops-summary") == [SCOPED_HASH]
    finally:
        browser.close()


@pytest.mark.skipif(shutil.which("agent-browser") is None, reason="agent-browser CLI is required")
def test_errors_page_renders_summary_and_rows_with_agent_browser(
    dashboard_server: tuple[str, DashboardMockState],
) -> None:
    base_url, state = dashboard_server
    browser = AgentBrowser()

    try:
        _open_seeded_page(browser, base_url, "/errors.html")

        _wait_until(lambda: _expect_count(browser, "#error-summary .card", 3))
        _wait_until(lambda: _expect_text(browser, "#error-summary .card:nth-child(2) .card-value", "3"))
        _wait_until(lambda: _expect_count(browser, "#errors-tbody tr", 2))
        _wait_until(lambda: _expect_eval(browser, 'document.getElementById("errors-tbody").textContent.includes("timeout") ? "yes" : "no"', "yes"))
        assert state.latest_hashes("/api/errors/summary") == [SCOPED_HASH]
        assert state.latest_hashes("/api/errors/recent") == [SCOPED_HASH]
    finally:
        browser.close()


@pytest.mark.skipif(shutil.which("agent-browser") is None, reason="agent-browser CLI is required")
def test_models_page_renders_usage_table_with_agent_browser(
    dashboard_server: tuple[str, DashboardMockState],
) -> None:
    base_url, state = dashboard_server
    browser = AgentBrowser()

    try:
        _open_seeded_page(browser, base_url, "/models.html")

        _wait_until(lambda: _expect_count(browser, "#model-usage-tbody tr", 1))
        _wait_until(lambda: _expect_eval(browser, 'document.getElementById("model-usage-tbody").textContent.includes("gpt-4o-mini") ? "yes" : "no"', "yes"))
        assert state.latest_hashes("/api/models/usage") == [SCOPED_HASH]
    finally:
        browser.close()


@pytest.mark.skipif(shutil.which("agent-browser") is None, reason="agent-browser CLI is required")
def test_costs_page_renders_summary_and_table_with_agent_browser(
    dashboard_server: tuple[str, DashboardMockState],
) -> None:
    base_url, state = dashboard_server
    browser = AgentBrowser()

    try:
        _open_seeded_page(browser, base_url, "/costs.html")

        _wait_until(lambda: _expect_count(browser, "#cost-summary .card", 3))
        _wait_until(lambda: _expect_text(browser, "#cost-summary .card:first-child .card-value", "$3.1415"))
        _wait_until(lambda: _expect_count(browser, "#model-cost-tbody tr", 2))
        _wait_until(lambda: _expect_eval(browser, 'document.getElementById("model-cost-tbody").textContent.includes("claude-3-7-sonnet") ? "yes" : "no"', "yes"))
        assert state.latest_hashes("/api/costs/summary") == [SCOPED_HASH]
        assert state.latest_hashes("/api/costs/daily") == [SCOPED_HASH]
        assert state.latest_hashes("/api/costs/by-model") == [SCOPED_HASH]
    finally:
        browser.close()


@pytest.mark.skipif(shutil.which("agent-browser") is None, reason="agent-browser CLI is required")
def test_latency_page_renders_cards_and_table_with_agent_browser(
    dashboard_server: tuple[str, DashboardMockState],
) -> None:
    base_url, state = dashboard_server
    browser = AgentBrowser()

    try:
        _open_seeded_page(browser, base_url, "/latency.html")

        _wait_until(lambda: _expect_text(browser, "#latency-p95", "880.1"))
        _wait_until(lambda: _expect_text(browser, "#latency-count", "88"))
        _wait_until(lambda: _expect_count(browser, "#latency-model-tbody tr", 2))
        _wait_until(lambda: _expect_eval(browser, 'document.getElementById("latency-model-tbody").textContent.includes("gpt-4o-mini") ? "yes" : "no"', "yes"))
        assert state.latest_hashes("/api/latency/summary") == [SCOPED_HASH]
        assert state.latest_hashes("/api/latency/daily") == [SCOPED_HASH]
        assert state.latest_hashes("/api/latency/by-model") == [SCOPED_HASH]
        assert state.latest_hashes("/api/latency/distribution") == [SCOPED_HASH]
    finally:
        browser.close()


@pytest.mark.skipif(shutil.which("agent-browser") is None, reason="agent-browser CLI is required")
def test_analyzer_page_renders_admin_panels_and_backup_action_with_agent_browser(
    dashboard_server: tuple[str, DashboardMockState],
) -> None:
    base_url, state = dashboard_server
    browser = AgentBrowser()

    try:
        _open_admin_page(browser, base_url, "/analyzer.html")

        _wait_until(lambda: _expect_text(browser, "#obs-raw-total", "999"))
        _wait_until(lambda: _expect_count(browser, "#analyzer-history-tbody tr", 1))
        _wait_until(lambda: _expect_count(browser, "#backup-list-tbody tr", 1))
        _wait_until(lambda: _expect_eval(browser, 'document.getElementById("backup-list-tbody").textContent.includes("analytics-20260405-080000.db") ? "yes" : "no"', "yes"))

        browser.run("click", "#backup-create-btn")
        _wait_until(lambda: _expect_eval(browser, 'document.getElementById("toast-container").textContent.includes("备份完成") ? "yes" : "no"', "yes"))

        assert state.latest_hashes("/api/admin/status") == [ADMIN_HASH]
        assert state.latest_hashes("/api/admin/analyzer/history") == [ADMIN_HASH]
        assert state.latest_hashes("/api/admin/backups") == [ADMIN_HASH]
        assert state.latest_hashes("/api/admin/backup") == [ADMIN_HASH]
    finally:
        browser.close()