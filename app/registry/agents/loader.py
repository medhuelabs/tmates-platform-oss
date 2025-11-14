"""Agent registration and discovery helpers."""

from __future__ import annotations

import importlib
import sys
from typing import Dict, Optional, Type

from app.config import CONFIG
from app.logger import log

from .base import AgentBase
from .bundle_manager import BUNDLE_MANAGER, BundleResolutionError, ResolvedBundle

_AGENT_CACHE: Dict[str, Type[AgentBase]] = {}
_CATALOG_CACHE: Dict[str, tuple[str, Type[AgentBase]]] = {}


def load_agent_class(agent_key: str) -> Type[AgentBase]:
    normalized = (agent_key or "").strip().casefold()
    if not normalized:
        raise ValueError("Agent key must be provided.")

    use_catalog = _should_use_catalog()
    resolved_bundle: ResolvedBundle | None = None

    if use_catalog:
        try:
            resolved_bundle = _refresh_catalog_bundle(normalized)
        except Exception as exc:  # pragma: no cover - defensive guard
            log(f"[agent-loader] catalog resolution failed for {agent_key}: {exc}")
            raise

        cached_entry = _CATALOG_CACHE.get(normalized)
        cached_cls = _AGENT_CACHE.get(normalized)
        if cached_entry and cached_cls and cached_entry[0] == resolved_bundle.version:
            return cached_cls
    else:
        cached_cls = _AGENT_CACHE.get(normalized)
        if cached_cls is not None:
            return cached_cls

    if use_catalog:
        agent_cls = _load_agent_class_from_catalog(normalized, resolved=resolved_bundle)
    else:
        agent_cls = _load_agent_class_from_filesystem(normalized)

    _AGENT_CACHE[normalized] = agent_cls
    return agent_cls


def _refresh_catalog_bundle(agent_key: str) -> ResolvedBundle:
    """Ensure cached metadata matches the active catalog version."""

    resolved = BUNDLE_MANAGER.prepare_bundle(agent_key)
    cached_entry = _CATALOG_CACHE.get(agent_key)
    cached_version = cached_entry[0] if cached_entry else None

    if cached_version and cached_version != resolved.version:
        _AGENT_CACHE.pop(agent_key, None)
        _CATALOG_CACHE.pop(agent_key, None)
        _purge_agent_modules(f"app.agents.{agent_key}")

    return resolved


def _should_use_catalog() -> bool:
    catalog_enabled = getattr(CONFIG, "agent_catalog_enabled", False)
    is_dev = getattr(CONFIG, "is_development", False)
    return bool(catalog_enabled and not is_dev)


def _load_agent_class_from_catalog(
    agent_key: str,
    *,
    resolved: ResolvedBundle | None = None,
) -> Type[AgentBase]:
    bundle = resolved or BUNDLE_MANAGER.prepare_bundle(agent_key)

    cached_entry = _CATALOG_CACHE.get(agent_key)
    if cached_entry and cached_entry[0] == bundle.version:
        return cached_entry[1]

    module_prefix = f"app.agents.{agent_key}"
    _purge_agent_modules(module_prefix)

    module = importlib.import_module(f"{module_prefix}.agent")
    agent_cls = getattr(module, "AGENT_CLASS", None) or _find_agent_class(module)
    if agent_cls is None:
        raise BundleResolutionError(
            f"Bundle for {agent_key}@{bundle.version} does not expose AGENT_CLASS or a valid AgentBase subclass"
        )
    if not issubclass(agent_cls, AgentBase):
        raise TypeError(f"{agent_cls!r} loaded from catalog is not a subclass of AgentBase.")

    _CATALOG_CACHE[agent_key] = (bundle.version, agent_cls)
    return agent_cls


def _load_agent_class_from_filesystem(agent_key: str) -> Type[AgentBase]:
    module_candidates = (
        f"app.agents.{agent_key}.agent",
        f"app.agents.{agent_key}.worker",
        f"agents.{agent_key}.agent",
        f"agents.{agent_key}.worker",
    )

    for module_name in module_candidates:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue

        agent_cls = getattr(module, "AGENT_CLASS", None)
        if agent_cls is None:
            agent_cls = _find_agent_class(module)
        if agent_cls is None:
            continue

        if not issubclass(agent_cls, AgentBase):
            raise TypeError(f"{agent_cls!r} is not a subclass of AgentBase.")

        return agent_cls

    raise RuntimeError(
        f"No agent class found for key '{agent_key}'. Ensure agents/{agent_key}/agent.py "
        "defines a subclass of AgentBase or exports AGENT_CLASS."
    )


def _purge_agent_modules(module_prefix: str) -> None:
    removal_targets = [name for name in sys.modules if name == module_prefix or name.startswith(f"{module_prefix}.")]
    for name in removal_targets:
        sys.modules.pop(name, None)


def _find_agent_class(module) -> Optional[Type[AgentBase]]:
    for attr in dir(module):
        obj = getattr(module, attr)
        if isinstance(obj, type) and issubclass(obj, AgentBase) and obj is not AgentBase:
            return obj
    return None


def create_agent(agent_key: str, *, user_context=None) -> AgentBase:
    agent_cls = load_agent_class(agent_key)
    return agent_cls(user_context=user_context)
