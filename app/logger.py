"""Lightweight logging helper replacing the legacy ``logs`` package."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger("tmates")


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

    if not _LOGGER.handlers:
        logging.basicConfig(level=logging.INFO)

    _LOGGER.info(message)


__all__ = ["log"]
