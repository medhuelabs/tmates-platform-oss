"""Lightweight logging helper replacing the legacy ``logs`` package."""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from logging.config import dictConfig
from typing import Any, Dict

_LOGGER = logging.getLogger("tmates")


class UTCFormatter(logging.Formatter):
    """Formatter that renders timestamps in UTC."""

    converter = time.gmtime


_DEFAULT_LOGGING_CONFIG: Dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "tmates": {
            "()": "app.logger.UTCFormatter",
            "format": "%(asctime)sZ | %(levelname)s | %(name)s | %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "tmates",
        }
    },
    "loggers": {
        "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "httpx": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "tmates": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

_CONFIGURED = False


def configure_logging(*, level: str | int | None = None) -> None:
    """Apply a shared logging configuration for the API, worker, and CLI."""

    global _CONFIGURED

    if _CONFIGURED:
        return

    logging_config = deepcopy(_DEFAULT_LOGGING_CONFIG)
    if level is not None:
        logging_config["root"]["level"] = level
        logging_config["loggers"]["tmates"]["level"] = level

    dictConfig(logging_config)
    _CONFIGURED = True


def _ensure_configured() -> None:
    if not _CONFIGURED:
        configure_logging()


def _coerce(parts: tuple[object, ...]) -> str:
    rendered = " ".join(str(part) for part in parts if part is not None)
    return rendered.strip()


def log(*parts: object, **metadata: Any) -> None:
    """
    Emit an info-level log message and gracefully ignore legacy kwargs.

    Previous agents passed custom keywords such as ``agent`` or ``feed``.
    We accept arbitrary metadata for backwards compatibility, append it to
    the message, and forward everything through the standard logging stack.
    """

    message = _coerce(parts)
    if metadata:
        message = f"{message} | {metadata}"

    _ensure_configured()

    _LOGGER.info(message)


__all__ = ["configure_logging", "log"]
