"""OpenAI Sora helpers for the Nolan agent."""

from __future__ import annotations

import asyncio
import base64
import binascii
import imghdr
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import unquote, urlparse

import requests
from PIL import Image
from agents import RunContextWrapper
from openai import OpenAI, AzureOpenAI
from pydantic import BaseModel, Field

from app.auth.user_context import UserContext, load_user_context_from_env
from app.core.api_urls import build_api_url
from app.services.downloads import DEFAULT_DOWNLOAD_ROOT
from app.services.user_file_storage import get_user_file_storage
from app.services.generated_media_registry import register_generated_attachments


logger = logging.getLogger(__name__)


class GeneratedVideoFile(BaseModel):
    """Metadata about a saved video asset."""

    file_name: str = Field(description="The basename of the generated video file.")
    relative_path: str = Field(description="Relative path from the downloads root.")
    download_url: str = Field(description="API endpoint that serves the video file.")
    mime_type: str = Field(description="Video MIME type as returned by the API.")
    size_bytes: int = Field(description="File size on disk in bytes.")
    duration_seconds: float | None = Field(default=None, description="Length of the clip in seconds.")
    width: int | None = Field(default=None, description="Video width in pixels.")
    height: int | None = Field(default=None, description="Video height in pixels.")


class GenerateVideoResult(BaseModel):
    """Structured response returned to the agent after video generation."""

    prompt: str
    model: str
    video_id: str
    status: str
    video: GeneratedVideoFile
    note: str
    warnings: list[str] = Field(default_factory=list)
    job_metadata: Dict[str, Any] = Field(default_factory=dict)
    reference_image: Optional[str] = Field(
        default=None,
        description="Reference image filename or descriptor used for guided generation.",
    )
    attachments: list[Dict[str, Any]] = Field(
        default_factory=list,
        description="Attachment metadata for downstream chat rendering.",
    )


@dataclass
class _ReferenceImage:
    file_name: str
    content: bytes
    mime_type: str


_CLIENT: OpenAI | AzureOpenAI | None = None
_CLIENT_PROVIDER: str = "openai"
_AZURE_DEPLOYMENT_NAME: Optional[str] = None
_DEFAULT_MODEL = os.getenv("NOLAN_DEFAULT_MODEL") or "sora-2"


def _first_env(*names: str) -> Optional[str]:
    """Return the first populated environment variable from the provided names."""

    for name in names:
        value = os.getenv(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None

_USER_ID_KEYS: Tuple[str, ...] = ("user_id", "auth_user_id", "supabase_user_id", "id", "uid")
_NESTED_USER_KEYS: Tuple[str, ...] = ("user", "profile", "account", "identity", "principal", "actor")

_LAST_VIDEO_ID_CACHE: Dict[str, str] = {}
_LAST_VIDEO_ID_LOCK = threading.Lock()


def _get_openai_client() -> OpenAI:
    """Create or reuse an OpenAI (or Azure OpenAI) client for the Sora API."""

    global _CLIENT, _CLIENT_PROVIDER, _AZURE_DEPLOYMENT_NAME
    if _CLIENT is not None:
        return _CLIENT

    provider_preference = (_first_env("NOLAN_OPENAI_CLIENT", "OPENAI_CLIENT") or "").lower()
    azure_endpoint = _first_env("NOLAN_AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_ENDPOINT")
    azure_key = _first_env("NOLAN_AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_KEY")
    azure_version = _first_env("NOLAN_AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_API_VERSION") or "2025-03-01-preview"
    azure_deployment = _first_env("NOLAN_AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT")

    should_use_azure = provider_preference == "azure" or (
        provider_preference == "" and azure_endpoint and azure_key
    )

    if should_use_azure:
        if not azure_endpoint or not azure_key:
            raise RuntimeError(
                "Azure OpenAI requested for Nolan but AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY are not configured."
            )

        _CLIENT = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=azure_key,
            api_version=azure_version,
        )
        _CLIENT_PROVIDER = "azure"
        _AZURE_DEPLOYMENT_NAME = azure_deployment
        logger.info(
            "Initialised Azure OpenAI client for Nolan",
            extra={
                "azure_endpoint": azure_endpoint,
                "azure_api_version": azure_version,
                "azure_deployment": azure_deployment,
            },
        )
        return _CLIENT

    api_key = _first_env("NOLAN_OPENAI_API_KEY", "OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is required to generate videos.")

    base_url = _first_env("NOLAN_OPENAI_BASE_URL", "OPENAI_BASE_URL")
    if base_url:
        _CLIENT = OpenAI(api_key=api_key, base_url=base_url)
        logger.info(
            "Initialised OpenAI client for Nolan with custom base URL",
            extra={"base_url": base_url},
        )
    else:
        _CLIENT = OpenAI(api_key=api_key)
        logger.info("Initialised standard OpenAI client for Nolan")

    _CLIENT_PROVIDER = "openai"
    _AZURE_DEPLOYMENT_NAME = None
    return _CLIENT


def _resolve_video_model_name(requested_model: Optional[str]) -> tuple[str, str]:
    """Resolve model and deployment based on provider configuration."""

    desired_model = (requested_model or _DEFAULT_MODEL or "").strip() or "sora-2"
    if _CLIENT_PROVIDER == "azure":
        deployment = _AZURE_DEPLOYMENT_NAME or _first_env("NOLAN_AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT")
        if deployment:
            return deployment.strip(), desired_model
    return desired_model, desired_model


def _summarise_sora_exception(exc: Exception) -> str:
    """Return a user-facing message that preserves useful details from the API error."""

    detail = _extract_openai_error_detail(exc)
    if not detail:
        return "OpenAI Sora request failed. Ensure the API key has Sora access and check the logs for details."

    hints: list[str] = []
    if re.search(r"\b(size|resolution|dimension)\b", detail, re.IGNORECASE):
        hints.append("Verify the requested `size` matches the model's supported resolutions.")

    message = f"OpenAI Sora request failed: {detail}"
    if hints:
        message = f"{message} {' '.join(hints)}"
    return message


