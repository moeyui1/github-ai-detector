"""
Logging setup for GitHub AI-Radar.

Controlled by the environment variable LOG_LEVEL:
  - unset or ""      → INFO level (default)
  - "debug"          → DEBUG level
  - "off" / "false"  → logging disabled (NullHandler only)

Logs are always written to both logs/ai-radar.log and the console (stderr).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_FILE = _LOG_DIR / "ai-radar.log"

_ENV_VAR = os.environ.get("LOG_LEVEL", "").strip().lower()
_DISABLED = _ENV_VAR in ("off", "false", "0")
_LEVEL = logging.DEBUG if _ENV_VAR == "debug" else logging.INFO


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name."""
    logger = logging.getLogger(f"ai_radar.{name}")

    if _DISABLED:
        logger.addHandler(logging.NullHandler())
        return logger

    # Avoid adding duplicate handlers on Streamlit hot-reloads
    if logger.handlers:
        return logger

    logger.setLevel(_LEVEL)

    _LOG_DIR.mkdir(exist_ok=True)

    fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    fh.setLevel(_LEVEL)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Also output to console (stderr)
    sh = logging.StreamHandler()
    sh.setLevel(_LEVEL)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger
