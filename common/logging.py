from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import TextIO


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service_name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    *,
    service_name: str,
    level: str = "INFO",
    log_format: str | None = None,
    stream: TextIO | None = None,
) -> None:
    resolved_format = (log_format or os.getenv("LOG_FORMAT", "text")).strip().lower()
    resolved_level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(stream or sys.stdout)
    if resolved_format == "json":
        handler.setFormatter(JsonFormatter(service_name))
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(name)s] %(levelname)s %(message)s",
            )
        )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(resolved_level)
