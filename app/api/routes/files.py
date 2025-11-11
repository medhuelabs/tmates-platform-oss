"""File metadata endpoints."""

from __future__ import annotations

import hmac
import hashlib
import io
import mimetypes
import time
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from app.api.dependencies import get_current_user_id
from app.api.schemas import FileEntry, FileListing, FileUploadResponse, UploadedFile
from app.config import CONFIG
from app.core.agent_runner import resolve_user_context
from app.services.user_file_storage import (
    StorageError,
    StorageFileNotFound,
    StoragePathError,
    get_user_file_storage,
    save_user_file,
)

router = APIRouter()

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB default cap for chat attachments
ALLOWED_UPLOAD_PREFIXES: List[str] = ["image/"]

def _guess_media_type(file_name: str) -> str:
    """Return an appropriate media type for the given filename."""

    extension_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".json": "application/json",
        ".csv": "text/csv",
    }

    suffix = file_name.lower().rsplit(".", 1)
    if len(suffix) == 2:
        candidate = f".{suffix[1]}"
        if candidate in extension_map:
            return extension_map[candidate]

    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


@router.get("/files", response_model=FileListing, status_code=status.HTTP_200_OK)
def list_files(
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(default=100, ge=1, le=500),
) -> FileListing:
    """Return downloadable files for the authenticated user."""

    try:
        user_context, _, _ = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    storage = get_user_file_storage()
    summary = storage.list_files(user_context, limit=limit)
    files = [FileEntry(**entry) for entry in summary.get("files", [])]
    return FileListing(
        files=files,
        total_count=summary.get("total_count", 0),
        total_size=summary.get("total_size", 0),
        total_size_display=summary.get("total_size_display"),
        has_more=summary.get("has_more", False),
        limit=summary.get("limit", limit),
    )


@router.post("/files/upload", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(..., description="Binary file to upload"),
    user_id: str = Depends(get_current_user_id),
) -> FileUploadResponse:
    """Upload a new file to the authenticated user's storage."""

    try:
        user_context, _, _ = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if file.size and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file exceeds the maximum allowed size.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file was empty.")

    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file exceeds the maximum allowed size.",
        )

    mime_type = file.content_type or _guess_media_type(file.filename or "")
    if not any(mime_type.startswith(prefix) for prefix in ALLOWED_UPLOAD_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Only image uploads are allowed.",
        )

    try:
        saved = save_user_file(
            user_context,
            file_name=file.filename or "upload.bin",
            content=data,
            mime_type=mime_type,
        )
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    uploaded = UploadedFile(
        file_name=saved.file_name,
        relative_path=saved.relative_path,
        download_url=saved.download_url,
        mime_type=saved.mime_type,
        size=saved.size,
    )

    return FileUploadResponse(file=uploaded)


@router.get("/files/download/{file_path:path}", status_code=status.HTTP_200_OK)
def download_file(
    file_path: str,
    user_id: str = Depends(get_current_user_id),
) -> FileResponse:
    """Download a specific file from the user's files directory."""
    
    try:
        user_context, _, _ = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    storage = get_user_file_storage()
    try:
        result = storage.retrieve_file(user_context, file_path)
    except StoragePathError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: invalid file path",
        ) from exc
    except StorageFileNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        ) from exc
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch file: {exc}",
        ) from exc

    filename = result.filename or file_path.split("/")[-1]
    media_type = _guess_media_type(filename)

    if result.path is not None:
        return FileResponse(path=str(result.path), filename=filename, media_type=media_type)

    if result.content is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File content unavailable",
        )

    return StreamingResponse(
        io.BytesIO(result.content),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _require_view_token_secret() -> bytes:
    secret = getattr(CONFIG, "file_view_token_secret", None) or getattr(CONFIG, "session_secret", None)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File view token secret is not configured.",
        )
    return secret.encode("utf-8")


# Simple temporary token generation for public image viewing
def generate_view_token(user_id: str, file_path: str, expires_at: int) -> str:
    """Generate a secure temporary token for public file viewing."""
    secret = _require_view_token_secret()
    message = f"{user_id}:{file_path}:{expires_at}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify_view_token(token: str, user_id: str, file_path: str, expires_at: int) -> bool:
    """Verify a temporary view token using constant-time comparison."""
    if expires_at <= int(time.time()):
        return False
    expected = generate_view_token(user_id, file_path, expires_at)
    return hmac.compare_digest(token, expected)


@router.get("/files/view/{file_path:path}", status_code=status.HTTP_200_OK)
def view_file_public(
    file_path: str,
    user_id: str = Query(...),
    expires: int = Query(...),
    token: str = Query(...),
) -> FileResponse:
    """Public file viewing with temporary signed URL - no authentication required."""
    
    # Verify the temporary token
    if not verify_view_token(token, user_id, file_path, expires):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired view token"
        )
    
    try:
        user_context, _, _ = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    storage = get_user_file_storage()
    try:
        result = storage.retrieve_file(user_context, file_path)
    except StoragePathError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: invalid file path",
        ) from exc
    except StorageFileNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        ) from exc
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch file: {exc}",
        ) from exc

    filename = result.filename or file_path.split("/")[-1]
    media_type = _guess_media_type(filename)
    headers = {"Cache-Control": "max-age=3600"}

    if result.path is not None:
        return FileResponse(
            path=str(result.path),
            filename=filename,
            media_type=media_type,
            headers=headers,
        )

    if result.content is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File content unavailable",
        )

    response = StreamingResponse(
        io.BytesIO(result.content),
        media_type=media_type,
    )
    response.headers.update(headers)
    response.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@router.post("/files/generate-view-url/{file_path:path}", status_code=status.HTTP_200_OK)
def generate_view_url(
    file_path: str,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """Generate a temporary public view URL for a file."""
    
    # Token expires in 24 hours
    expires_at = int(time.time()) + (24 * 60 * 60)
    token = generate_view_token(user_id, file_path, expires_at)
    
    # Construct the public view URL
    view_url = f"/api/files/view/{file_path}?user_id={user_id}&expires={expires_at}&token={token}"
    
    return {
        "view_url": view_url,
        "expires_at": expires_at,
    }


@router.delete("/files/{file_path:path}", status_code=status.HTTP_200_OK)
def delete_file(
    file_path: str,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """Delete a specific file from the user's files directory."""
    
    try:
        user_context, _, _ = resolve_user_context(user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    storage = get_user_file_storage()

    try:
        storage.delete_file(user_context, file_path)
    except StoragePathError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: invalid file path",
        ) from exc
    except StorageFileNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        ) from exc
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file: {exc}",
        ) from exc

    return {
        "success": True,
        "message": f"File '{file_path}' deleted successfully",
    }
