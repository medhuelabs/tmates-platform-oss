"""Thread-safe registry for storing agent-generated media attachments per job."""

from __future__ import annotations

from threading import Lock
from typing import Any, Dict, List
import logging

_REGISTRY: Dict[str, List[Dict[str, Any]]] = {}
_LOCK = Lock()
logger = logging.getLogger(__name__)


def register_generated_attachments(job_id: str | None, attachments: List[Dict[str, Any]] | None) -> None:
    """Register attachments for the given job identifier."""

    if not job_id or not attachments:
        return

    with _LOCK:
        bucket = _REGISTRY.setdefault(job_id, [])
        bucket.extend(attachments)
        logger.debug("[Registry] Registered attachments for %s: %s", job_id, attachments)


def consume_generated_attachments(job_id: str | None) -> List[Dict[str, Any]]:
    """Return and clear attachments registered for the job identifier."""

    if not job_id:
        return []

    with _LOCK:
        attachments = _REGISTRY.pop(job_id, [])
        if attachments:
            logger.debug("[Registry] Consumed attachments for %s: %s", job_id, attachments)
        return attachments


def clear_generated_attachments() -> None:
    """Remove all registered attachments."""

    with _LOCK:
        _REGISTRY.clear()
