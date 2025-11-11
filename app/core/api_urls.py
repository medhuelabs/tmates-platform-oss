"""Helpers for constructing API URLs that respect the configured base path."""

from __future__ import annotations

import logging
import os
from typing import Iterable


def get_api_base_url() -> str:
    """Return the configured API base URL without a trailing slash."""
    raw = (os.getenv("API_BASE_URL") or "http://api:8000").strip()
    base = raw.rstrip("/")

    for suffix in ("/api/v1", "/v1", "/api"):
        if base.endswith(suffix):
            logging.getLogger(__name__).warning(
                "API_BASE_URL should not include '%s'; normalizing value %s",
                suffix,
                raw,
            )
            base = base[: -len(suffix)]
            break

    return base or "http://api:8000"


def build_api_url(*segments: Iterable[str] | str) -> str:
    """
    Join the configured API base URL with the provided path segments.

    Each segment is stripped of leading/trailing slashes to avoid duplicate
    separators. Empty or falsy segments are ignored.
    """
    base = get_api_base_url()

    parts: list[str] = []
    for segment in segments:
        if not segment:
            continue
        if isinstance(segment, str):
            parts.append(segment.strip("/"))
        else:
            parts.extend(str(item).strip("/") for item in segment if item)

    if not parts:
        return base

    return f"{base}/{'/'.join(parts)}"
