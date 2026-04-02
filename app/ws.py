from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import TYPE_CHECKING

import websockets
import websockets.exceptions
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

if TYPE_CHECKING:
    from .config import Config
    from .recorder import Recorder

logger = logging.getLogger("llm-proxy.ws")

# Headers that must not be forwarded during WS handshake
WS_SKIP_HEADERS = frozenset({
    "host", "connection", "upgrade",
    "sec-websocket-key", "sec-websocket-version",
    "sec-websocket-extensions", "sec-websocket-accept",
})


def _upstream_ws_url(upstream_http_url: str, path: str, query_string: str) -> str:
    """Convert upstream HTTP URL to WebSocket URL."""
    base = upstream_http_url.rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    url = f"{ws_base}{path}"
    if query_string:
        url += f"?{query_string}"
    return url


class WSProxyHandler:
    """Bidirectional WebSocket proxy with optional recording."""

    def __init__(self, config: "Config", recorder: "Recorder"):
        self.config = config
        self.recorder = recorder

    async def handle(self, websocket: WebSocket) -> None:
        path = websocket.url.path
        query_string = websocket.url.query or ""

        should_record = self.config.recording_filter.should_record(path)

        # Build forwarding headers (strip WS handshake headers)
        fwd_headers = [
            (k, v)
            for k, v in websocket.headers.items()
            if k.lower() not in WS_SKIP_HEADERS
        ]

        # Extract subprotocols requested by client
        subprotocols_header = websocket.headers.get("sec-websocket-protocol", "")
        subprotocols = (
            [s.strip() for s in subprotocols_header.split(",")]
            if subprotocols_header else []
        )

        upstream_url = _upstream_ws_url(self.config.upstream_url, path, query_string)
        conn_id = self.recorder.new_request_id()
        start = time.monotonic()

        logger.info("WS connect %s -> %s", conn_id[:8], upstream_url)

        try:
            async with websockets.connect(
                upstream_url,
                additional_headers=fwd_headers,
                subprotocols=subprotocols or None,
                open_timeout=10,
                close_timeout=5,
            ) as upstream_ws:
                # Accept client with negotiated subprotocol
                negotiated = upstream_ws.subprotocol
                await websocket.accept(subprotocol=negotiated)

                if should_record:
                    self.recorder.record_ws_connect(
                        conn_id=conn_id,
                        path=path,
                        query_string=query_string,
                        headers=dict(websocket.headers),
                        subprotocol=negotiated,
                    )

                # Bridge both directions concurrently
                await asyncio.gather(
                    self._client_to_upstream(conn_id, websocket, upstream_ws, should_record),
                    self._upstream_to_client(conn_id, websocket, upstream_ws, should_record),
                    return_exceptions=True,
                )

        except websockets.exceptions.InvalidURI as e:
            logger.error("WS invalid upstream URI %s: %s", upstream_url, e)
            await websocket.close(code=1011, reason="invalid upstream URI")
            return
        except (websockets.exceptions.WebSocketException, OSError) as e:
            logger.error("WS upstream connect failed %s: %s", conn_id[:8], e)
            if websocket.client_state == WebSocketState.CONNECTING:
                await websocket.accept()
            await websocket.close(code=1014, reason="upstream unavailable")
            return
        except WebSocketDisconnect:
            pass  # Client disconnected before upstream connected
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            if should_record:
                self.recorder.record_ws_close(conn_id, duration_ms)
            logger.info("WS closed %s (%.1fms)", conn_id[:8], duration_ms)

    async def _client_to_upstream(
        self,
        conn_id: str,
        client_ws: WebSocket,
        upstream_ws: websockets.ClientConnection,
        should_record: bool,
    ) -> None:
        """Forward messages from client → upstream."""
        try:
            while True:
                msg = await client_ws.receive()
                if msg["type"] == "websocket.disconnect":
                    await upstream_ws.close()
                    break

                if msg.get("text") is not None:
                    data = msg["text"]
                    await upstream_ws.send(data)
                    if should_record:
                        self.recorder.record_ws_message(
                            conn_id, "client_to_server", "text", data,
                            self.config.max_body_log_size,
                        )
                elif msg.get("bytes") is not None:
                    data = msg["bytes"]
                    await upstream_ws.send(data)
                    if should_record:
                        self.recorder.record_ws_message(
                            conn_id, "client_to_server", "binary", data,
                            self.config.max_body_log_size,
                        )
        except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
            pass
        except Exception as e:
            logger.debug("WS client→upstream error %s: %s", conn_id[:8], e)

    async def _upstream_to_client(
        self,
        conn_id: str,
        client_ws: WebSocket,
        upstream_ws: websockets.ClientConnection,
        should_record: bool,
    ) -> None:
        """Forward messages from upstream → client."""
        try:
            async for message in upstream_ws:
                if isinstance(message, str):
                    await client_ws.send_text(message)
                    if should_record:
                        self.recorder.record_ws_message(
                            conn_id, "server_to_client", "text", message,
                            self.config.max_body_log_size,
                        )
                else:
                    await client_ws.send_bytes(message)
                    if should_record:
                        self.recorder.record_ws_message(
                            conn_id, "server_to_client", "binary", message,
                            self.config.max_body_log_size,
                        )
        except websockets.exceptions.ConnectionClosed:
            pass
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("WS upstream→client error %s: %s", conn_id[:8], e)
        finally:
            # Ensure client connection is closed when upstream closes
            if client_ws.client_state not in (
                WebSocketState.DISCONNECTED,
            ):
                try:
                    await client_ws.close()
                except Exception:
                    pass
