"""Gemini Imagen helpers for the Leonardo agent."""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

from agents import RunContextWrapper
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.services.downloads import DEFAULT_DOWNLOAD_ROOT
from app.services.user_file_storage import get_user_file_storage
from app.auth.user_context import UserContext, load_user_context_from_env
from app.services.generated_media_registry import register_generated_attachments


logger = logging.getLogger(__name__)


AspectRatio = Literal["1:1", "3:4", "4:3", "9:16", "16:9"]
ImageSize = Literal["1K", "2K"]
PersonGenerationPolicy = Literal["dont_allow", "allow_adult", "allow_all"]

SUPPORTED_ASPECT_RATIOS: Tuple[AspectRatio, ...] = ("1:1", "3:4", "4:3", "9:16", "16:9")
SUPPORTED_IMAGE_SIZES: Tuple[ImageSize, ...] = ("1K", "2K")
SUPPORTED_PERSON_POLICIES: Tuple[PersonGenerationPolicy, ...] = (
    "dont_allow",
    "allow_adult",
    "allow_all",
)

MAX_IMAGES_PER_REQUEST = 4

# Imagen 4 standard/ultra models allow image_size parameter.
_MODELS_SUPPORTING_IMAGE_SIZE_PREFIXES: Tuple[str, ...] = ("imagen-4.", "imagen-4-", "imagen-4")

class GeneratedImageFile(BaseModel):
    """Metadata about a saved image asset."""

    file_name: str = Field(description="The basename of the generated image file.")
    relative_path: str = Field(description="Relative path from the downloads root.")
    download_url: str = Field(description="API endpoint that serves the image file.")
    mime_type: str = Field(description="Image MIME type as returned by the API.")
    size_bytes: int = Field(description="File size on disk in bytes.")
    enhanced_prompt: str | None = Field(
        default=None,
        description="The model-enhanced prompt, when available.",
    )
    rai_filtered_reason: str | None = Field(
        default=None,
        description="Reason why the image may have been filtered for safety.",
    )
    safety_labels: List[dict[str, float | str]] = Field(
        default_factory=list,
        description="Safety categories and scores attached to the image.",
    )


class GenerateImageResult(BaseModel):
    """Structured response returned to the agent after image generation."""

    prompt: str
    model: str
    count: int
    images: List[GeneratedImageFile]
    note: str
    warnings: List[str] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Attachment metadata for downstream chat rendering.",
    )


_CLIENT: genai.Client | None = None

_USER_ID_KEYS: Tuple[str, ...] = ("user_id", "auth_user_id", "supabase_user_id", "id", "uid")
_NESTED_USER_KEYS: Tuple[str, ...] = ("user", "profile", "account", "identity", "principal", "actor")


def _get_genai_client() -> genai.Client:
    """Create or reuse a GenAI client for the Gemini Imagen API."""

    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY environment variable is required to generate images.")

    logger.debug("Initialising Gemini client for Imagen requests")
    _CLIENT = genai.Client(api_key=api_key)
    return _CLIENT


def _summarise_imagen_exception(exc: Exception) -> str:
    """Return a user-facing message while preserving useful error details from Imagen."""

    detail = _extract_imagen_error_detail(exc)
    if not detail:
        return (
            "Gemini Imagen request failed. Ensure the API key has Imagen access and quotas, "
            "and check the logs for full details."
        )

    hints: List[str] = []
    if re.search(r"\b(aspect|ratio|size|dimension)\b", detail, re.IGNORECASE):
        hints.append("Verify the requested aspect_ratio and image_size are supported by the chosen model.")
    if re.search(r"\bquota|limit\b", detail, re.IGNORECASE):
        hints.append("You may need to review your Gemini project quotas or rate limits.")

    message = f"Gemini Imagen request failed: {detail}"
    if hints:
        message = f"{message} {' '.join(hints)}"
    return message


