from __future__ import annotations

import logging
import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from .config import Config, load_config
from common.logging import configure_logging
from .recorder import Recorder
from .recorder_client import DEFAULT_SOCKET, RecorderClient
from .proxy import ProxyHandler
from .ws import WSProxyHandler

logger = logging.getLogger("llm-proxy")


def create_app(config: Config | None = None) -> Starlette:
    cfg = config or load_config()

    configure_logging(service_name="proxy", level=cfg.log_level)

    logger.info("Starting LLM Proxy")
    logger.info("  Upstream:    %s", cfg.upstream_url)
    logger.info("  Listen:      %s:%d", cfg.listen_host, cfg.listen_port)
    logger.info("  Log dir:     %s", cfg.log_dir)
    if cfg.recording_filter.include:
        logger.info("  Include:     %s", [r.pattern for r in cfg.recording_filter.include])
    if cfg.recording_filter.exclude:
        logger.info("  Exclude:     %s", [r.pattern for r in cfg.recording_filter.exclude])

    socket_path = os.environ.get("RECORDER_SOCKET", DEFAULT_SOCKET)
    recorder_client = RecorderClient(socket_path=socket_path)

    recorder = Recorder(cfg, recorder_client)
    http_proxy = ProxyHandler(cfg, recorder)
    ws_proxy = WSProxyHandler(cfg, recorder)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def http_catch_all(request: Request):
        return await http_proxy.handle(request)

    async def ws_catch_all(websocket: WebSocket):
        await ws_proxy.handle(websocket)

    async def on_startup():
        await recorder_client.start()
        logger.info("LLM Proxy ready (HTTP + WebSocket)")

    async def on_shutdown():
        logger.info("Shutting down LLM Proxy")
        await recorder_client.stop()
        await http_proxy.close()

    HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]

    app = Starlette(
        routes=[
            # Health check (HTTP only, not proxied)
            Route("/health", health, methods=["GET"]),
            # WebSocket catch-all (must come before HTTP routes so Starlette
            # can distinguish WS upgrade requests on the same paths)
            WebSocketRoute("/{path:path}", ws_catch_all),
            WebSocketRoute("/", ws_catch_all),
            # HTTP catch-all
            Route("/{path:path}", http_catch_all, methods=HTTP_METHODS),
            Route("/", http_catch_all, methods=HTTP_METHODS),
        ],
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
    )
    return app
