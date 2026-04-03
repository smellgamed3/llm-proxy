"""CLI entry point: python -m analyzer"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import load_analyzer_config
from .worker import AnalyzerWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM Proxy Analyzer Worker")
    parser.add_argument(
        "--mode", choices=["incremental", "full", "range"], default="incremental"
    )
    parser.add_argument("--since", help="Start date for range mode (ISO 8601)")
    parser.add_argument("--until", help="End date for range mode (ISO 8601)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    config = load_analyzer_config()
    config.mode = args.mode
    if args.since:
        config.since = args.since
    if args.until:
        config.until = args.until

    worker = AnalyzerWorker(config)
    worker.run()


if __name__ == "__main__":
    main()