def _extract_imagen_error_detail(exc: Exception) -> str | None:
    """Attempt to extract a concise detail string from a Gemini Imagen exception."""

    attr_message = getattr(exc, "message", None)
    if isinstance(attr_message, str):
        sanitised = _sanitize_error_detail(attr_message)
        if sanitised:
            return sanitised

    if isinstance(getattr(exc, "details", None), str):
        sanitised = _sanitize_error_detail(exc.details)  # type: ignore[attr-defined]
        if sanitised:
            return sanitised

    details_obj = getattr(exc, "details", None)
    if isinstance(details_obj, (list, tuple)):
        for item in details_obj:
            if isinstance(item, str):
                sanitised = _sanitize_error_detail(item)
                if sanitised:
                    return sanitised
            elif isinstance(item, dict):
                for key in ("message", "detail", "reason", "status"):
                    value = item.get(key)
                    if isinstance(value, str):
                        sanitised = _sanitize_error_detail(value)
                        if sanitised:
                            return sanitised

    response = getattr(exc, "response", None) or getattr(exc, "http_response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None) or getattr(response, "status", None)
        json_payload: Dict[str, Any] | None = None

        if hasattr(response, "json"):
            try:
                json_payload = response.json()
            except Exception:  # noqa: BLE001
                logger.debug("Failed to decode Imagen error response JSON", exc_info=True)

        if isinstance(json_payload, dict):
            for key in ("message", "detail", "error", "error_message"):
                value = json_payload.get(key)
                if isinstance(value, str):
                    sanitised = _sanitize_error_detail(value)
                    if sanitised:
                        return sanitised
            error_obj = json_payload.get("error")
            if isinstance(error_obj, dict):
                for key in ("message", "status", "reason"):
                    value = error_obj.get(key)
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
            return f"HTTP {status_code} error from Gemini Imagen."

    for arg in getattr(exc, "args", ()):
        if isinstance(arg, str):
            sanitised = _sanitize_error_detail(arg)
            if sanitised:
                return sanitised
        elif isinstance(arg, dict):
            for key in ("message", "detail", "reason", "status"):
                value = arg.get(key)
                if isinstance(value, str):
                    sanitised = _sanitize_error_detail(value)
                    if sanitised:
                        return sanitised

    stringified = str(exc).strip()
    if stringified:
        sanitised = _sanitize_error_detail(stringified)
        if sanitised:
            return sanitised

    return None


def _sanitize_error_detail(message: str) -> str | None:
    """Normalise whitespace and cap the length of provider error messages."""

    normalised = re.sub(r"\s+", " ", message or "").strip()
    if not normalised:
        return None
    if len(normalised) > 400:
        return normalised[:397] + "..."
    return normalised


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
        temp_dir = Path(tempfile.mkdtemp(prefix=f"leo_{user_id}_"))
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
        "Unable to determine user identity for image generation; ensure USER_ID or auth context is provided.",
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


def _has_valid_reference_image(prompt_image_base64: Optional[str]) -> bool:
    if not prompt_image_base64:
        return False

    if isinstance(prompt_image_base64, (bytes, bytearray)):
        return True

    try:
        base64.b64decode(prompt_image_base64)
    except (ValueError, TypeError) as exc:  # noqa: PERF203 - clarity matters here
        raise ValueError("prompt_img must be valid base64-encoded data.") from exc

    return True


def _model_supports_image_size(model_name: str) -> bool:
    model_name = model_name or ""
    return any(model_name.startswith(prefix) for prefix in _MODELS_SUPPORTING_IMAGE_SIZE_PREFIXES)


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

    # Attempt to load from environment variables populated by the dispatcher
    try:
        env_context = load_user_context_from_env()
    except Exception:  # noqa: BLE001 - environment loading should not break generation
        env_context = None
    return env_context


def _collect_safety_labels(attributes: Optional[types.SafetyAttributes]) -> List[dict[str, float | str]]:
    if not attributes:
        return []

    categories = list(attributes.categories or [])
    scores = list(attributes.scores or [])

    labels: List[dict[str, float | str]] = []
    for index, category in enumerate(categories):
        if not category:
            continue
        score = scores[index] if index < len(scores) else None
        labels.append(
            {
                "category": category,
                "score": float(score) if score is not None else None,
            }
        )
    return labels


def _save_generated_images(
    generated_images: Iterable[types.GeneratedImage],
    output_directory: Path,
    *,
    storage: Any,
    user_context: Optional[UserContext],
    keep_local: bool,
) -> List[GeneratedImageFile]:
    saved: List[GeneratedImageFile] = []
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    storage_backend_id = (getattr(storage, "backend_id", None) or "").lower()
    can_use_backend = storage is not None and user_context is not None

    for index, item in enumerate(generated_images, start=1):
        image = item.image
        if not image or not image.image_bytes:
            continue

        mime_type = image.mime_type or "image/png"
        extension = mimetypes.guess_extension(mime_type) or ".png"
        file_name = f"leo_{timestamp}_{index}{extension}"
        file_path = output_directory / file_name
        file_path.write_bytes(image.image_bytes)

        relative_path = file_name
        download_url = f"/v1/files/download/{relative_path}"
        safety_labels = _collect_safety_labels(item.safety_attributes)
        size_bytes = file_path.stat().st_size

        if can_use_backend and storage_backend_id != "local":
            try:
                saved_info = storage.save_file(  # type: ignore[call-arg]
                    user_context,
                    file_name=file_name,
                    content=image.image_bytes,
                    mime_type=mime_type,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to persist generated image via storage backend: %s", exc)
                raise RuntimeError("Unable to persist generated image to storage backend.") from exc

            if saved_info:
                relative_path = saved_info.relative_path
                size_bytes = saved_info.size
                download_url = f"/v1/files/download/{relative_path}"

            if not keep_local:
                try:
                    file_path.unlink()
                except FileNotFoundError:
                    pass

        saved.append(
            GeneratedImageFile(
                file_name=file_name,
                relative_path=relative_path,
                download_url=download_url,
                mime_type=mime_type,
                size_bytes=size_bytes,
                enhanced_prompt=item.enhanced_prompt,
                rai_filtered_reason=item.rai_filtered_reason,
                safety_labels=safety_labels,
            )
        )

    return saved


def _generate_images_impl(
    ctx: RunContextWrapper[Any],
    *,
    prompt: str,
    model: Optional[str],
    prompt_image_base64: Optional[str],
    aspect_ratio: Optional[str],
    image_size: Optional[str],
    number_of_images: int,
    person_generation: Optional[str],
) -> GenerateImageResult:
    client = _get_genai_client()
    model_name = model or "imagen-3.0-generate-002"

    warnings: List[str] = []
    if _has_valid_reference_image(prompt_image_base64):
        warnings.append(
            "Imagen text-to-image generation does not accept reference images yet; proceeding with the text prompt only.",
        )

    requested_images = number_of_images or 1
    if not 1 <= requested_images <= MAX_IMAGES_PER_REQUEST:
        raise ValueError(
            f"number_of_images must be between 1 and {MAX_IMAGES_PER_REQUEST}; received {number_of_images}."
        )

    if aspect_ratio and aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
        raise ValueError(
            "aspect_ratio must be one of: " + ", ".join(SUPPORTED_ASPECT_RATIOS)
        )

    if person_generation and person_generation not in SUPPORTED_PERSON_POLICIES:
        raise ValueError(
            "person_generation must be one of: " + ", ".join(SUPPORTED_PERSON_POLICIES)
        )

    if image_size:
        if image_size not in SUPPORTED_IMAGE_SIZES:
            raise ValueError(
                "image_size must be one of: " + ", ".join(SUPPORTED_IMAGE_SIZES)
            )
        if not _model_supports_image_size(model_name):
            warnings.append(
                "The selected Imagen model does not support adjustable image_size; ignoring the requested value.",
            )
            image_size = None

    config_payload: dict[str, Any] = {
        "number_of_images": requested_images,
        "output_mime_type": "image/png",
        "include_rai_reason": True,
    }

    if aspect_ratio:
        config_payload["aspect_ratio"] = aspect_ratio
    if image_size:
        config_payload["image_size"] = image_size
    if person_generation:
        config_payload["person_generation"] = person_generation

    logger.debug(
        "Invoking Gemini Imagen model=%s images=%s aspect_ratio=%s image_size=%s person_generation=%s",
        model_name,
        config_payload["number_of_images"],
        config_payload.get("aspect_ratio"),
        config_payload.get("image_size"),
        config_payload.get("person_generation"),
    )

    try:
        response = client.models.generate_images(
            model=model_name,
            prompt=prompt,
            config=config_payload,
        )
    except Exception as exc:  # noqa: BLE001 - need full error context for debugging
        logger.exception("Gemini Imagen request failed: %s", exc)
        raise RuntimeError(_summarise_imagen_exception(exc)) from exc

    user_context = _resolve_user_context(ctx)
    if user_context and user_context.user_id:
        user_id = user_context.user_id
    else:
        user_id = _extract_user_identifier(ctx)

    try:
        storage = get_user_file_storage()
    except Exception:  # noqa: BLE001 - storage misconfiguration should not crash tool
        storage = None

    output_directory, keep_local = _resolve_output_directory(user_id, storage, user_context)
    saved_images = _save_generated_images(
        response.generated_images,
        output_directory,
        storage=storage,
        user_context=user_context,
        keep_local=keep_local,
    )

    if not saved_images:
        raise RuntimeError("The Imagen API did not return any image bytes to store.")

    if not keep_local:
        shutil.rmtree(output_directory, ignore_errors=True)

    if keep_local:
        note = (
            f"Saved {len(saved_images)} image{'s' if len(saved_images) != 1 else ''} "
            f"to files/users/{user_id}"
        )
    else:
        backend_label = _describe_storage_backend(storage) if storage is not None else "remote"
        note = (
            f"Uploaded {len(saved_images)} image{'s' if len(saved_images) != 1 else ''} "
            f"to {backend_label} storage for user {user_id}"
        )

    logger.debug(
        "Gemini Imagen produced %s image(s); stored_locally=%s",
        len(saved_images),
        keep_local,
    )

    context_payload = getattr(ctx, "context", {}) or {}
    job_id = context_payload.get("job_id")
    attachments_payload: List[Dict[str, Any]] = []
    for image in saved_images:
        api_uri = f"/v1/files/download/{image.relative_path}"
        attachments_payload.append(
            {
                "uri": api_uri,
                "download_url": image.download_url,
                "relative_path": image.relative_path,
                "name": image.file_name,
                "type": image.mime_type,
                "size_bytes": image.size_bytes,
            }
        )
    register_generated_attachments(job_id, attachments_payload)

    return GenerateImageResult(
        prompt=prompt,
        model=model_name,
        count=len(saved_images),
        images=saved_images,
        note=note,
        warnings=warnings,
        attachments=attachments_payload,
    )


async def generate_image_tool(
    ctx: RunContextWrapper[Any],
    *,
    prompt: str,
    model: Optional[str] = None,
    prompt_image_base64: Optional[str] = None,
    prompt_image_mime_type: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    image_size: Optional[str] = None,
    number_of_images: int = 1,
    person_generation: Optional[str] = None,
) -> GenerateImageResult:
    """Generate images via Gemini Imagen and persist them to the downloads directory."""

    if prompt_image_mime_type and not prompt_image_base64:
        raise ValueError("prompt_img_mime_type was provided without prompt_img base64 data.")

    return await asyncio.to_thread(
        _generate_images_impl,
        ctx,
        prompt=prompt,
        model=model,
        prompt_image_base64=prompt_image_base64,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        number_of_images=number_of_images,
        person_generation=person_generation,
    )


__all__ = [
    "GenerateImageResult",
    "GeneratedImageFile",
    "generate_image_tool",
]
