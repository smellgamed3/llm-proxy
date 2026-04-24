"""
Recorder 异步客户端 — Proxy worker 侧使用，通过 Unix socket 发送记录到 recorder server。

特性：
- 非阻塞发送（fire-and-forget），不影响代理转发主路径
- 自动重连，recorder 不可用时内部缓冲（有界队列）
- 缓冲满时丢弃最旧记录（代理转发 > 数据记录的降级策略）
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

import orjson

logger = logging.getLogger("llm-proxy.recorder-client")

# 默认队列上限：防止 recorder 离线时内存无限增长
DEFAULT_MAX_QUEUE = 10000

# 默认 Unix socket 路径
DEFAULT_SOCKET = "/var/run/llm-proxy/recorder.sock"


class RecorderClient:
    """异步 recorder 客户端，每个 uvicorn worker 持有一个实例。"""

    def __init__(
        self,
        socket_path: str = DEFAULT_SOCKET,
        max_queue: int = DEFAULT_MAX_QUEUE,
    ):
        self._socket_path = socket_path
        self._max_queue = max_queue
        self._queue: deque[bytes] = deque(maxlen=max_queue)
        self._drops: int = 0
        self._running = False
        self._writer: asyncio.StreamWriter | None = None
        self._send_task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动后台发送任务。"""
        self._running = True
        self._send_task = asyncio.create_task(self._send_loop())

    async def stop(self) -> None:
        """优雅关闭：排空队列后断开连接。"""
        self._running = False
        if self._send_task:
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass

        # 排空残余消息
        if self._writer and self._queue:
            try:
                while self._queue:
                    msg = self._queue.popleft()
                    self._writer.write(msg + b"\n")
                await self._writer.drain()
            except Exception:
                pass

        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None

        if self._drops:
            logger.warning("Recorder client: %d messages dropped due to queue overflow", self._drops)

    async def _connect(self) -> asyncio.StreamWriter | None:
        """尝试连接到 recorder server。"""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self._socket_path),
                timeout=3.0,
            )
            # 启动消费任务，读取响应（recorder 不返回数据，仅消费以检测断连）
            asyncio.create_task(self._read_loop(reader))
            return writer
        except Exception:
            return None

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        """消费 recorder 响应（用于检测断连）。"""
        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break
        except Exception:
            pass

    async def _send_loop(self) -> None:
        """后台循环：维持连接并发送队列中的消息。"""
        while self._running:
            if self._writer is None:
                self._writer = await self._connect()
                if self._writer is None:
                    await asyncio.sleep(1.0)  # 重连间隔
                    continue
                # 连接成功，记录队列状态
                qlen = len(self._queue)
                if qlen > 0:
                    logger.debug("Recorder client connected, draining %d buffered messages", qlen)

            try:
                # 先发送队列中的缓冲消息
                while self._queue:
                    msg = self._queue.popleft()
                    self._writer.write(msg + b"\n")
                await self._writer.drain()

                # 等待新消息或定期心跳
                # 使用小间隔轮询，避免完全空闲循环
                await asyncio.sleep(0.5)

                # 检查是否有新消息入队
                if self._queue:
                    continue

            except (ConnectionError, BrokenPipeError, OSError) as e:
                logger.debug("Recorder connection lost: %s", e)
                try:
                    self._writer.close()
                except Exception:
                    pass
                self._writer = None
                await asyncio.sleep(1.0)

    def send(self, cmd: str, data: dict[str, Any]) -> None:
        """非阻塞发送记录请求。

        Args:
            cmd: 命令名（record_request, record_response 等）
            data: 记录数据字典
        """
        try:
            msg = orjson.dumps({"cmd": cmd, "data": data})
        except Exception:
            logger.debug("Failed to serialize recorder message for cmd=%s", cmd)
            return

        self._queue.append(msg)
