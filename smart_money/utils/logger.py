from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional


def _setup_root_logger(log_file: str, log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(level)
    root.addHandler(sh)

    # Rotating file handler
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    fh.setLevel(level)
    root.addHandler(fh)

    root.setLevel(level)


def init_logging() -> None:
    """Call once at startup after settings are loaded."""
    try:
        from config.settings import get_settings

        s = get_settings()
        _setup_root_logger(s.log_file, s.log_level)
    except Exception:
        _setup_root_logger("logs/smart_money.log", "INFO")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
