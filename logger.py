"""
logger.py — Structured logging configuration for the QA framework.

Sets up:
- Console handler (human-readable during local runs)
- JSON file handler (machine-readable for CI artifact ingestion)
- Per-module loggers under the "qa" namespace

Usage in test files:
    from logger import get_logger
    logger = get_logger(__name__)
    logger.info("Fetching price data", extra={"coin": "bitcoin", "currency": "usd"})
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_SESSION_TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
JSON_LOG_PATH = LOG_DIR / f"qa_run_{_SESSION_TIMESTAMP}.json"


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON for structured log ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Include any extra fields passed via extra={...}
        standard_attrs = set(logging.LogRecord("", 0, "", 0, "", [], None).__dict__.keys())
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                log_obj[key] = value

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


def _build_root_logger() -> logging.Logger:
    root = logging.getLogger("qa")
    root.setLevel(LOG_LEVEL)

    if root.handlers:
        return root  # Already configured (e.g. pytest re-import)

    # Console — readable output during test runs
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(LOG_LEVEL)
    console_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(console_handler)

    # JSON file — structured output for CI artifact ingestion
    file_handler = logging.FileHandler(JSON_LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # Always capture everything to file
    file_handler.setFormatter(JSONFormatter())
    root.addHandler(file_handler)

    root.propagate = False
    return root


_root_logger = _build_root_logger()


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger under the qa.* namespace.

    Args:
        name: typically __name__ from the calling module

    Returns:
        Logger instance inheriting qa root config
    """
    # Normalise module names like __main__ or tests.test_market_data
    if not name.startswith("qa"):
        name = f"qa.{name.split('.')[-1]}"
    return logging.getLogger(name)