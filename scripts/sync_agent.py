#!/usr/bin/env python3
"""
Sync a local agent implementation into the tmates-agents bundle repo and keep manifest
versions aligned.

Usage examples:

    # Copy a custom agent (e.g., 'researcher') into tmates-agents and bump the version
    python scripts/sync_agent.py researcher --version 0.1.0

    # Sync and immediately publish the updated bundle to Supabase
    python scripts/sync_agent.py researcher --version 0.1.0 --publish

    # Sync every agent from this working tree and bump the patch version automatically
    python scripts/sync_agent.py --all --bump patch
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

MONOREPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_AGENT_ROOT = MONOREPO_ROOT / "tmates-platform" / "app" / "agents"
BUNDLE_AGENT_ROOT = MONOREPO_ROOT / "tmates-agents" / "app" / "agents"


class SyncError(RuntimeError):
    """Raised when the sync pipeline cannot complete."""


def update_manifest_version(manifest_path: Path, version: str) -> None:
    """Update the version entry inside a manifest.yaml file."""

    if not manifest_path.is_file():
        raise SyncError(f"Manifest not found: {manifest_path}")

    lines = manifest_path.read_text().splitlines()
    for idx, line in enumerate(lines):
        if line.strip().startswith("version:"):
            lines[idx] = f'version: "{version}"'
            manifest_path.write_text("\n".join(lines) + "\n")
            return

    raise SyncError(f'"version:" key not found in {manifest_path}')


def copy_agent_directory(agent: str) -> None:
    """Copy the agent directory from the platform repo into the bundle repo."""

    source_dir = PLATFORM_AGENT_ROOT / agent
    target_dir = BUNDLE_AGENT_ROOT / agent

    if not source_dir.is_dir():
        raise SyncError(f"Source agent directory not found: {source_dir}")

    if target_dir.exists():
        shutil.rmtree(target_dir)

    shutil.copytree(source_dir, target_dir)


def publish_bundle(agent: str) -> None:
    """Invoke publish_agent_bundle.py to build/upload the agent bundle."""

    script = MONOREPO_ROOT / "tmates-agents" / "scripts" / "publish_agent_bundle.py"
    if not script.is_file():
        raise SyncError(f"Bundle publish script not found: {script}")

    result = subprocess.run(
        [sys.executable, str(script), agent, "--upload"],
        cwd=script.parent,
        check=False,
    )
    if result.returncode != 0:
        raise SyncError("Bundle publish script failed.")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync an agent into the bundle repo.")
    parser.add_argument(
        "agent",
        nargs="?",
        help="Agent key (directory name) to sync. Required unless --all is provided.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Sync every agent found under tmates-platform/app/agents.",
    )
    parser.add_argument(
        "--version",
        help="Manifest version to set for both platform and bundle manifests.",
    )
    parser.add_argument(
        "--bump",
        choices=("major", "minor", "patch"),
        help="Increment manifest versions by one major/minor/patch step.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish the synced bundle via publish_agent_bundle.py --upload.",
    )
    return parser.parse_args(argv)


def discover_agent_keys() -> list[str]:
    """Enumerate agent directories that contain a manifest."""

    if not PLATFORM_AGENT_ROOT.is_dir():
        return []

    agent_keys = []
    for entry in PLATFORM_AGENT_ROOT.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith("__"):
            continue
        if not (entry / "manifest.yaml").is_file():
            continue
        agent_keys.append(entry.name)

    return sorted(agent_keys)


def read_manifest_version(manifest_path: Path) -> str:
    """Extract the version string from a manifest file."""

    if not manifest_path.is_file():
        raise SyncError(f"Manifest not found: {manifest_path}")

    for line in manifest_path.read_text().splitlines():
        match = re.match(r'\s*version:\s*"?([0-9]+(?:\.[0-9]+)*)"?', line)
        if match:
            return match.group(1)

    raise SyncError(f'"version" key not found in {manifest_path}')


def bump_version(version: str, step: str) -> str:
    """Bump a semantic version string by one step."""

    parts = [int(part) for part in version.split(".")]
    if len(parts) != 3:
        raise SyncError(f"Unsupported version format (expected x.y.z): {version}")

    major, minor, patch = parts
    if step == "major":
        major += 1
        minor = 0
        patch = 0
    elif step == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1

    return f"{major}.{minor}.{patch}"


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    if args.version and args.bump:
        print("[sync-agent] Use either --version or --bump, not both.", file=sys.stderr)
        return 1

    if args.all:
        agents = discover_agent_keys()
        if not agents:
            print("[sync-agent] No agents found to sync.", file=sys.stderr)
            return 1
    else:
        if not args.agent:
            print("[sync-agent] Provide an agent key or use --all.", file=sys.stderr)
            return 1
        agents = [args.agent.strip()]

    version_updates: dict[str, str] = {}

    try:
        for agent in agents:
            copy_agent_directory(agent)

            if args.version or args.bump:
                platform_manifest = PLATFORM_AGENT_ROOT / agent / "manifest.yaml"
                bundle_manifest = BUNDLE_AGENT_ROOT / agent / "manifest.yaml"

                if args.version:
                    new_version = args.version
                else:
                    current_version = read_manifest_version(platform_manifest)
                    new_version = bump_version(current_version, args.bump)

                update_manifest_version(platform_manifest, new_version)
                update_manifest_version(bundle_manifest, new_version)
                version_updates[agent] = new_version

            if args.publish:
                publish_bundle(agent)

    except SyncError as exc:
        print(f"[sync-agent] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[sync-agent] Unexpected error: {exc}", file=sys.stderr)
        return 1

    joined_agents = ", ".join(agents)
    print(f"[sync-agent] Synced agent(s): {joined_agents}.")
    if version_updates:
        for agent, version in version_updates.items():
            print(f"[sync-agent] {agent}: version -> {version}")
    if args.publish:
        print("[sync-agent] Bundle publish triggered.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
