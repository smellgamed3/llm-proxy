"""Multi-process parallel record processing for the analyzer.

Architecture:
  Main process (Fetcher + Writer)
    - fetches batches from raw.db
    - batch-reads body files (I/O)
    - dispatches CPU work to a process pool
    - batch-writes results to analytics.db

  Worker pool (N processes)
    - JSON parsing of request/response bodies
    - extractor selection + extraction
    - cost calculation
    - fingerprinting
    - returns processed result dicts (no I/O side-effects)
"""

from __future__ import annotations

import hashlib
import logging
import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

import orjson

logger = logging.getLogger("analyzer.parallel")

# ---------------------------------------------------------------------------
# Per-process worker state (initialised once via _init_worker)
# ---------------------------------------------------------------------------
_extractors: list | None = None
_cost_calculator: Any = None
_fingerprinter: Any = None


def _init_worker(pricing_file: str) -> None:
    """Initialise heavy objects once per worker process."""
    global _extractors, _cost_calculator, _fingerprinter

    from analyzer.cost import CostCalculator
    from analyzer.extractors.anthropic import AnthropicExtractor
    from analyzer.extractors.generic import GenericExtractor
    from analyzer.extractors.openai_compat import OpenAICompatExtractor
    from analyzer.fingerprint import Fingerprinter

    _extractors = [
        OpenAICompatExtractor(),
        AnthropicExtractor(),
        GenericExtractor(),
    ]
    _cost_calculator = CostCalculator(pricing_file)
    _fingerprinter = Fingerprinter()


# ---------------------------------------------------------------------------
# Pure-compute function executed in worker processes
# ---------------------------------------------------------------------------


