"""Shared helpers for resolving download directories."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from app.auth import UserContext

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOWNLOAD_ROOT = PROJECT_ROOT / "files"


def _normalize_path(path_like: Union[str, Path]) -> Path:
    """Return an absolute Path for the given value."""
    path = Path(path_like)
    if not path.is_absolute():
        return (PROJECT_ROOT / path).resolve()
    return path


def resolve_download_directory(
    *,
    user_context: Optional[UserContext],
    override_path: Optional[Union[str, Path]] = None,
    default_path: Optional[Union[str, Path]] = None,
    default_subdir: Optional[str] = None,
    ensure_exists: bool = True,
) -> Path:
    """
    Resolve the target directory for agent downloads.

    Priority order:
    1. Explicit override_path
    2. User-specific directory when a UserContext is provided
    3. default_path if provided
    4. DEFAULT_DOWNLOAD_ROOT / default_subdir (or DEFAULT_DOWNLOAD_ROOT when subdir missing)
    """
    if override_path:
        directory = _normalize_path(override_path)
    elif user_context:
        directory = Path(user_context.get_download_dir())
    elif default_path:
        directory = _normalize_path(default_path)
    else:
        directory = DEFAULT_DOWNLOAD_ROOT
        if default_subdir:
            directory /= default_subdir

    if ensure_exists:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


__all__ = ["resolve_download_directory"]
