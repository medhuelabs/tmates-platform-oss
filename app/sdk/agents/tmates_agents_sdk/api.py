"""API adapter helpers for session-aware agent executions."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import mimetypes
from typing import Any, Dict, List, Optional, Sequence

from app.auth import UserContext
from app.services.generated_media_registry import consume_generated_attachments
from app.services.session_manager import session_manager
from app.services.user_file_storage import get_user_file_storage, StorageError

from app.sdk.agents.tmates_agents_sdk.types import ContextBuilder, RunPromptCallable


logger = logging.getLogger(__name__)

_VISION_ATTACHMENT_LIMIT = 3
_SUPPORTED_VISION_MIME = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}
_DOWNLOAD_URI_MARKERS = (
    "/api/files/download/",
    "/api/v1/files/download/",
    "/v1/files/download/",
    "/files/download/",
)


def _ensure_event_loop() -> tuple[asyncio.AbstractEventLoop, bool, Optional[asyncio.AbstractEventLoop]]:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            return new_loop, True, loop
        return loop, False, None
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        return new_loop, True, None


def _normalize_mime(candidate: Optional[str]) -> Optional[str]:
    if not candidate:
        return None
    lowered = candidate.strip().lower()
    if not lowered:
        return None
    if lowered == "image/jpg":
        lowered = "image/jpeg"
    if lowered in _SUPPORTED_VISION_MIME:
        return lowered
    return None


def _extract_relative_path(entry: Dict[str, Any]) -> Optional[str]:
    relative_path = entry.get("relative_path")
    if isinstance(relative_path, str) and relative_path.strip():
        return relative_path.strip()

    uri = entry.get("uri") or entry.get("download_url") or entry.get("url")
    if not isinstance(uri, str):
        return None
    for marker in _DOWNLOAD_URI_MARKERS:
        if marker in uri:
            _, _, tail = uri.partition(marker)
            return tail.lstrip("/") or None
    return None


def _attachment_inline_data(entry: Dict[str, Any]) -> Optional[str]:
    uri = entry.get("uri")
    if isinstance(uri, str) and uri.strip().startswith("data:"):
        return uri.strip()

    inline_field = entry.get("base64") or entry.get("data")
    if not isinstance(inline_field, str):
        return None
    inline = inline_field.strip()
    if not inline:
        return None

    mime_type = _normalize_mime(entry.get("mime_type") or entry.get("type")) or "image/png"
    try:
        base64.b64decode(inline, validate=True)
    except binascii.Error:
        return None
    return f"data:{mime_type};base64,{inline}"


def _build_data_url(content: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _attachment_data_url(
    entry: Dict[str, Any],
    user_context: UserContext,
    storage,
) -> Optional[str]:
    inline = _attachment_inline_data(entry)
    if inline:
        return inline

    relative_path = _extract_relative_path(entry)
    if not relative_path:
        return None

    try:
        result = storage.retrieve_file(user_context, relative_path)
    except StorageError as exc:
        logger.warning("Failed to load attachment for vision", exc_info=True, extra={"reason": str(exc)})
        return None

    content = result.content
    if content is None and result.path is not None:
        try:
            content = result.path.read_bytes()
        except OSError as exc:  # pragma: no cover - filesystem edge
            logger.warning("Unable to read attachment file", exc_info=True, extra={"reason": str(exc)})
            return None

    if not content:
        return None

    mime_type = _normalize_mime(entry.get("mime_type") or entry.get("type"))
    if not mime_type and result.filename:
        guessed, _ = mimetypes.guess_type(result.filename)
        mime_type = _normalize_mime(guessed)

    if not mime_type:
        return None

    return _build_data_url(content, mime_type)


def _prepare_vision_inputs(
    attachments: Sequence[Dict[str, Any]],
    user_context: UserContext,
) -> List[Dict[str, Any]]:
    prepared: List[Dict[str, Any]] = []
    storage = get_user_file_storage()

    for entry in attachments:
        if len(prepared) >= _VISION_ATTACHMENT_LIMIT:
            break
        if not isinstance(entry, dict):
            continue
        data_url = _attachment_data_url(entry, user_context, storage)
        if not data_url:
            continue
        part: Dict[str, Any] = {"type": "input_image", "image_url": data_url}
        detail_value = entry.get("detail")
        if isinstance(detail_value, str):
            cleaned = detail_value.strip().lower()
            if cleaned in {"low", "high", "auto"}:
                part["detail"] = cleaned
        prepared.append(part)

    if attachments and not prepared:
        logger.debug("Vision attachments were provided but none were usable")

    return prepared


def run_agent_api_request(
    *,
    agent_key: str,
    author_name: str,
    request: Dict[str, Any],
    user_context: Optional[UserContext],
    run_prompt: RunPromptCallable,
    include_generated_attachments: bool = False,
    context_builder: Optional[ContextBuilder] = None,
    vision_enabled: bool = False,
) -> Dict[str, Any]:
    """Common API handler wired for tmates agents."""

    message = request.get("message", "")
    thread_id = request.get("thread_id")
    author = request.get("author", "User")
    provided_session_id = request.get("session_id")
    request_attachments = request.get("attachments") or (request.get("metadata") or {}).get("attachments")

    if not user_context or not getattr(user_context, "user_id", None):
        return {
            "success": False,
            "error": "User context required for session management",
            "error_type": "AuthenticationError",
            "thread_id": thread_id,
            "author": author_name,
        }

    try:
        session_id = session_manager.get_or_create_session(
            user_context=user_context,
            thread_id=thread_id or "default",
            agent_key=agent_key,
            provided_session_id=provided_session_id,
        )

        user_id = user_context.user_id

        def _build_context() -> Optional[Dict[str, Any]]:
            if context_builder is None:
                return None
            return context_builder(request, user_id, session_id)

        prepared_vision_inputs: Optional[List[Dict[str, Any]]] = None
        if vision_enabled and isinstance(request_attachments, list):
            prepared_vision_inputs = _prepare_vision_inputs(request_attachments, user_context)
            if not prepared_vision_inputs:
                prepared_vision_inputs = None

        async def _run_async(vision_inputs: Optional[List[Dict[str, Any]]]) -> str:
            return await run_prompt(
                message,
                user_id,
                session_id,
                context=_build_context(),
                attachments=vision_inputs,
            )

        loop, should_close, previous_loop = _ensure_event_loop()

        try:
            response_text = loop.run_until_complete(_run_async(prepared_vision_inputs))
        except Exception as exc:  # pragma: no cover - defensive logging
            return {
                "success": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "thread_id": thread_id,
                "author": author_name,
                "session_id": provided_session_id,
            }
        finally:
            if should_close:
                if previous_loop is not None:
                    asyncio.set_event_loop(previous_loop)
                    loop.close()

        session_manager.update_session_activity(session_id)

        attachments = []
        if include_generated_attachments:
            metadata = request.get("metadata") or {}
            attachments = consume_generated_attachments(metadata.get("job_id"))

        response: Dict[str, Any] = {
            "success": True,
            "response": response_text,
            "thread_id": thread_id,
            "author": author_name,
            "session_id": session_id,
            "metadata": {
                "agent_key": agent_key,
                "processing_method": "api",
                "session_created": provided_session_id != session_id,
            },
        }

        if attachments:
            response["attachments"] = attachments

        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        return {
            "success": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "thread_id": thread_id,
            "author": author_name,
            "session_id": provided_session_id,
        }
