"""Shared helpers for collecting user file metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote


@dataclass(frozen=True)
class FileDescriptor:
    """Summary of a downloadable file belonging to a user."""

    name: str
    relative_path: str
    size: int
    size_display: str
    modified: datetime
    modified_display: str
    modified_iso: str
    download_url: str


def format_file_size(num_bytes: int) -> str:
    """Return a human readable file size string."""
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(max(num_bytes, 0))

    for index, unit in enumerate(units):
        is_last_unit = index == len(units) - 1
        if size < step or is_last_unit:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}" if size != int(size) else f"{int(size)} {unit}"
        size /= step

    return "0 B"


def format_file_timestamp(dt_obj: datetime) -> str:
    """Format a datetime object for display."""
    return dt_obj.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def collect_user_files(download_dir: Path, *, limit: int = 250) -> Dict[str, Any]:
    """Gather metadata for files stored under the user files directory."""
    summary: Dict[str, Any] = {
        "files": [],
        "total_count": 0,
        "total_size": 0,
        "has_more": False,
        "limit": limit,
    }

    try:
        base_dir = download_dir.resolve()
    except FileNotFoundError:
        return summary

    if not base_dir.exists():
        return summary

    entries: List[FileDescriptor] = []
    total_size = 0

    for path in base_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative_path = path.relative_to(base_dir)
        except ValueError:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue

        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        relative_posix = relative_path.as_posix()
        entries.append(
            FileDescriptor(
                name=path.name,
                relative_path=relative_posix,
                size=stat.st_size,
                size_display=format_file_size(stat.st_size),
                modified=modified,
                modified_display=format_file_timestamp(modified),
                modified_iso=modified.isoformat(timespec="seconds"),
                download_url=f"/v1/files/download/{quote(relative_posix, safe='/')}",
            )
        )
        total_size += stat.st_size

    if not entries:
        summary["total_count"] = 0
        summary["total_size"] = 0
        return summary

    entries.sort(key=lambda item: item.modified, reverse=True)

    summary["total_count"] = len(entries)
    summary["total_size"] = total_size
    summary["total_size_display"] = format_file_size(total_size)
    summary["has_more"] = len(entries) > limit
    limited_entries = entries[:limit]
    summary["files"] = serialize_file_descriptors(limited_entries)
    return summary


def serialize_file_descriptors(files: Iterable[FileDescriptor]) -> List[Dict[str, Any]]:
    """Convert file descriptors to dictionaries suitable for JSON responses."""
    return [
        {
            "name": descriptor.name,
            "relative_path": descriptor.relative_path,
            "size": descriptor.size,
            "size_display": descriptor.size_display,
            "modified": descriptor.modified,
            "modified_display": descriptor.modified_display,
            "modified_iso": descriptor.modified_iso,
            "download_url": descriptor.download_url,
        }
        for descriptor in files
    ]


__all__ = [
    "FileDescriptor",
    "collect_user_files",
    "format_file_size",
    "format_file_timestamp",
    "serialize_file_descriptors",
]
