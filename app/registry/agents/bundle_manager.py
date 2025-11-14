"""Agent bundle download, cache, and module registration helpers."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

import requests
from supabase import create_client

from app.config import CONFIG
from app.db.client import get_database_client
from app.logger import log


class BundleResolutionError(RuntimeError):
    """Raised when a catalog bundle cannot be resolved."""


@dataclass(frozen=True)
class ResolvedBundle:
    agent_key: str
    version: str
    agent_dir: Path
    manifest: Dict[str, object]


class AgentBundleManager:
    """Fetch and cache agent bundles referenced by the catalog."""

    def __init__(self, cache_dir: Optional[str | Path] = None) -> None:
        default_cache = Path(
            os.getenv("AGENT_BUNDLE_CACHE_DIR")
            or Path(tempfile.gettempdir()) / "agents-cache"
        )
        self.cache_dir = Path(cache_dir or default_cache).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._prepared_versions: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def prepare_bundle(self, agent_key: str) -> ResolvedBundle:
        """Ensure the bundle for ``agent_key`` is downloaded and unpacked."""

        environment = getattr(CONFIG, "agent_catalog_environment", "prod")
        entry = self._lookup_catalog_entry(agent_key=agent_key, environment=environment)
        if entry is None:
            raise BundleResolutionError(f"No catalog entry available for {agent_key} in {environment} environment")

        version = entry.get("version")
        if not isinstance(version, str) or not version.strip():
            raise BundleResolutionError(f"Catalog entry for {agent_key} is missing a version")
        version = version.strip()

        bundle_url = entry.get("bundle_url")
        if not isinstance(bundle_url, str) or not bundle_url.strip():
            raise BundleResolutionError(f"Catalog entry for {agent_key}@{version} has no bundle_url")
        bundle_url = bundle_url.strip()

        checksum = entry.get("bundle_checksum")
        if checksum and isinstance(checksum, str):
            checksum = checksum.strip()
        else:
            checksum = None

        bundle_dir = self._ensure_bundle(agent_key, version, bundle_url, checksum)
        manifest = entry.get("manifest")
        manifest_dict: Dict[str, object]
        if isinstance(manifest, dict):
            manifest_dict = manifest
        else:
            manifest_dict = {}

        agent_dir = bundle_dir / "app" / "agents" / agent_key
        if not agent_dir.exists():
            raise BundleResolutionError(
                f"Bundle for {agent_key}@{version} is missing expected directory {agent_dir}"  # noqa: E501
            )

        self._register_agent_path(agent_key, agent_dir)
        self._prepared_versions[agent_key] = version
        return ResolvedBundle(agent_key=agent_key, version=version, agent_dir=agent_dir, manifest=manifest_dict)

    def prepared_version(self, agent_key: str) -> Optional[str]:
        return self._prepared_versions.get(agent_key)

    # ------------------------------------------------------------------
    # Catalog lookups & caching
    # ------------------------------------------------------------------
    def _lookup_catalog_entry(self, agent_key: str, *, environment: str) -> Optional[Dict[str, object]]:
        db = get_database_client()
        try:
            entry = db.get_agent_catalog_entry(agent_key=agent_key, environment=environment)
        except Exception as exc:  # pragma: no cover - defensive logging
            log(f"[agent-bundles] failed to load catalog entry for {agent_key}: {exc}")
            return None
        return entry

    def _ensure_bundle(
        self,
        agent_key: str,
        version: str,
        bundle_url: str,
        checksum: Optional[str],
    ) -> Path:
        bundle_root = self.cache_dir / agent_key / version
        marker = bundle_root / ".ready"
        if marker.exists():
            return bundle_root

        if bundle_root.exists():
            shutil.rmtree(bundle_root)
        bundle_root.mkdir(parents=True, exist_ok=True)

        archive_path = bundle_root / f"{agent_key}-{version}.tar.gz"
        self._download_bundle(bundle_url, archive_path)

        if checksum:
            computed = self._compute_sha256(archive_path)
            if computed.lower() != checksum.lower():
                raise BundleResolutionError(
                    f"Checksum mismatch for {agent_key}@{version}: expected {checksum}, got {computed}"
                )

        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(bundle_root)

        extracted_agent_dir = bundle_root / agent_key
        if not extracted_agent_dir.exists():
            raise BundleResolutionError(
                f"Bundle {agent_key}@{version} did not contain expected directory {extracted_agent_dir}"
            )

        target_agent_dir = bundle_root / "app" / "agents" / agent_key
        target_agent_dir.parent.mkdir(parents=True, exist_ok=True)

        if target_agent_dir.exists():
            shutil.rmtree(target_agent_dir)
        shutil.move(str(extracted_agent_dir), str(target_agent_dir))

        init_file = target_agent_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text(
                '"""Auto-generated package marker for catalog bundle."""\n',
                encoding="utf-8",
            )

        marker.write_text(checksum or "", encoding="utf-8")
        return bundle_root

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _register_agent_path(self, agent_key: str, agent_dir: Path) -> None:
        try:
            import app.agents as agents_pkg
        except ImportError as exc:  # pragma: no cover - should never happen
            raise BundleResolutionError("app.agents package not available") from exc

        agent_parent = agent_dir.parent
        parent_str = str(agent_parent)
        if parent_str not in agents_pkg.__path__:
            agents_pkg.__path__.insert(0, parent_str)

    def _download_bundle(self, bundle_url: str, target_path: Path) -> None:
        parsed = urlparse(bundle_url)
        scheme = parsed.scheme.lower()

        if scheme in {"", "file"}:
            source_path = Path(parsed.path or bundle_url).expanduser()
            if not source_path.exists():
                raise BundleResolutionError(f"Bundle path not found: {bundle_url}")
            shutil.copyfile(source_path, target_path)
            return

        if scheme in {"http", "https"}:
            response = requests.get(bundle_url, timeout=60)
            if response.status_code != 200:
                raise BundleResolutionError(f"Failed to download bundle from {bundle_url}: {response.status_code}")
            target_path.write_bytes(response.content)
            return

        if scheme == "s3":
            bucket = parsed.netloc.strip()
            key = parsed.path.lstrip("/")
            if not bucket or not key:
                raise BundleResolutionError(f"Invalid S3 bundle URL: {bundle_url}")

            try:
                import boto3
                from botocore.exceptions import BotoCoreError, ClientError
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise BundleResolutionError("boto3 is required for S3 bundle URLs") from exc

            profile = getattr(CONFIG, "aws_profile", None) or os.getenv("AWS_PROFILE")
            session_kwargs = {"profile_name": profile} if profile else {}
            session = boto3.session.Session(**session_kwargs) if session_kwargs else boto3.session.Session()

            client_kwargs: Dict[str, object] = {}
            endpoint_url = getattr(CONFIG, "s3_endpoint_url", None) or os.getenv("S3_ENDPOINT_URL")
            if endpoint_url:
                client_kwargs["endpoint_url"] = endpoint_url
            region = getattr(CONFIG, "aws_region", None) or os.getenv("AWS_REGION")
            if region:
                client_kwargs["region_name"] = region
            access_key = getattr(CONFIG, "aws_access_key_id", None) or os.getenv("AWS_ACCESS_KEY_ID")
            secret_key = getattr(CONFIG, "aws_secret_access_key", None) or os.getenv("AWS_SECRET_ACCESS_KEY")
            session_token = getattr(CONFIG, "aws_session_token", None) or os.getenv("AWS_SESSION_TOKEN")
            if access_key and secret_key:
                client_kwargs["aws_access_key_id"] = access_key
                client_kwargs["aws_secret_access_key"] = secret_key
            if session_token:
                client_kwargs["aws_session_token"] = session_token

            client = session.client("s3", **client_kwargs)

            streaming_body = None
            try:
                response = client.get_object(Bucket=bucket, Key=key)
                streaming_body = response.get("Body")
                if streaming_body is None:
                    raise BundleResolutionError(f"Empty response body when downloading s3://{bucket}/{key}")
                with target_path.open("wb") as handle:
                    shutil.copyfileobj(streaming_body, handle)
            except (ClientError, BotoCoreError) as exc:
                raise BundleResolutionError(f"Failed to download bundle from s3://{bucket}/{key}: {exc}") from exc
            finally:
                if streaming_body is not None:
                    streaming_body.close()
            return

        if scheme == "supabase":
            bucket, _, storage_path = parsed.path.partition("/")
            bucket = (parsed.netloc or bucket).strip()
            storage_path = storage_path.lstrip("/")
            if not bucket or not storage_path:
                raise BundleResolutionError(f"Invalid Supabase bundle URL: {bundle_url}")

            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
            if not supabase_url or not supabase_key:
                raise BundleResolutionError("Supabase credentials not configured for bundle download")

            client = create_client(supabase_url, supabase_key)
            storage = client.storage.from_(bucket)
            data = storage.download(storage_path)
            if not data:
                raise BundleResolutionError(f"Supabase download returned empty payload for {bundle_url}")
            target_path.write_bytes(data)
            return

        raise BundleResolutionError(f"Unsupported bundle URL scheme: {bundle_url}")

    @staticmethod
    def _compute_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(131072), b""):
                digest.update(chunk)
        return digest.hexdigest()


# Global manager instance reused by loader
BUNDLE_MANAGER = AgentBundleManager()
