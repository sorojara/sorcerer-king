"""
game/logger.py — Centralised logging configuration
====================================================

Call ``setup_logging()`` once at process start (main.py).

After that, every module gets a named logger via::

    from game.logger import get_logger
    log = get_logger(__name__)

Output
------
* ``logs/game.log`` — rotating file, DEBUG level, full detail.
* stderr          — WARNING and above only (keeps the terminal clean).

Format (file)::
    2024-01-01 12:00:00.123 [DEBUG   ] game.core.rules       | message

The log directory is created automatically next to cwd.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


# ── Log file location ─────────────────────────────────────────────────────────
_LOG_DIR  = Path.cwd() / "logs"
_LOG_FILE = _LOG_DIR / "game.log"

# ── Formatters ────────────────────────────────────────────────────────────────
_FILE_FMT   = "%(asctime)s.%(msecs)03d [%(levelname)-8s] %(name)-28s | %(message)s"
_STDERR_FMT = "[%(levelname)-8s] %(name)-20s | %(message)s"
_DATE_FMT   = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.DEBUG) -> None:
    """
    Configure the root logger.  Safe to call multiple times (idempotent).

    ``level`` — minimum level written to the log file (default: DEBUG).
    stderr always shows WARNING+.
    """
    root = logging.getLogger("game")
    if root.handlers:
        return  # already configured

    root.setLevel(level)

    # ── File handler (rotating, max 5 MB × 3 backups) ─────────────────────
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        _LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(_FILE_FMT, datefmt=_DATE_FMT))
    root.addHandler(fh)

    # ── Stderr handler (warnings only — keeps terminal clean) ─────────────
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(logging.Formatter(_STDERR_FMT))
    root.addHandler(sh)


def get_logger(name: str) -> logging.Logger:
    """
    Return a child logger under the ``game`` namespace.

    Usage::
        log = get_logger(__name__)
        log.debug("something happened")

    ``name`` is typically ``__name__`` which produces loggers like
    ``game.core.rules``, ``game.mechanics.effects._meta``, etc.
    """
    # Strip leading package prefix if the module is already under 'game.'
    return logging.getLogger(name if name.startswith("game") else f"game.{name}")