def _extract_openai_error_detail(exc: Exception) -> str | None:
    """Attempt to pull a concise error message out of an OpenAI client exception."""

    # Prefer any explicit message attribute the SDK sets.
    attr_message = getattr(exc, "message", None)
    if isinstance(attr_message, str):
        sanitised = _sanitize_error_detail(attr_message)
        if sanitised:
            return sanitised

    response = getattr(exc, "response", None)
    if response is not None:
        # HTTP status code improves clarity when we have to fall back to opaque text.
        status_code = getattr(response, "status_code", None)
        json_payload: Dict[str, Any] | None = None

        if hasattr(response, "json"):
            try:
                json_payload = response.json()
            except Exception:  # noqa: BLE001
                logger.debug("Failed to decode Sora error response JSON", exc_info=True)

        if isinstance(json_payload, dict):
            candidates = [
                json_payload.get("error", {}),
                json_payload,
            ]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                for key in ("message", "detail", "error"):
                    value = candidate.get(key)
                    if isinstance(value, str):
                        sanitised = _sanitize_error_detail(value)
                        if sanitised:
                            return sanitised

        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            sanitised = _sanitize_error_detail(text)
            if sanitised:
                if status_code:
                    return f"{sanitised} (HTTP {status_code})"
                return sanitised

        if status_code:
            return f"HTTP {status_code} error from OpenAI Sora."

    stringified = str(exc).strip()
    if stringified:
        sanitised = _sanitize_error_detail(stringified)
        if sanitised:
            return sanitised

    return None


def _sanitize_error_detail(message: str) -> str | None:
    """Normalise whitespace and truncate overly long error messages."""

    normalised = re.sub(r"\s+", " ", message or "").strip()
    if not normalised:
        return None
    if len(normalised) > 400:
        return normalised[:397] + "..."
    return normalised


def _remember_last_video_id(ctx: RunContextWrapper[Any], video_id: str) -> None:
    """Cache the most recently produced video id for quick remix lookups."""

    context_payload = getattr(ctx, "context", {}) or {}
    metadata = context_payload.get("metadata") or {}

    keys: list[str] = []

    thread_id = context_payload.get("thread_id") or metadata.get("thread_id")
    if isinstance(thread_id, str) and thread_id.strip():
        keys.append(f"thread:{thread_id.strip()}")

    session = getattr(ctx, "session", None)
    session_id = getattr(session, "session_id", None)
    if isinstance(session_id, str) and session_id.strip():
        keys.append(f"session:{session_id.strip()}")

    try:
        user_identifier = _extract_user_identifier(ctx)
    except RuntimeError:
        user_identifier = None

    if user_identifier:
        keys.append(f"user:{user_identifier}")

    if not keys:
        return

    with _LAST_VIDEO_ID_LOCK:
        for key in keys:
            _LAST_VIDEO_ID_CACHE[key] = video_id


