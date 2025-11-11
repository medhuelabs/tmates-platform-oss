"""Environment-aware storage helpers for user-facing file management."""

from __future__ import annotations

import logging
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from supabase import Client, create_client
from storage3.exceptions import StorageApiError

from app.auth import UserContext
from app.config import CONFIG
from app.services.downloads import resolve_download_directory
from uuid import uuid4
from app.services.files import (
    FileDescriptor,
    collect_user_files,
    format_file_size,
    format_file_timestamp,
    serialize_file_descriptors,
)

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Base class for storage backend errors."""


class StorageFileNotFound(StorageError):
    """Raised when a requested file is missing."""


class StoragePathError(StorageError):
    """Raised when a relative path contains unsafe traversal segments."""


@dataclass
class StorageFileResult:
    """Represents the contents of a retrieved file."""

    filename: str
    path: Optional[Path] = None
    content: Optional[bytes] = None
    size: Optional[int] = None
    modified: Optional[datetime] = None


@dataclass(frozen=True)
class SavedFileInfo:
    """Metadata returned after saving a file."""

    file_name: str
    relative_path: str
    download_url: str
    mime_type: str
    size: int


def _sanitize_file_name(candidate: str, default: str = "upload.bin") -> str:
    """Return a filesystem-safe file name."""

    name = Path(candidate or "").name
    if not name or name in {".", ".."}:
        name = default
    return name


class UserFileStorageBackend:
    """Interface for user file storage implementations."""

    backend_id: str = "base"
    requires_temporary_directory: bool = False

    def list_files(self, user_context: UserContext, *, limit: int) -> Dict[str, object]:
        raise NotImplementedError

    def retrieve_file(self, user_context: UserContext, relative_path: str) -> StorageFileResult:
        raise NotImplementedError

    def delete_file(self, user_context: UserContext, relative_path: str) -> None:
        raise NotImplementedError

    def save_file(
        self,
        user_context: UserContext,
        *,
        file_name: str,
        content: bytes,
        mime_type: str,
    ) -> SavedFileInfo:
        raise NotImplementedError


class LocalUserFileStorageBackend(UserFileStorageBackend):
    """Local filesystem-backed storage used for development."""

    backend_id = "local"
    requires_temporary_directory = False

    def list_files(self, user_context: UserContext, *, limit: int) -> Dict[str, object]:
        download_dir = resolve_download_directory(
            user_context=user_context,
            default_subdir=f"users/{user_context.user_id}",
            ensure_exists=True,
        )
        return collect_user_files(download_dir, limit=limit)

    def retrieve_file(self, user_context: UserContext, relative_path: str) -> StorageFileResult:
        download_dir = resolve_download_directory(
            user_context=user_context,
            default_subdir=f"users/{user_context.user_id}",
            ensure_exists=False,
        )
        if not download_dir.exists():
            raise StorageFileNotFound("User files directory is empty")

        requested = self._resolve_path(download_dir, relative_path)
        try:
            stat = requested.stat()
        except FileNotFoundError as exc:
            raise StorageFileNotFound(str(exc)) from exc

        return StorageFileResult(
            filename=requested.name,
            path=requested,
            size=stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )

    def delete_file(self, user_context: UserContext, relative_path: str) -> None:
        download_dir = resolve_download_directory(
            user_context=user_context,
            default_subdir=f"users/{user_context.user_id}",
            ensure_exists=False,
        )
        if not download_dir.exists():
            raise StorageFileNotFound("User files directory is empty")

        target = self._resolve_path(download_dir, relative_path)
        try:
            target.unlink()
        except FileNotFoundError as exc:
            raise StorageFileNotFound(str(exc)) from exc
        except OSError as exc:
            raise StorageError(str(exc)) from exc

    @staticmethod
    def _resolve_path(root: Path, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise StoragePathError("Absolute paths are not allowed")
        resolved_root = root.resolve()
        resolved_target = (resolved_root / candidate).resolve()
        try:
            resolved_target.relative_to(resolved_root)
        except ValueError as exc:
            raise StoragePathError("Path escapes files directory") from exc
        if not resolved_target.is_file():
            raise StorageFileNotFound("Path is not a file")
        return resolved_target

    def save_file(
        self,
        user_context: UserContext,
        *,
        file_name: str,
        content: bytes,
        mime_type: str,
    ) -> SavedFileInfo:
        directory = resolve_download_directory(
            user_context=user_context,
            default_subdir=f"users/{user_context.user_id}",
            ensure_exists=True,
        )

        safe_name = _sanitize_file_name(file_name)
        suffix = Path(safe_name).suffix
        unique_name = f"{uuid4().hex}{suffix}" if suffix else uuid4().hex

        target = directory / unique_name
        target.write_bytes(content)

        relative_path = target.name
        download_url = f"/v1/files/download/{quote(relative_path)}"

        return SavedFileInfo(
            file_name=target.name,
            relative_path=relative_path,
            download_url=download_url,
            mime_type=mime_type,
            size=len(content),
        )


class SupabaseUserFileStorageBackend(UserFileStorageBackend):
    """Supabase Storage-backed user file storage for production."""

    backend_id = "supabase"
    requires_temporary_directory = True

    def __init__(self) -> None:
        self.supabase_url = getattr(CONFIG, "supabase_url", None) or os.getenv("SUPABASE_URL")
        self.supabase_service_role_key = (
            getattr(CONFIG, "supabase_service_role_key", None)
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )
        if not self.supabase_url or not self.supabase_service_role_key:
            raise StorageError("Supabase storage configuration is incomplete")

        self.bucket_name = (
            getattr(CONFIG, "supabase_storage_bucket", None)
            or os.getenv("SUPABASE_STORAGE_BUCKET")
            or "user-files"
        )
        self.prefix_root = (
            getattr(CONFIG, "supabase_storage_prefix", None)
            or os.getenv("SUPABASE_STORAGE_PREFIX")
            or "users"
        ).strip("/")
        self.signed_url_ttl = getattr(CONFIG, "supabase_signed_url_ttl", 3600) or 3600

        self.client: Client = create_client(self.supabase_url, self.supabase_service_role_key)
        self._ensure_bucket_exists()

    def list_files(self, user_context: UserContext, *, limit: int) -> Dict[str, object]:
        try:
            self._sync_local_directory(user_context)
            user_prefix = self._user_prefix(user_context.user_id)
            entries = list(self._walk_supabase_tree(user_prefix))
        except (StorageFileNotFound, StoragePathError):
            raise
        except Exception as exc:  # noqa: BLE001 - avoid leaking raw exceptions
            raise StorageError(f"Supabase list_files failed: {exc}") from exc

        if not entries:
            return {
                "files": [],
                "total_count": 0,
                "total_size": 0,
                "has_more": False,
                "limit": limit,
            }

        descriptors: List[FileDescriptor] = []
        total_size = 0

        for full_path, metadata in entries:
            relative_path = self._relative_to_user(full_path, user_prefix)
            stats = metadata.get("metadata") or {}
            size = int(stats.get("size") or 0)
            total_size += size

            modified = self._parse_timestamp(
                metadata.get("updated_at")
                or metadata.get("created_at")
            )

            download_url = self._build_download_url(full_path, relative_path)

            descriptors.append(
                FileDescriptor(
                    name=PurePosixPath(relative_path).name,
                    relative_path=relative_path,
                    size=size,
                    size_display=format_file_size(size),
                    modified=modified,
                    modified_display=format_file_timestamp(modified),
                    modified_iso=modified.isoformat(timespec="seconds"),
                    download_url=download_url,
                )
            )

        descriptors.sort(key=lambda item: item.modified, reverse=True)
        summary: Dict[str, object] = {
            "total_count": len(descriptors),
            "total_size": total_size,
            "total_size_display": format_file_size(total_size),
            "has_more": len(descriptors) > limit,
            "limit": limit,
            "files": serialize_file_descriptors(descriptors[:limit]),
        }
        return summary

    def retrieve_file(self, user_context: UserContext, relative_path: str) -> StorageFileResult:
        try:
            self._sync_local_directory(user_context)
            cleaned_path = self._sanitize_relative(relative_path)
            storage_path = self._full_storage_path(user_context.user_id, cleaned_path)
            data = self.client.storage.from_(self.bucket_name).download(storage_path)
        except StorageFileNotFound:
            raise
        except StoragePathError:
            raise
        except StorageApiError as exc:
            if getattr(exc, "status", None) == 404:
                raise StorageFileNotFound("File not found in Supabase") from exc
            raise StorageError(f"Supabase download failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive catch
            raise StorageError(f"Unexpected Supabase error: {exc}") from exc

        if data is None:
            raise StorageError("Supabase returned an empty response")

        return StorageFileResult(
            filename=PurePosixPath(cleaned_path).name,
            content=data,
            size=len(data),
        )

    def delete_file(self, user_context: UserContext, relative_path: str) -> None:
        try:
            cleaned_path = self._sanitize_relative(relative_path)
            storage_path = self._full_storage_path(user_context.user_id, cleaned_path)
            response = self.client.storage.from_(self.bucket_name).remove([storage_path])
        except StorageFileNotFound:
            raise
        except StoragePathError:
            raise
        except StorageApiError as exc:
            if getattr(exc, "status", None) == 404:
                raise StorageFileNotFound("File not found in Supabase") from exc
            raise StorageError(f"Supabase delete failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive catch
            raise StorageError(f"Unexpected Supabase delete error: {exc}") from exc

        if isinstance(response, list) and not response:
            raise StorageFileNotFound("File not removed; Supabase returned empty response")

        local_dir = resolve_download_directory(
            user_context=user_context,
            default_subdir=f"users/{user_context.user_id}",
            ensure_exists=False,
        )
        if local_dir.exists():
            local_candidate = local_dir / PurePosixPath(cleaned_path)
            if local_candidate.exists() and local_candidate.is_file():
                try:
                    local_candidate.unlink()
                except OSError as exc:
                    logger.warning(
                        "Failed to remove local file %s after Supabase delete: %s",
                        local_candidate,
                        exc,
                    )
        
    def _api_download_url(self, relative_path: str) -> str:
        return f"/v1/files/download/{quote(relative_path, safe='/')}"

    def _build_download_url(self, full_path: str, relative_path: str) -> str:
        try:
            signed = self.client.storage.from_(self.bucket_name).create_signed_url(
                full_path,
                expires_in=self.signed_url_ttl,
            )
            url = signed.get("signedUrl") or signed.get("signedURL")
            if url:
                return url
        except StorageApiError as exc:
            logger.warning("Failed to create Supabase signed URL for %s: %s", full_path, exc)
        except Exception as exc:  # pragma: no cover - defensive catch
            logger.warning("Unexpected error creating Supabase signed URL for %s: %s", full_path, exc)
        return self._api_download_url(relative_path)

    def _ensure_bucket_exists(self) -> None:
        try:
            self.client.storage.get_bucket(self.bucket_name)
        except StorageApiError:
            try:
                self.client.storage.create_bucket(
                    self.bucket_name,
                    options={"public": False},
                )
            except StorageApiError as exc:
                raise StorageError(f"Unable to ensure Supabase bucket '{self.bucket_name}' exists: {exc}") from exc

    def _user_prefix(self, user_id: str) -> str:
        if self.prefix_root:
            return f"{self.prefix_root}/{user_id}"
        return user_id

    def _full_storage_path(self, user_id: str, cleaned_path: str) -> str:
        prefix = self._user_prefix(user_id)
        return f"{prefix}/{cleaned_path}" if prefix else cleaned_path

    def _relative_to_user(self, full_path: str, user_prefix: str) -> str:
        if not user_prefix:
            return full_path
        prefix = f"{user_prefix}/"
        if full_path.startswith(prefix):
            return full_path[len(prefix):]
        return full_path

    def _sanitize_relative(self, relative_path: str) -> str:
        path_obj = PurePosixPath(relative_path)
        if path_obj.is_absolute():
            raise StoragePathError("Absolute paths are not allowed")
        if any(part in {"..", ""} for part in path_obj.parts):
            raise StoragePathError("Unsafe traversal segments detected")
        normalized_parts = [part for part in path_obj.parts if part not in {"."}]
        if not normalized_parts:
            raise StoragePathError("Path cannot be empty")
        return "/".join(normalized_parts)

    def save_file(
        self,
        user_context: UserContext,
        *,
        file_name: str,
        content: bytes,
        mime_type: str,
    ) -> SavedFileInfo:
        safe_name = _sanitize_file_name(file_name)
        suffix = Path(safe_name).suffix
        unique_name = f"{uuid4().hex}{suffix}" if suffix else uuid4().hex

        cleaned_path = self._sanitize_relative(unique_name)
        storage_path = self._full_storage_path(user_context.user_id, cleaned_path)

        fd, temp_path = tempfile.mkstemp(prefix="upload_", suffix=suffix or ".bin")
        os.close(fd)
        temp_file = Path(temp_path)
        try:
            temp_file.write_bytes(content)
            bucket = self.client.storage.from_(self.bucket_name)
            bucket.upload(
                storage_path,
                str(temp_file),
                file_options={
                    "content-type": mime_type,
                    "cacheControl": "3600",
                },
            )
        except StorageApiError as exc:
            raise StorageError(f"Supabase upload failed: {exc}") from exc
        finally:
            try:
                temp_file.unlink(missing_ok=True)  # type: ignore[arg-type]
            except Exception:
                pass

        download_url = self._build_download_url(storage_path, cleaned_path)

        return SavedFileInfo(
            file_name=cleaned_path,
            relative_path=cleaned_path,
            download_url=download_url,
            mime_type=mime_type,
            size=len(content),
        )

    def _walk_supabase_tree(self, prefix: str) -> Iterable[Tuple[str, Dict[str, object]]]:
        queue: List[str] = [prefix]
        seen: set[str] = set()
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            for entry in self._list_objects(current):
                name = entry.get("name")
                if not name:
                    continue
                full_path = f"{current}/{name}" if current else name
                metadata = entry.get("metadata")
                if metadata:
                    yield full_path, entry
                else:
                    queue.append(full_path)

    def _list_objects(self, path: str) -> List[Dict[str, object]]:
        results: List[Dict[str, object]] = []
        offset = 0
        while True:
            options = {
                "limit": 1000,
                "offset": offset,
                "sortBy": {"column": "updated_at", "order": "desc"},
            }
            try:
                batch = self.client.storage.from_(self.bucket_name).list(path, options=options)
            except StorageApiError as exc:
                raise StorageError(str(exc)) from exc
            if not batch:
                break
            results.extend(batch)
            if len(batch) < 1000:
                break
            offset += len(batch)
        return results

    @staticmethod
    def _parse_timestamp(value: Optional[str]) -> datetime:
        if not value:
            return datetime.now(timezone.utc)
        try:
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)

    def _sync_local_directory(self, user_context: UserContext) -> None:
        local_dir = resolve_download_directory(
            user_context=user_context,
            default_subdir=f"users/{user_context.user_id}",
            ensure_exists=False,
        )
        if not local_dir.exists():
            return

        bucket = self.client.storage.from_(self.bucket_name)
        for file_path in local_dir.rglob("*"):
            if not file_path.is_file():
                continue

            relative_path = file_path.relative_to(local_dir).as_posix()
            storage_path = self._full_storage_path(user_context.user_id, relative_path)

            try:
                if bucket.exists(storage_path):
                    continue
            except StorageApiError as exc:
                logger.warning("Supabase existence check failed for %s: %s", storage_path, exc)
                continue

            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

            try:
                bucket.upload(
                    storage_path,
                    str(file_path),
                    file_options={"content-type": content_type},
                )
            except StorageApiError as exc:
                logger.error("Supabase upload failed for %s: %s", storage_path, exc)
                continue


class S3UserFileStorageBackend(UserFileStorageBackend):
    """AWS S3 (or compatible) storage backend for user files."""

    backend_id = "s3"
    requires_temporary_directory = True

    def __init__(self) -> None:
        try:
            import boto3
            from botocore.config import Config as BotoConfig
            from botocore.exceptions import ClientError
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise StorageError(
                "boto3 must be installed to use the S3 storage backend (pip install boto3).",
            ) from exc

        self._client_error = ClientError
        self.bucket_name = (
            getattr(CONFIG, "s3_bucket_name", None)
            or os.getenv("S3_BUCKET_NAME")
        )
        if not self.bucket_name:
            raise StorageError("S3 storage configuration is missing S3_BUCKET_NAME.")

        self.prefix_root = (
            getattr(CONFIG, "s3_storage_prefix", None)
            or os.getenv("S3_STORAGE_PREFIX")
            or "users"
        ).strip("/")
        self.signed_url_ttl = getattr(CONFIG, "s3_signed_url_ttl", 3600) or 3600

        region = (
            getattr(CONFIG, "aws_region", None)
            or os.getenv("AWS_REGION")
        )
        profile = (
            getattr(CONFIG, "aws_profile", None)
            or os.getenv("AWS_PROFILE")
        )
        access_key = (
            getattr(CONFIG, "aws_access_key_id", None)
            or os.getenv("AWS_ACCESS_KEY_ID")
        )
        secret_key = (
            getattr(CONFIG, "aws_secret_access_key", None)
            or os.getenv("AWS_SECRET_ACCESS_KEY")
        )
        session_token = (
            getattr(CONFIG, "aws_session_token", None)
            or os.getenv("AWS_SESSION_TOKEN")
        )
        endpoint_url = (
            getattr(CONFIG, "s3_endpoint_url", None)
            or os.getenv("S3_ENDPOINT_URL")
        )
        force_path_style = (
            getattr(CONFIG, "s3_force_path_style", False)
            or _env_bool_fallback("S3_FORCE_PATH_STYLE", False)
        )

        session_kwargs: Dict[str, str] = {}
        if profile:
            session_kwargs["profile_name"] = profile

        session = boto3.session.Session(**session_kwargs) if session_kwargs else boto3.session.Session()

        client_kwargs: Dict[str, object] = {}
        if region:
            client_kwargs["region_name"] = region
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        if access_key and secret_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key
        if session_token:
            client_kwargs["aws_session_token"] = session_token
        if force_path_style:
            client_kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})

        self.client = session.client("s3", **client_kwargs)

        try:
            self.client.head_bucket(Bucket=self.bucket_name)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = error.get("Code")
            message = error.get("Message") or str(exc)
            raise StorageError(f"Unable to access S3 bucket '{self.bucket_name}' ({code}): {message}") from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise StorageError(f"Unexpected error validating S3 bucket '{self.bucket_name}': {exc}") from exc

    def list_files(self, user_context: UserContext, *, limit: int) -> Dict[str, object]:
        user_prefix = self._user_prefix(user_context.user_id)
        s3_prefix = f"{user_prefix}/" if user_prefix else ""

        paginator = self.client.get_paginator("list_objects_v2")
        descriptors: List[FileDescriptor] = []
        total_size = 0

        try:
            page_iterator = paginator.paginate(Bucket=self.bucket_name, Prefix=s3_prefix)
        except self._client_error as exc:
            raise StorageError(f"S3 list_objects failed: {exc}") from exc

        for page in page_iterator:
            contents = page.get("Contents") or []
            for obj in contents:
                key = obj.get("Key")
                if not key or key.endswith("/"):
                    continue
                relative_path = self._relative_to_user(key, user_prefix)
                size = int(obj.get("Size") or 0)
                total_size += size

                modified_raw = obj.get("LastModified")
                if isinstance(modified_raw, datetime):
                    modified = modified_raw.astimezone(timezone.utc)
                elif isinstance(modified_raw, str):
                    try:
                        modified = datetime.fromisoformat(modified_raw)
                        if modified.tzinfo is None:
                            modified = modified.replace(tzinfo=timezone.utc)
                        else:
                            modified = modified.astimezone(timezone.utc)
                    except ValueError:
                        modified = datetime.now(timezone.utc)
                else:
                    modified = datetime.now(timezone.utc)

                download_url = self._build_download_url(key, relative_path)

                descriptors.append(
                    FileDescriptor(
                        name=PurePosixPath(relative_path).name,
                        relative_path=relative_path,
                        size=size,
                        size_display=format_file_size(size),
                        modified=modified,
                        modified_display=format_file_timestamp(modified),
                        modified_iso=modified.isoformat(timespec="seconds"),
                        download_url=download_url,
                    )
                )

        if not descriptors:
            return {
                "files": [],
                "total_count": 0,
                "total_size": 0,
                "has_more": False,
                "limit": limit,
            }

        descriptors.sort(key=lambda item: item.modified, reverse=True)
        summary: Dict[str, object] = {
            "total_count": len(descriptors),
            "total_size": total_size,
            "total_size_display": format_file_size(total_size),
            "has_more": len(descriptors) > limit,
            "limit": limit,
            "files": serialize_file_descriptors(descriptors[:limit]),
        }
        return summary

    def retrieve_file(self, user_context: UserContext, relative_path: str) -> StorageFileResult:
        cleaned_path = self._sanitize_relative(relative_path)
        key = self._full_storage_path(user_context.user_id, cleaned_path)

        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
        except self._client_error as exc:
            error = exc.response.get("Error", {})
            code = (error.get("Code") or "").lower()
            if code in {"nosuchkey", "404"}:
                raise StorageFileNotFound("File not found in S3") from exc
            raise StorageError(f"S3 download failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise StorageError(f"Unexpected S3 error: {exc}") from exc

        body = response.get("Body")
        if body is None:
            raise StorageError("S3 returned an empty response body.")

        content = body.read()
        last_modified = response.get("LastModified")
        modified = (
            last_modified.astimezone(timezone.utc)
            if isinstance(last_modified, datetime)
            else datetime.now(timezone.utc)
        )

        return StorageFileResult(
            filename=PurePosixPath(cleaned_path).name,
            content=content,
            size=len(content),
            modified=modified,
        )

    def delete_file(self, user_context: UserContext, relative_path: str) -> None:
        cleaned_path = self._sanitize_relative(relative_path)
        key = self._full_storage_path(user_context.user_id, cleaned_path)

        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
        except self._client_error as exc:
            error = exc.response.get("Error", {})
            code = (error.get("Code") or "").lower()
            if code in {"nosuchkey", "404"}:
                raise StorageFileNotFound("File not found in S3") from exc
            raise StorageError(f"S3 delete failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise StorageError(f"Unexpected S3 delete error: {exc}") from exc

    def save_file(
        self,
        user_context: UserContext,
        *,
        file_name: str,
        content: bytes,
        mime_type: str,
    ) -> SavedFileInfo:
        safe_name = _sanitize_file_name(file_name)
        suffix = Path(safe_name).suffix
        unique_name = f"{uuid4().hex}{suffix}" if suffix else uuid4().hex

        cleaned_path = self._sanitize_relative(unique_name)
        key = self._full_storage_path(user_context.user_id, cleaned_path)

        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=content,
                ContentType=mime_type,
            )
        except self._client_error as exc:
            raise StorageError(f"S3 upload failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise StorageError(f"Unexpected S3 upload error: {exc}") from exc

        download_url = self._build_download_url(key, cleaned_path)

        return SavedFileInfo(
            file_name=cleaned_path,
            relative_path=cleaned_path,
            download_url=download_url,
            mime_type=mime_type,
            size=len(content),
        )

    def _api_download_url(self, relative_path: str) -> str:
        return f"/v1/files/download/{quote(relative_path, safe='/')}"

    def _build_download_url(self, key: str, relative_path: str) -> str:
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=self.signed_url_ttl,
            )
        except self._client_error as exc:
            logger.warning("Failed to create S3 presigned URL for %s: %s", key, exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Unexpected error creating S3 presigned URL for %s: %s", key, exc)
        return self._api_download_url(relative_path)

    def _user_prefix(self, user_id: str) -> str:
        if self.prefix_root:
            return f"{self.prefix_root}/{user_id}"
        return user_id

    def _full_storage_path(self, user_id: str, cleaned_path: str) -> str:
        prefix = self._user_prefix(user_id)
        return f"{prefix}/{cleaned_path}" if prefix else cleaned_path

    def _relative_to_user(self, full_path: str, user_prefix: str) -> str:
        if not user_prefix:
            return full_path
        prefix = f"{user_prefix}/"
        if full_path.startswith(prefix):
            return full_path[len(prefix):]
        return full_path

    def _sanitize_relative(self, relative_path: str) -> str:
        path_obj = PurePosixPath(relative_path)
        if path_obj.is_absolute():
            raise StoragePathError("Absolute paths are not allowed")
        if any(part in {"..", ""} for part in path_obj.parts):
            raise StoragePathError("Unsafe traversal segments detected")
        normalized_parts = [part for part in path_obj.parts if part not in {"."}]
        if not normalized_parts:
            raise StoragePathError("Path cannot be empty")
        return "/".join(normalized_parts)


def _env_bool_fallback(env_name: str, default: bool) -> bool:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_user_file_storage() -> UserFileStorageBackend:
    """Return the active storage backend based on configuration."""

    backend = (
        getattr(CONFIG, "file_storage_backend", None)
        or os.getenv("FILE_STORAGE_BACKEND")
        or ("local" if getattr(CONFIG, "is_development", False) else None)
    )
    backend_normalized = (backend or "").strip().lower()

    if backend_normalized in {"", "local"}:
        logger.debug("Using local filesystem storage backend for user files")
        return LocalUserFileStorageBackend()

    if backend_normalized == "supabase":
        logger.debug("Using Supabase storage backend for user files")
        return SupabaseUserFileStorageBackend()

    if backend_normalized == "s3":
        logger.debug("Using S3 storage backend for user files")
        return S3UserFileStorageBackend()

    raise StorageError(f"Unsupported file storage backend '{backend_normalized}'. Set FILE_STORAGE_BACKEND to local, supabase, or s3.")


def save_user_file(
    user_context: UserContext,
    *,
    file_name: str,
    content: bytes,
    mime_type: str,
) -> SavedFileInfo:
    """Persist a user-provided file using the configured storage backend."""

    storage = get_user_file_storage()
    try:
        return storage.save_file(
            user_context,
            file_name=file_name,
            content=content,
            mime_type=mime_type,
        )
    except Exception as exc:
        raise StorageError(f"Failed to save user file: {exc}") from exc


__all__ = [
    "StorageError",
    "StorageFileNotFound",
    "StorageFileResult",
    "StoragePathError",
    "UserFileStorageBackend",
    "S3UserFileStorageBackend",
    "get_user_file_storage",
    "save_user_file",
    "SavedFileInfo",
]
