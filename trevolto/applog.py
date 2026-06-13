"""Rotating debug/support log (stdlib ``logging``).

Writes a size-capped, auto-rotating log to ``DATA_DIR/app.log`` so a user can
attach it to a support ticket when something misbehaves. This is intentionally
separate from the human-readable trade-log CSV: it captures *everything* the
backend logs (plus tracebacks) at a low level, and rotates so it never grows
without bound (``LOG_MAX_BYTES`` × ``LOG_BACKUP_COUNT`` is the hard ceiling).

Usage:
    from applog import get_logger
    get_logger().info("connected to %s", exchange)
    get_logger().exception("trade failed")   # inside an except block
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from config import DEBUG_LOG, LOG_BACKUP_COUNT, LOG_MAX_BYTES

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    """Return the shared rotating logger, creating it on first use."""
    global _logger
    if _logger is not None:
        return _logger
    log = logging.getLogger("trevolto")
    log.setLevel(logging.INFO)
    log.propagate = False
    if not log.handlers:
        try:
            handler = RotatingFileHandler(
                DEBUG_LOG, maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
            log.addHandler(handler)
        except OSError:
            # Disk not writable — degrade to a no-op so logging never crashes.
            log.addHandler(logging.NullHandler())
    _logger = log
    return log