def _process_record_in_worker(
    args: tuple[dict, str | None, str | None],
) -> dict[str, Any] | None:
    """Process a single record using per-process state.

    ``args`` = ``(record, request_body, response_body)``.
    Bodies are pre-read by the main process (serial I/O avoids
    Docker FUSE volume contention from concurrent worker I/O).
    Returns a dict with keys ``conv_data``, ``template_info``, ``date``
    or *None* on error.
    """
    try:
        record, request_body, response_body = args
        return process_record_cpu(
            record,
            request_body,
            response_body,
            _extractors,  # type: ignore[arg-type]
            _cost_calculator,
            _fingerprinter,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Worker error for record %s: %s", args[0].get("id", "?"), exc)
        return None


def process_record_cpu(
    record: dict,
    request_body: str | None,
    response_body: str | None,
    extractors: list,
    cost_calculator: Any,
    fingerprinter: Any,
) -> dict[str, Any]:
    """Pure CPU processing of a single record — no I/O side-effects.

    Returns ``{"conv_data": dict, "template_info": tuple|None, "date": str}``.
    """
    # Parse request headers
    request_headers: dict = {}
    raw_headers = record.get("request_headers")
    if raw_headers:
        try:
            request_headers = orjson.loads(raw_headers)
        except Exception:
            pass

    # Select extractor
    path = record.get("path", "")
    method = record.get("method", "")
    extractor = extractors[-1]  # GenericExtractor fallback
    for ext in extractors:
        if ext.can_handle(path, method, request_headers):
            extractor = ext
            break

    result = extractor.extract(record, request_body, response_body)

    # Cost
    cost = cost_calculator.calculate(
        result.model, result.prompt_tokens, result.completion_tokens
    )

    # Fingerprint
    template_id = fingerprinter.fingerprint(result.system_prompt)

    # API key hash
    api_key_hash = record.get("api_key_hash")
    if not api_key_hash:
        auth = (
            request_headers.get("authorization")
            or request_headers.get("Authorization")
            or ""
        )
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
        else:
            key = (
                request_headers.get("x-api-key")
                or request_headers.get("X-Api-Key")
                or ""
            ).strip()
        if key:
            api_key_hash = hashlib.sha256(key.encode()).hexdigest()[:32]

    # Build conversation data
    timestamp = record.get("timestamp", "")
    date = timestamp[:10] if timestamp else ""

    tools_list_json = (
        orjson.dumps(result.tools_list).decode() if result.tools_list else None
    )

    conv_data: dict = {
        "id": record["id"],
        "seq": record.get("seq"),
        "timestamp": timestamp,
        "path": path,
        "method": method,
        "provider": result.provider,
        "model": result.model,
        "request_type": result.request_type,
        "status": result.status,
        "error_type": result.error_type,
        "error_message": result.error_message,
        "status_code": record.get("status_code"),
        "is_stream": record.get("is_stream", 0),
        "duration_ms": record.get("duration_ms"),
        "client_ip": record.get("client_ip"),
        "upstream_url": record.get("upstream_url"),
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "cost_usd": cost,
        "template_id": template_id,
        "finish_reason": result.finish_reason,
        "has_tools": 1 if result.has_tools else 0,
        "tools_list": tools_list_json,
        "messages_count": result.messages_count,
        "temperature": result.temperature,
        "max_tokens": result.max_tokens,
        "system_prompt": result.system_prompt,
        "user_prompt": result.user_prompt,
        "assistant_response": result.assistant_response,
        "api_key_hash": api_key_hash,
    }

    template_info = None
    if template_id and result.system_prompt:
        template_info = (template_id, result.system_prompt, timestamp, cost)

    return {"conv_data": conv_data, "template_info": template_info, "date": date}


# ---------------------------------------------------------------------------
# Pool manager
# ---------------------------------------------------------------------------


def _cpu_count_cgroup() -> int | None:
    """Read CPU quota from cgroup v1/v2 (respects Docker --cpus limit)."""
    for path in (
        "/sys/fs/cgroup/cpu.max",              # cgroup v2
        "/sys/fs/cgroup/cpu/cpu.cfs_quota_us",  # cgroup v1 quota
    ):
        try:
            with open(path) as f:
                content = f.read().strip()
        except OSError:
            continue
        if path.endswith("cpu.max"):
            parts = content.split()
            if parts and parts[0] in ("max", "-1"):
                return None  # no CPU limit set
            quota = int(parts[0])
            period = int(parts[1]) if len(parts) > 1 else 100000
            if quota > 0:
                return max(1, int(quota / period))
        else:
            quota = int(content)
            if quota <= 0:
                return None
            try:
                with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us") as pf:
                    period = int(pf.read().strip()) or 100000
            except OSError:
                period = 100000
            return max(1, int(quota / period))
    return None


def _cpu_count() -> int:
    """CPU count respecting Docker container cgroup limits."""
    return _cpu_count_cgroup() or os.cpu_count() or 2


def resolve_num_workers(configured: int) -> int:
    """Resolve the effective number of worker processes.

    * ``0`` → auto: ``max(1, cpu_count - 1)``, respecting container limits
    * ``1`` → single-process mode (no pool)
    * ``N`` → exactly N workers
    """
    if configured == 1:
        return 1
    if configured <= 0:
        cpu = _cpu_count()
        return max(1, cpu - 1)
    return configured


class ParallelProcessor:
    """Manages a ``ProcessPoolExecutor`` for CPU-bound record processing."""

    def __init__(self, num_workers: int, pricing_file: str):
        self.num_workers = num_workers
        self.pricing_file = pricing_file
        self._pool: ProcessPoolExecutor | None = None

    def _ensure_pool(self) -> ProcessPoolExecutor:
        if self._pool is None:
            ctx = mp.get_context("spawn")
            self._pool = ProcessPoolExecutor(
                max_workers=self.num_workers,
                mp_context=ctx,
                initializer=_init_worker,
                initargs=(self.pricing_file,),
            )
        return self._pool

    def process_batch(
        self,
        tasks: list[tuple[dict, str | None, str | None]],
    ) -> list[dict[str, Any] | None]:
        """提交任务到进程池处理，使用分块提交控制内存。

        将任务分成多个 chunk 依次提交，每轮最多有 ``pool_size * 2`` 个
        Future 同时在内存中，避免大批量（5000）时一次性创建所有 Future
        导致内存压力。
        """
        pool = self._ensure_pool()
        chunk_size = self.num_workers * 2
        results: list[dict[str, Any] | None] = [None] * len(tasks)

        for start in range(0, len(tasks), chunk_size):
            chunk = tasks[start:start + chunk_size]
            indexed: dict = {}
            for i, t in enumerate(chunk):
                actual_idx = start + i
                fut = pool.submit(_process_record_in_worker, t)
                indexed[fut] = actual_idx

            for fut in as_completed(indexed):
                idx = indexed[fut]
                try:
                    results[idx] = fut.result()
                except Exception as exc:
                    logger.error("Future %d failed: %s", idx, exc)

        return results

    def submit_batch(
        self,
        tasks: list[tuple[dict, str | None, str | None]],
    ) -> list:
        """Non-blocking: 提交所有任务到进程池，立即返回 future 列表。

        配合 ``collect_results()`` 使用，实现 body I/O 与 CPU 的 pipelining：
        ``submit_batch(N) → read_bodies(N+1) → collect_results(N) → write_DB(N)``
        """
        pool = self._ensure_pool()
        chunk_size = self.num_workers * 2
        indexed: dict = {}
        for start in range(0, len(tasks), chunk_size):
            chunk = tasks[start:start + chunk_size]
            for i, t in enumerate(chunk):
                actual_idx = start + i
                fut = pool.submit(_process_record_in_worker, t)
                indexed[fut] = actual_idx
        return indexed

    @staticmethod
    def collect_results(
        indexed: dict,
        num_tasks: int,
    ) -> list[dict[str, Any] | None]:
        """收集 ``submit_batch`` 提交的 future 结果。

        Args:
            indexed: ``submit_batch`` 返回的 ``{future: index}`` 映射。
            num_tasks: 原始任务总数。
        """
        results: list[dict[str, Any] | None] = [None] * num_tasks
        for fut in as_completed(indexed):
            idx = indexed[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                logger.error("Future %d failed: %s", idx, exc)
        return results

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None