def _lookup_last_video_id(ctx: RunContextWrapper[Any]) -> Optional[str]:
    """Return the most recently generated video id for this context, if known."""

    context_payload = getattr(ctx, "context", {}) or {}
    metadata = context_payload.get("metadata") or {}

    for candidate in (
        context_payload.get("previous_video_id"),
        metadata.get("previous_video_id"),
        metadata.get("last_video_id"),
        metadata.get("source_video_id"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    keys: list[str] = []

    thread_id = context_payload.get("thread_id") or metadata.get("thread_id")
    if isinstance(thread_id, str) and thread_id.strip():
        keys.append(f"thread:{thread_id.strip()}")

    session = getattr(ctx, "session", None)
    session_id = getattr(session, "session_id", None)
    if isinstance(session_id, str) and session_id.strip():
        keys.append(f"session:{session_id.strip()}")

    try:
        user_identifier = _extract_user_identifier(ctx)
    except RuntimeError:
        user_identifier = None

    if user_identifier:
        keys.append(f"user:{user_identifier}")

    with _LAST_VIDEO_ID_LOCK:
        for key in keys:
            cached = _LAST_VIDEO_ID_CACHE.get(key)
            if isinstance(cached, str) and cached.strip():
                return cached

    return None


async def _lookup_last_video_id_from_session(ctx: RunContextWrapper[Any]) -> Optional[str]:
    """Examine session history to find the last video id when cache miss occurs."""

    session = getattr(ctx, "session", None)
    if session is None or not hasattr(session, "get_items"):
        return None

    try:
        items = await session.get_items(limit=40)
    except Exception as exc:  # noqa: BLE001 - non-fatal diagnostics
        logger.debug("Failed to read session items for remix lookup: %s", exc)
        return None

    if not isinstance(items, list):
        return None

    for entry in reversed(items):
        candidate = _extract_video_id_from_structure(entry)
        if candidate:
            return candidate
    return None


def _extract_video_id_from_structure(data: Any, *, _depth: int = 0) -> Optional[str]:
    """Recursively search a nested structure for a plausible video id."""

    if _depth > 6 or data is None:
        return None

    if isinstance(data, dict):
        for key, value in data.items():
            if key in {"video_id", "id"} and isinstance(value, str) and value.strip():
                return value.strip()
            candidate = _extract_video_id_from_structure(value, _depth=_depth + 1)
            if candidate:
                return candidate
        return None

    if isinstance(data, list):
        for item in reversed(data):
            candidate = _extract_video_id_from_structure(item, _depth=_depth + 1)
            if candidate:
                return candidate
        return None

    if isinstance(data, str) and data.strip().startswith("{"):
        try:
            decoded = json.loads(data)
        except Exception:
            return None
        return _extract_video_id_from_structure(decoded, _depth=_depth + 1)

    return None


def _looks_like_attachment(candidate: Dict[str, Any]) -> bool:
    keys = {
        "uri",
        "url",
        "download_url",
        "relative_path",
        "filename",
        "file_name",
        "name",
        "base64",
        "data",
    }
    return any(key in candidate for key in keys)


def _coerce_attachment_entries(source: Any) -> Iterable[Dict[str, Any]]:
    """Traverse the source and yield attachment-like dictionaries."""

    if source is None:
        return

    queue: list[Any] = [source]

    while queue:
        current = queue.pop(0)
        if current is None:
            continue

        if isinstance(current, str):
            stripped = current.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                queue.append(parsed)
            continue

        if isinstance(current, dict):
            if _looks_like_attachment(current):
                yield current
            for key in ("items", "data", "attachments", "values", "files"):
                nested = current.get(key)
                if nested is not None:
                    queue.append(nested)
            continue

        if isinstance(current, list):
            queue.extend(current)


def _iter_context_attachments(ctx: RunContextWrapper[Any]) -> Iterable[Dict[str, Any]]:
    """Yield attachment dictionaries exposed to the current tool context."""

    context_payload = getattr(ctx, "context", {}) or {}
    metadata = context_payload.get("metadata") or {}
    message_payload = context_payload.get("message") if isinstance(context_payload.get("message"), dict) else {}

    candidate_sources = [
        context_payload.get("attachments"),
        metadata.get("attachments"),
        metadata.get("image_reference"),
        metadata.get("image_references"),
        metadata.get("reference_images"),
        message_payload.get("attachments"),
        message_payload.get("image_reference"),
        message_payload.get("image_references"),
        metadata,
    ]

    seen: set[tuple[str, str, str]] = set()
    for source in candidate_sources:
        for entry in _coerce_attachment_entries(source):
            identifier = (
                str(entry.get("relative_path") or ""),
                str(entry.get("uri") or entry.get("download_url") or entry.get("url") or ""),
                str(entry.get("name") or entry.get("filename") or entry.get("file_name") or ""),
            )
            if identifier in seen:
                continue
            seen.add(identifier)
            yield entry


def _select_context_reference_attachment(
    attachments: Iterable[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Select the first available image attachment deterministically."""

    for index, attachment in enumerate(attachments):
        if not _attachment_is_image(attachment):
            continue
        identifier = _attachment_identifier(attachment)
        logger.info(
            "Selected reference attachment index=%s identifier=%s",
            index,
            identifier,
        )
        print(f"[Nolan] Using attachment #{index} as reference", identifier)
        return attachment

    return None


def _attachment_identifier(candidate: Dict[str, Any]) -> str:
    return str(
        candidate.get("name")
        or candidate.get("filename")
        or candidate.get("title")
        or candidate.get("relative_path")
        or candidate.get("uri")
        or ""
    ).lower()


def _ensure_supported_reference_mime(mime_type: str) -> str:
    """Validate the reference image mime type."""

    supported = {"image/jpeg", "image/png", "image/webp"}
    normalised = mime_type.lower()
    if normalised not in supported:
        raise ValueError("Reference image must be JPEG, PNG, or WebP.")
    return normalised


def _guess_mime_type(file_name: Optional[str], content: Optional[bytes]) -> str:
    """Guess the mime type of the supplied image data."""

    if file_name:
        guessed, _ = mimetypes.guess_type(file_name)
        if guessed:
            return guessed

    if content:
        detected = imghdr.what(None, h=content)
        if detected:
            if detected in {"jpeg", "png", "webp"}:
                return f"image/{detected}"
            if detected == "jpg":
                return "image/jpeg"

    return "image/png"


def _ensure_reference_image_dimensions(
    reference_image: _ReferenceImage,
    target_width: int,
    target_height: int,
) -> _ReferenceImage:
    """Resize the reference image to match the requested video dimensions."""

    try:
        with Image.open(BytesIO(reference_image.content)) as image:
            current_width, current_height = image.size
            if current_width == target_width and current_height == target_height:
                return reference_image

            if current_width == 0 or current_height == 0:
                raise ValueError("Reference image has invalid dimensions.")

            scale = min(target_width / current_width, target_height / current_height)
            resized_width = max(1, int(round(current_width * scale)))
            resized_height = max(1, int(round(current_height * scale)))

            resized = image.resize((resized_width, resized_height), Image.LANCZOS)

            background = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 255))
            offset_x = (target_width - resized_width) // 2
            offset_y = (target_height - resized_height) // 2
            background.paste(resized, (offset_x, offset_y), resized if resized.mode in {"RGBA", "LA"} else None)

            buffer = BytesIO()
            background.convert("RGB").save(buffer, format="PNG")
            buffer.seek(0)
            new_content = buffer.read()
    except Exception:  # noqa: BLE001 - fall back to the original reference on failure
        logger.warning(
            "Failed to resize reference image to %sx%s; using original dimensions",
            target_width,
            target_height,
            exc_info=True,
        )
        return reference_image

    base_name = Path(reference_image.file_name).stem or "reference_image"
    new_file_name = f"{base_name}_{target_width}x{target_height}.png"

    logger.info(
        "Letterboxed reference image to match requested dimensions",
        extra={
            "original_width": current_width,
            "original_height": current_height,
            "target_width": target_width,
            "target_height": target_height,
            "resized_width": resized_width,
            "resized_height": resized_height,
            "file_name": new_file_name,
        },
    )
    print(
        "[Nolan] Letterboxed reference image",
        f"{current_width}x{current_height}",
        "->",
        f"{target_width}x{target_height}",
        f"(content {resized_width}x{resized_height})",
    )

    return _ReferenceImage(
        file_name=new_file_name,
        content=new_content,
        mime_type="image/png",
    )


def _load_reference_image_from_path(
    storage: Any,
    user_context: UserContext,
    relative_path: str,
) -> _ReferenceImage:
    """Fetch image bytes from user storage."""

    if not relative_path:
        raise ValueError("reference_image_path cannot be empty.")

    if storage is None:
        raise RuntimeError("User file storage backend is not configured.")

    try:
        result = storage.retrieve_file(user_context, relative_path)
    except Exception as exc:  # noqa: BLE001 - propagate meaningful message
        raise RuntimeError(f"Unable to retrieve reference image '{relative_path}': {exc}") from exc

    if result.path is not None:
        content = Path(result.path).read_bytes()
    elif result.content is not None:
        content = bytes(result.content)
    else:
        raise RuntimeError("Reference image file had no content to read.")

    file_name = result.filename or Path(relative_path).name or "reference_image"
    mime_type = _ensure_supported_reference_mime(_guess_mime_type(file_name, content))
    logger.debug(
        "Loaded reference image from storage",
        extra={
            "relative_path": relative_path,
            "file_name": file_name,
            "size_bytes": len(content),
        },
    )
    print(
        "[Nolan] Loaded reference image",
        relative_path,
        file_name,
        len(content),
    )
    return _ReferenceImage(file_name=file_name, content=content, mime_type=mime_type)


def _load_reference_image_from_base64(
    data: str,
    mime_type_hint: Optional[str],
) -> _ReferenceImage:
    """Decode base64-encoded image content."""

    if not data:
        raise ValueError("reference_image_base64 cannot be empty.")

    stripped = data.strip()
    header_mime: Optional[str] = None

    if stripped.startswith("data:"):
        header, _, body = stripped.partition(",")
        if ";base64" not in header:
            raise ValueError("Data URI for reference image must be base64 encoded.")
        header_mime = header[5:].split(";")[0] or None
        stripped = body

    try:
        content = base64.b64decode(stripped, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("reference_image_base64 must be valid base64 data.") from exc

    resolved_mime = mime_type_hint or header_mime or _guess_mime_type(None, content)
    mime_type = _ensure_supported_reference_mime(resolved_mime)
    extension = mimetypes.guess_extension(mime_type) or ".png"
    file_name = f"reference_image{extension}"
    return _ReferenceImage(file_name=file_name, content=content, mime_type=mime_type)


def _load_reference_image_from_url(
    url: str,
    file_name_hint: Optional[str],
    mime_type_hint: Optional[str],
) -> _ReferenceImage:
    """Download a reference image over HTTP(S)."""

    if not url or not isinstance(url, str):
        raise ValueError("Reference image URL cannot be empty.")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Reference image URL must use HTTP or HTTPS.")

    try:
        response = requests.get(url, timeout=(5, 30))
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Unable to fetch reference image from '{url}': {exc}") from exc

    content = response.content or b""
    if not content:
        raise RuntimeError(f"Reference image URL '{url}' returned no content.")

    header_mime = response.headers.get("Content-Type")
    header_mime = header_mime.split(";", 1)[0].strip() if isinstance(header_mime, str) and header_mime.strip() else None

    filename_candidate = file_name_hint or unquote(Path(parsed.path).name)
    if not filename_candidate or filename_candidate in {".", ".."}:
        filename_candidate = "reference_image"

    mime_candidates = [
        mime_type_hint,
        header_mime,
        _guess_mime_type(filename_candidate, content),
    ]

    resolved_mime: Optional[str] = None
    for candidate in mime_candidates:
        if not candidate:
            continue
        try:
            resolved_mime = _ensure_supported_reference_mime(candidate)
            break
        except ValueError:
            continue

    if resolved_mime is None:
        raise ValueError("Downloaded reference image must be JPEG, PNG, or WebP.")

    if "." not in filename_candidate:
        extension = ".jpg" if resolved_mime == "image/jpeg" else mimetypes.guess_extension(resolved_mime) or ".png"
        filename_candidate = f"{filename_candidate}{extension}"

    logger.debug(
        "Downloaded reference image from URL",
        extra={"url": url, "file_name": filename_candidate, "mime_type": resolved_mime, "size_bytes": len(content)},
    )
    print("[Nolan] Downloaded reference image from URL", url, filename_candidate, resolved_mime)

    return _ReferenceImage(file_name=filename_candidate, content=content, mime_type=resolved_mime)


def _extract_relative_path_from_uri(uri: str) -> Optional[str]:
    """Extract a relative download path from an API download URI."""

    if not uri:
        return None

    for marker in ("/v1/files/download/", "/api/v1/files/download/"):
        if marker in uri:
            _, _, tail = uri.partition(marker)
            return tail.lstrip("/") or None

    # If the URI already looks like a relative path, return it cautiously.
    if uri.startswith("/"):
        return uri.lstrip("/")
    if not uri.startswith("http"):
        return uri
    return None


def _resolve_reference_image(
    ctx: RunContextWrapper[Any],
    *,
    user_context: Optional[UserContext],
    storage: Any,
) -> tuple[Optional[_ReferenceImage], list[str]]:
    """Return a resolved reference image and any warnings for the caller."""

    warnings: list[str] = []
    context_attachments = list(_iter_context_attachments(ctx))

    try:
        attachment_summaries = []
        for att in context_attachments:
            if not isinstance(att, dict):
                continue
            attachment_summaries.append(
                {
                    "name": att.get("name") or att.get("filename") or att.get("title"),
                    "relative_path": att.get("relative_path"),
                    "uri": att.get("uri"),
                    "mime_type": att.get("mime_type") or att.get("type"),
                    "is_image": _attachment_is_image(att),
                }
            )
        logger.info(
            "Reference image resolution context",
            extra={
                "attachment_count": len(context_attachments),
                "attachments": attachment_summaries,
            },
        )
        print(
            "[Nolan] Reference image context attachments:",
            {
                "count": len(context_attachments),
                "attachments": attachment_summaries,
            },
        )
    except Exception:
        logger.warning("Unable to summarize context attachments", exc_info=True)

    attachment = _select_context_reference_attachment(context_attachments)
    if not attachment:
        logger.info("No image attachments available to use as a reference")
        return None, warnings

    inline_base64 = attachment.get("base64") or attachment.get("data")
    if isinstance(inline_base64, str) and inline_base64.strip():
        try:
            return (
                _load_reference_image_from_base64(
                    inline_base64,
                    attachment.get("mime_type") or attachment.get("type"),
                ),
                warnings,
            )
        except ValueError as exc:
            warnings.append(f"Failed to decode inline reference image: {exc}")
            logger.warning("Inline reference image decode failed", exc_info=True)

    relative_path = attachment.get("relative_path")
    if isinstance(relative_path, str) and relative_path.strip():
        if user_context is None or not getattr(user_context, "user_id", None):
            warnings.append("A reference image attachment was provided but no authenticated user context is available.")
            return None, warnings
        try:
            print(f"[Nolan] Loading reference from relative path {relative_path}")
            return _load_reference_image_from_path(storage, user_context, relative_path.strip()), warnings
        except Exception as exc:  # noqa: BLE001 - communicate failure gracefully
            warnings.append(f"Failed to load attached reference image '{relative_path}': {exc}")
            logger.warning(
                "Failed to load reference image from relative path",
                exc_info=True,
                extra={"relative_path": relative_path},
            )
            return None, warnings

    uri = attachment.get("uri") or attachment.get("download_url") or attachment.get("url")
    if isinstance(uri, str):
        relative_candidate = _extract_relative_path_from_uri(uri)
        if relative_candidate:
            try:
                print(f"[Nolan] Derived relative path {relative_candidate} from URI {uri}")
                if user_context is None or not getattr(user_context, "user_id", None):
                    warnings.append("A reference image attachment requires an authenticated user context to download.")
                    return None, warnings
                return _load_reference_image_from_path(storage, user_context, relative_candidate), warnings
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Failed to load reference image from '{uri}': {exc}")
                logger.warning(
                    "Failed to load reference image from URI",
                    exc_info=True,
                    extra={"uri": uri, "relative_candidate": relative_candidate},
                )
                return None, warnings
        parsed = urlparse(uri)
        if parsed.scheme in {"http", "https"}:
            try:
                return (
                    _load_reference_image_from_url(
                        uri,
                        attachment.get("name") or attachment.get("filename") or attachment.get("file_name"),
                        attachment.get("mime_type") or attachment.get("type"),
                    ),
                    warnings,
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Failed to download reference image from '{uri}': {exc}")
                logger.warning(
                    "Failed to download reference image from URL",
                    exc_info=True,
                    extra={"uri": uri},
                )
                return None, warnings

    warnings.append("Reference image attachment did not include a relative path that could be downloaded.")
    logger.info(
        "Reference attachment missing downloadable path name=%s uri=%s",
        attachment.get("name") or attachment.get("filename"),
        attachment.get("uri"),
    )
    print(
        "[Nolan] Reference attachment missing downloadable path",
        attachment.get("name") or attachment.get("filename"),
        attachment.get("uri"),
    )
    return None, warnings


def _finalize_video_job(
    ctx: RunContextWrapper[Any],
    *,
    client: OpenAI,
    video_job: Any,
    prompt: str,
    model_name: str,
    api_model_name: str,
    client_provider: str,
    status_notifier: _ChatStatusNotifier,
    user_context: Optional[UserContext],
    storage: Any,
    initial_warnings: list[str],
    reference_image: Optional[_ReferenceImage],
) -> GenerateVideoResult:
    """Download, persist, and describe a completed Sora video job."""

    status_notifier.send(stage="render_complete", message="Rendering finished, preparing download…")

    status = getattr(video_job, "status", None) or (video_job.get("status") if isinstance(video_job, dict) else None)
    if status != "completed":
        raise RuntimeError(f"Video generation did not complete successfully (status={status}).")

    video_id = getattr(video_job, "id", None) or (video_job.get("id") if isinstance(video_job, dict) else None)
    if not video_id:
        raise RuntimeError("OpenAI Sora did not return a video id.")

    if user_context and user_context.user_id:
        user_id = user_context.user_id
    else:
        user_id = _extract_user_identifier(ctx)

    try:
        storage_backend = storage if storage is not None else get_user_file_storage()
    except Exception:
        storage_backend = None

    output_directory, keep_local = _resolve_output_directory(user_id, storage_backend, user_context)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    file_name = f"nolan_{timestamp}.mp4"
    file_path = output_directory / file_name

    try:
        status_notifier.send(stage="downloading", message="Downloading video from Sora…")
        content = client.videos.download_content(video_id, variant="video")
        content.write_to_file(str(file_path))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to download video content: %s", exc)
        status_notifier.send(
            status="agent_processing_error",
            stage="error",
            message="Unable to download generated video content from Sora.",
        )
        raise RuntimeError("Unable to download generated video content from Sora.") from exc

    size_bytes = file_path.stat().st_size
    width, height = _parse_size(getattr(video_job, "size", None) or (video_job.get("size") if isinstance(video_job, dict) else None))
    duration_seconds = _sanitize_seconds(
        getattr(video_job, "seconds", None) or (video_job.get("seconds") if isinstance(video_job, dict) else None)
    )

    relative_path = file_name
    download_url = f"/v1/files/download/{relative_path}"
    mime_type = "video/mp4"
    storage_backend_id = (getattr(storage_backend, "backend_id", None) or "").lower()

    if (
        storage_backend is not None
        and user_context is not None
        and storage_backend_id != "local"
    ):
        status_notifier.send(stage="uploading", message="Uploading video to storage…")
        try:
            saved_info = storage_backend.save_file(  # type: ignore[call-arg]
                user_context,
                file_name=file_name,
                content=file_path.read_bytes(),
                mime_type=mime_type,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to persist generated video via storage backend: %s", exc)
            status_notifier.send(
                status="agent_processing_error",
                stage="error",
                message="Unable to upload generated video to storage.",
            )
            raise RuntimeError("Unable to persist generated video to storage backend.") from exc

        if saved_info:
            relative_path = saved_info.relative_path
            size_bytes = saved_info.size
            download_url = f"/v1/files/download/{relative_path}"

        if not keep_local:
            try:
                file_path.unlink()
            except FileNotFoundError:
                pass

    if not keep_local:
        shutil.rmtree(output_directory, ignore_errors=True)

    video_file = GeneratedVideoFile(
        file_name=file_name,
        relative_path=relative_path,
        download_url=download_url,
        mime_type=mime_type,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        width=width,
        height=height,
    )
    attachments_payload = [
        {
            "uri": f"/v1/files/download/{video_file.relative_path}",
            "download_url": video_file.download_url,
            "relative_path": video_file.relative_path,
            "name": video_file.file_name,
            "type": video_file.mime_type,
            "size_bytes": video_file.size_bytes,
            "duration": video_file.duration_seconds,
            "width": video_file.width,
            "height": video_file.height,
        }
    ]

    context_payload = getattr(ctx, "context", {}) or {}
    job_id = context_payload.get("job_id")
    register_generated_attachments(job_id, attachments_payload)

    if keep_local:
        note = f"Saved video to files/users/{user_id}"
    else:
        backend_label = _describe_storage_backend(storage_backend) if storage_backend is not None else "remote"
        note = f"Uploaded video to {backend_label} storage for user {user_id}"

    if reference_image:
        note = f"{note} (guided by {reference_image.file_name})"

    status_notifier.send(stage="finalizing", message="Finalising video response…")

    logger.debug(
        "OpenAI Sora produced a video; stored_locally=%s video_id=%s provider=%s api_model=%s reported_model=%s",
        keep_local,
        video_id,
        client_provider,
        api_model_name,
        model_name,
    )

    _remember_last_video_id(ctx, video_id)

    warnings = list(initial_warnings)
    job_metadata = _prepare_job_metadata(video_job)
    job_metadata.setdefault("requested_model", model_name)
    job_metadata.setdefault("api_model", api_model_name)
    job_metadata.setdefault("provider", client_provider)
    if client_provider == "azure":
        job_metadata.setdefault("azure_deployment", api_model_name)

    return GenerateVideoResult(
        prompt=prompt,
        model=model_name,
        video_id=video_id,
        status=status or "completed",
        video=video_file,
        note=note,
        warnings=warnings,
        job_metadata=job_metadata,
        reference_image=reference_image.file_name if reference_image else None,
        attachments=attachments_payload,
    )


class _ChatStatusNotifier:
    """Helper to push chat status updates so the UI can show long-running progress."""

    def __init__(self, ctx: RunContextWrapper[Any], agent_key: str = "nolan") -> None:
        context_payload = getattr(ctx, "context", {}) or {}
        metadata = context_payload.get("metadata") or {}

        self._thread_id = context_payload.get("thread_id") or metadata.get("thread_id")
        self._job_id = metadata.get("job_id")
        self._agent_key = agent_key
        self._chat_status_url = build_api_url("v1", "internal", "chat-status")

        try:
            self._user_id = _extract_user_identifier(ctx)
        except Exception:  # noqa: BLE001 - missing user context should not fail generation
            self._user_id = None

        self._enabled = bool(self._thread_id and self._user_id)
        self._session = requests.Session() if self._enabled else None

    def send(
        self,
        *,
        status: str = "agent_typing",
        stage: Optional[str] = None,
        message: Optional[str] = None,
        progress: Optional[float] = None,
    ) -> bool:
        if not self._enabled or self._session is None or not self._user_id or not self._thread_id:
            return False

        payload: Dict[str, Any] = {
            "agent_key": self._agent_key,
            "user_id": self._user_id,
            "thread_id": self._thread_id,
            "status": status,
        }
        if self._job_id:
            payload["job_id"] = self._job_id
        if stage:
            payload["stage"] = stage
        if message:
            payload["status_message"] = message
        if progress is not None:
            try:
                payload["progress"] = float(progress)
            except (TypeError, ValueError):
                pass

        try:
            response = self._session.post(self._chat_status_url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 - status updates should never interrupt generation
            logger.debug("Failed to push chat status update: %s", exc)
            return False

    @contextmanager
    def heartbeat(
        self,
        *,
        stage: str,
        message: Optional[str] = None,
        interval_seconds: float = 20.0,
    ):
        """Send recurring status updates until the context exits."""

        if not self._enabled:
            yield
            return

        stop_event = threading.Event()

        def _loop() -> None:
            while not stop_event.wait(interval_seconds):
                self.send(stage=stage, message=message)

        # Send an immediate update so the UI refreshes without waiting for the first interval.
        self.send(stage=stage, message=message)
        thread = threading.Thread(
            target=_loop,
            name=f"{self._agent_key}-status-heartbeat",
            daemon=True,
        )
        thread.start()

        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=1.0)


def _storage_requires_temporary_directory(storage: Any) -> bool:
    return bool(getattr(storage, "requires_temporary_directory", False))


def _describe_storage_backend(storage: Any) -> str:
    backend_id = (getattr(storage, "backend_id", None) or "").lower()
    if backend_id == "supabase":
        return "Supabase"
    if backend_id == "s3":
        return "S3"
    if backend_id == "local":
        return "local filesystem"
    name = storage.__class__.__name__.replace("Backend", "")
    return name or "storage"


def _resolve_output_directory(
    user_id: str,
    storage: Any,
    user_context: Optional[UserContext],
) -> Tuple[Path, bool]:
    """Determine where generated assets should be stored and whether to persist locally."""

    if storage is not None and _storage_requires_temporary_directory(storage):
        if user_context is None or not user_context.user_id:
            raise RuntimeError(
                "The configured storage backend requires an authenticated user context with a valid user_id.",
            )
        temp_dir = Path(tempfile.mkdtemp(prefix=f"nolan_{user_id}_"))
        logger.debug("Using temporary directory for remote storage upload: %s", temp_dir)
        return temp_dir, False

    if user_context and getattr(user_context, "get_download_dir", None):
        directory = Path(user_context.get_download_dir())
    else:
        directory = DEFAULT_DOWNLOAD_ROOT / "users" / user_id

    directory.mkdir(parents=True, exist_ok=True)
    logger.debug("Using local download directory: %s", directory)
    return directory, True


def _extract_user_identifier(ctx: RunContextWrapper[Any]) -> str:
    user_context = _resolve_user_context(ctx)
    if user_context and getattr(user_context, "user_id", None):
        return str(user_context.user_id)

    for source in (
        getattr(ctx, "context", None),
        getattr(ctx, "user_context", None),
        getattr(ctx, "metadata", None),
        getattr(ctx, "state", None),
        getattr(ctx, "session", None),
        ctx,
    ):
        candidate = _extract_user_id_from_source(source)
        if candidate:
            return candidate

    for env_var in ("USER_CONTEXT_USER_ID", "AUTH_USER_ID", "SUPABASE_USER_ID", "USER_ID"):
        env_candidate = os.getenv(env_var)
        if env_candidate:
            return env_candidate

    raise RuntimeError(
        "Unable to determine user identity for video generation; ensure USER_ID or auth context is provided.",
    )


def _extract_user_id_from_source(source: Any, *, _depth: int = 0) -> Optional[str]:
    if source is None or _depth > 4:
        return None

    if isinstance(source, str) and source.strip():
        return source.strip()

    if isinstance(source, dict):
        for key in _USER_ID_KEYS:
            raw = source.get(key)
            if raw:
                return str(raw)
        for nested_key in _NESTED_USER_KEYS:
            nested = source.get(nested_key)
            candidate = _extract_user_id_from_source(nested, _depth=_depth + 1)
            if candidate:
                return candidate
        return None

    for key in _USER_ID_KEYS:
        raw = getattr(source, key, None)
        if raw:
            return str(raw)

    for nested_attr in _NESTED_USER_KEYS:
        nested = getattr(source, nested_attr, None)
        candidate = _extract_user_id_from_source(nested, _depth=_depth + 1)
        if candidate:
            return candidate

    return None


def _resolve_user_context(ctx: RunContextWrapper[Any]) -> Optional[UserContext]:
    candidate = getattr(ctx, "context", None)
    if isinstance(candidate, UserContext):
        return candidate

    candidate = getattr(ctx, "user_context", None)
    if isinstance(candidate, UserContext):
        return candidate

    candidate_dict = None
    for possible in (getattr(ctx, "context", None), getattr(ctx, "user_context", None), getattr(ctx, "metadata", None)):
        if isinstance(possible, dict):
            candidate_dict = possible
            break

    if candidate_dict:
        user_id = _extract_user_id_from_source(candidate_dict)
        if user_id:
            try:
                return UserContext(
                    user_id=user_id,
                    display_name=str(candidate_dict.get("display_name") or candidate_dict.get("name") or "Unknown User"),
                    email=candidate_dict.get("email"),
                    enabled_agents=candidate_dict.get("enabled_agents") or [],
                    agent_configs=candidate_dict.get("agent_configs") or {},
                    timezone=str(candidate_dict.get("timezone") or "UTC"),
                )
            except TypeError:
                pass

    try:
        env_context = load_user_context_from_env()
    except Exception:
        env_context = None
    return env_context


def _parse_size(size_value: Any) -> tuple[int | None, int | None]:
    if not size_value:
        return None, None

    if isinstance(size_value, str):
        match = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", size_value)
        if match:
            return int(match.group(1)), int(match.group(2))
        return None, None

    if isinstance(size_value, (tuple, list)) and len(size_value) == 2:
        try:
            width = int(size_value[0])
            height = int(size_value[1])
            return width, height
        except (TypeError, ValueError):
            return None, None

    return None, None


def _sanitize_seconds(seconds_value: Any) -> float | None:
    if seconds_value is None:
        return None
    try:
        return float(seconds_value)
    except (TypeError, ValueError):
        return None


def _prepare_job_metadata(video_job: Any) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    for key in ("id", "status", "model", "progress", "seconds", "size", "created_at", "completed_at", "failure_reason"):
        value = None
        if isinstance(video_job, dict):
            value = video_job.get(key)
        else:
            value = getattr(video_job, key, None)
        if value is not None:
            metadata[key] = value
    return metadata


def _generate_video_impl(
    ctx: RunContextWrapper[Any],
    *,
    prompt: str,
    model: Optional[str],
    size: Optional[str],
    seconds: Optional[int],
) -> GenerateVideoResult:
    client = _get_openai_client()
    resolved_model_name, reported_model_name = _resolve_video_model_name(model)

    warnings: list[str] = []
    status_notifier = _ChatStatusNotifier(ctx)

    request_payload: Dict[str, Any] = {
        "model": resolved_model_name,
        "prompt": prompt,
    }

    if size:
        if not re.match(r"^\d+x\d+$", size.strip()):
            raise ValueError("size must be formatted as WIDTHxHEIGHT, for example 1280x720.")
        request_payload["size"] = size.strip()

    if seconds is not None:
        try:
            seconds_int = int(seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("seconds must be an integer value when provided.") from exc

        if seconds_int <= 0:
            raise ValueError("seconds must be a positive integer when provided.")
        if seconds_int > 120:
            warnings.append("Requested duration exceeds 120 seconds; the API may reject long renders.")
        request_payload["seconds"] = str(seconds_int)

    user_context = _resolve_user_context(ctx)
    try:
        storage = get_user_file_storage()
    except Exception:
        storage = None

    reference_image, reference_warnings = _resolve_reference_image(
        ctx,
        user_context=user_context,
        storage=storage,
    )
    warnings.extend(reference_warnings)

    if reference_image:
        target_width, target_height = _parse_size(request_payload.get("size"))
        if target_width and target_height:
            reference_image = _ensure_reference_image_dimensions(reference_image, target_width, target_height)

        logger.info(
            "Using reference image for video generation",
            extra={
                "file_name": reference_image.file_name,
                "mime_type": reference_image.mime_type,
            },
        )
        print(
            "[Nolan] Using reference image",
            reference_image.file_name,
            reference_image.mime_type,
        )
    else:
        logger.info(
            "No reference image resolved",
            extra={
                "warnings": reference_warnings,
            },
        )
        print(
            "[Nolan] No reference image resolved",
            reference_warnings,
        )

    if reference_image:
        request_payload["input_reference"] = (
            reference_image.file_name,
            reference_image.content,
            reference_image.mime_type,
        )

    logger.debug(
        "Invoking OpenAI Sora model=%s api_model=%s size=%s seconds=%s reference_image=%s provider=%s",
        reported_model_name,
        resolved_model_name,
        request_payload.get("size"),
        request_payload.get("seconds"),
        bool(reference_image),
        _CLIENT_PROVIDER,
    )

    status_notifier.send(stage="preparing", message="Submitting request to Sora…")

    try:
        with status_notifier.heartbeat(stage="rendering", message="Rendering video…"):
            video_job = client.videos.create_and_poll(**request_payload)
    except Exception as exc:  # noqa: BLE001 - external API errors should preserve context
        error_message = _summarise_sora_exception(exc)
        status_notifier.send(status="agent_processing_error", stage="error", message=error_message)
        logger.exception("Sora request failed: %s", exc)
        raise RuntimeError(error_message) from exc

    return _finalize_video_job(
        ctx,
        client=client,
        video_job=video_job,
        prompt=prompt,
        model_name=reported_model_name,
        api_model_name=resolved_model_name,
        client_provider=_CLIENT_PROVIDER,
        status_notifier=status_notifier,
        user_context=user_context,
        storage=storage,
        initial_warnings=warnings,
        reference_image=reference_image,
    )


async def generate_video_tool(
    ctx: RunContextWrapper[Any],
    *,
    prompt: str,
    model: Optional[str] = None,
    size: Optional[str] = None,
    seconds: Optional[int] = None,
) -> GenerateVideoResult:
    """Generate videos via OpenAI Sora and persist them to the downloads directory."""

    return await asyncio.to_thread(
        _generate_video_impl,
        ctx,
        prompt=prompt,
        model=model,
        size=size,
        seconds=seconds,
    )


def _remix_video_impl(
    ctx: RunContextWrapper[Any],
    *,
    prompt: str,
    source_video_id: str,
) -> GenerateVideoResult:
    client = _get_openai_client()
    status_notifier = _ChatStatusNotifier(ctx)

    logger.debug(
        "Invoking OpenAI Sora remix source_video_id=%s",
        source_video_id,
    )

    status_notifier.send(stage="preparing", message="Submitting remix request to Sora…")

    try:
        with status_notifier.heartbeat(stage="remixing", message="Applying remix adjustments…"):
            remix_job = client.videos.remix(source_video_id, prompt=prompt)
            video_job = client.videos.poll(remix_job.id)
    except Exception as exc:  # noqa: BLE001
        error_message = _summarise_sora_exception(exc)
        status_notifier.send(status="agent_processing_error", stage="error", message=error_message)
        logger.exception("Sora remix request failed: %s", exc)
        raise RuntimeError(error_message) from exc

    user_context = _resolve_user_context(ctx)
    try:
        storage = get_user_file_storage()
    except Exception:
        storage = None

    provider_model = getattr(video_job, "model", None)
    if provider_model is None and isinstance(video_job, dict):
        provider_model = video_job.get("model")
    provider_model_name = provider_model.strip() if isinstance(provider_model, str) else None
    model_name = provider_model_name or "sora-2"

    return _finalize_video_job(
        ctx,
        client=client,
        video_job=video_job,
        prompt=prompt,
        model_name=model_name,
        api_model_name=provider_model_name or model_name,
        client_provider=_CLIENT_PROVIDER,
        status_notifier=status_notifier,
        user_context=user_context,
        storage=storage,
        initial_warnings=[],
        reference_image=None,
    )


async def remix_video_tool(
    ctx: RunContextWrapper[Any],
    *,
    prompt: str,
    source_video_id: Optional[str] = None,
) -> GenerateVideoResult:
    """Remix a previously generated video using OpenAI Sora."""

    cleaned_prompt = prompt.strip()
    if not cleaned_prompt:
        raise ValueError("prompt is required for remix_video.")

    provided_video_id = source_video_id.strip() if isinstance(source_video_id, str) else None
    resolved_video_id = provided_video_id or _lookup_last_video_id(ctx)
    if not resolved_video_id:
        resolved_video_id = await _lookup_last_video_id_from_session(ctx)

    if not resolved_video_id:
        raise ValueError(
            "source_video_id is required unless a previous Nolan video exists in this thread."
        )

    return await asyncio.to_thread(
        _remix_video_impl,
        ctx,
        prompt=cleaned_prompt,
        source_video_id=resolved_video_id,
    )


__all__ = [
    "GenerateVideoResult",
    "GeneratedVideoFile",
    "generate_video_tool",
    "remix_video_tool",
    "_extract_user_identifier",
]
def _attachment_is_image(attachment: Dict[str, Any]) -> bool:
    mime_value = str(attachment.get("mime_type") or attachment.get("type") or "").lower()
    if mime_value.startswith("image/"):
        return True

    candidate_name = (
        attachment.get("name")
        or attachment.get("filename")
        or attachment.get("title")
        or attachment.get("relative_path")
        or attachment.get("uri")
        or ""
    )
    candidate_name = str(candidate_name)
    return bool(re.search(r"\.(png|jpe?g|webp)$", candidate_name, re.IGNORECASE))
