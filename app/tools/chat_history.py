"""Shared chat history tool for agents."""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Any, Dict, List, Optional

import requests
from agents import FunctionTool, RunContextWrapper, function_tool

from app.core.api_urls import build_api_url

USER_ID_KEYS: tuple[str, ...] = (
    "user_id",
    "auth_user_id",
    "supabase_user_id",
    "id",
    "uid",
)


def _extract_from_mapping(mapping: Any) -> Optional[str]:
    if not isinstance(mapping, dict):
        return None
    for key in USER_ID_KEYS:
        candidate = mapping.get(key)
        if candidate:
            return str(candidate)
    return None


def _extract_user_id(ctx: RunContextWrapper[Any]) -> str:
    for attr in ("user_id", "auth_user_id", "supabase_user_id"):
        value = getattr(ctx, attr, None)
        if value:
            return str(value)

    for attr in ("context", "metadata", "state"):
        mapping = getattr(ctx, attr, None)
        candidate = _extract_from_mapping(mapping)
        if candidate:
            return candidate

    env_user = os.getenv("USER_ID") or os.getenv("AUTH_USER_ID")
    if env_user:
        return env_user

    raise RuntimeError("Unable to determine user identity for chat history requests.")


def _extract_thread_id(ctx: RunContextWrapper[Any]) -> str:
    context_mapping = getattr(ctx, "context", {}) or {}
    metadata = context_mapping.get("metadata") or {}
    candidates = [
        context_mapping.get("thread_id"),
        metadata.get("thread_id"),
        metadata.get("thread"),
        metadata.get("threadId"),
        os.getenv("THREAD_ID"),
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate)
    raise RuntimeError("thread_id is required to read chat history.")


def _extract_job_id(ctx: RunContextWrapper[Any]) -> Optional[str]:
    context_mapping = getattr(ctx, "context", {}) or {}
    metadata = context_mapping.get("metadata") or {}
    return (
        context_mapping.get("job_id")
        or metadata.get("job_id")
        or os.getenv("JOB_ID")
    )


def build_read_chat_history_tool(*, agent_key: str, max_limit: int = 20) -> FunctionTool:
    """Create a tool that allows agents to inspect the recent chat transcript."""

    if not agent_key:
        raise ValueError("agent_key is required for chat history tool.")

    api_url = build_api_url("v1", "internal", "chat-history")

    def _cache_history_attachments(ctx: RunContextWrapper[Any], messages: List[Dict[str, Any]]) -> None:
        context_mapping = getattr(ctx, "context", None)
        if not isinstance(context_mapping, dict):
            return

        bucket: List[Dict[str, Any]] = context_mapping.setdefault("history_attachments", [])
        seen_keys = {
            _attachment_identity(entry)
            for entry in bucket
            if isinstance(entry, dict)
        }

        for message in messages:
            attachments = message.get("attachments") or []
            if not isinstance(attachments, list):
                continue
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                identity = _attachment_identity(attachment)
                if identity in seen_keys:
                    continue
                seen_keys.add(identity)
                bucket.append(dict(attachment))

    def _attachment_identity(entry: Dict[str, Any]) -> str:
        for key in ("relative_path", "download_url", "uri", "name", "filename"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return str(id(entry))

    @function_tool
    async def read_chat_history_tool(
        ctx: RunContextWrapper[Any],
        limit: Annotated[int, "Number of recent messages to fetch (oldest->newest order)."] = 10,
    ) -> Dict[str, Any]:
        user_id = _extract_user_id(ctx)
        thread_id = _extract_thread_id(ctx)
        job_id = _extract_job_id(ctx)

        bounded_limit = max(1, min(limit, max_limit))

        payload = {
            "job_id": job_id,
            "agent_key": agent_key,
            "user_id": user_id,
            "thread_id": thread_id,
            "limit": bounded_limit,
        }

        def _request() -> Dict[str, Any]:
            response = requests.post(api_url, json=payload, timeout=15)
            response.raise_for_status()
            return response.json()

        response = await asyncio.to_thread(_request)
        messages = response.get("messages") if isinstance(response, dict) else None
        if isinstance(messages, list):
            _cache_history_attachments(ctx, messages)
        return response

    return read_chat_history_tool


__all__ = ["build_read_chat_history_tool"]
