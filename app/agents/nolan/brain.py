import asyncio
import logging
from typing import Annotated, Any, Dict, Optional

import requests
from agents import ModelSettings, RunContextWrapper, function_tool
from openai.types.shared import Reasoning

from app.agents.nolan.prompts.brain.loader import load_brain_prompt
from app.agents.nolan.tools import (
    GenerateVideoResult,
    _extract_user_identifier,
    generate_video_tool,
    remix_video_tool,
)
from app.core.api_urls import build_api_url
from app.sdk.agents.tmates_agents_sdk import TmatesAgentsSDK
from app.tools import build_create_pinboard_post_tool


_PINBOARD_CREATE_POST = build_create_pinboard_post_tool(agent_key="nolan")
logger = logging.getLogger(__name__)


@function_tool
async def generate_video(
    ctx: RunContextWrapper[Any],
    prompt: Annotated[str, "Detailed description of the video to create."],
    model: Annotated[
        Optional[str],
        "Video model identifier such as `sora-2` or `sora-2-pro`.",
    ] = None,
    size: Annotated[
        Optional[str],
        "Target resolution in WIDTHxHEIGHT format (e.g., `1280x720`).",
    ] = None,
    seconds: Annotated[
        Optional[int],
        "Desired length of the clip in seconds.",
    ] = None,
) -> GenerateVideoResult:
    """Generate a video with the OpenAI Sora API and persist it."""

    return await generate_video_tool(
        ctx,
        prompt=prompt,
        model=model,
        size=size,
        seconds=seconds,
    )


@function_tool
async def remix_video(
    ctx: RunContextWrapper[Any],
    prompt: Annotated[str, "Describe the targeted change to apply to the existing video."],
    source_video_id: Annotated[
        Optional[str],
        "Previously generated video ID to remix; defaults to the latest Nolan video in this thread.",
    ] = None,
) -> GenerateVideoResult:
    """Apply a targeted remix to an existing Nolan video."""

    return await remix_video_tool(
        ctx,
        prompt=prompt,
        source_video_id=source_video_id,
    )


@function_tool
async def announce_plan(
    ctx: RunContextWrapper[Any],
    message: Annotated[str, "Summary of the generation plan to share with the user."],
    parameters: Annotated[Optional[str], "Optional plain-text details about the settings Nolan will use."] = None,
) -> str:
    """Inform the user about the planned video generation settings."""

    original_message = message.strip()
    plan_message = original_message
    formatted_parameters = parameters.strip() if isinstance(parameters, str) else None

    if formatted_parameters:
        entries: list[tuple[str, str]] = []
        for raw_line in formatted_parameters.splitlines():
            cleaned = raw_line.strip().lstrip("-•").strip()
            if not cleaned:
                continue
            if ":" in cleaned:
                key, value = cleaned.split(":", 1)
                entries.append((key.strip(), value.strip()))
            else:
                entries.append(("", cleaned))

        if entries:
            lead_clause = original_message.split("Settings:", 1)[0].strip()
            lead_clause = lead_clause.split("settings:", 1)[0].strip()
            lead_clause = lead_clause.lstrip("Plan:- ").strip()
            if lead_clause and not lead_clause.endswith("."):
                lead_clause = f"{lead_clause}."
            if lead_clause:
                stripped_lead = lead_clause.lstrip()
                if stripped_lead:
                    lead_clause = lead_clause[: len(lead_clause) - len(stripped_lead)] + stripped_lead[0].upper() + stripped_lead[1:]

            intro_phrases = [
                "Here's how I'll proceed.",
                "This is the plan I'm lining up.",
                "I'll take this approach.",
                "Let me walk you through what I'll do.",
            ]
            intro_index = sum(ord(char) for char in original_message) % len(intro_phrases)
            intro_sentence = intro_phrases[intro_index]

            summary_sentence = lead_clause if lead_clause else original_message.split(".", 1)[0].strip()
            if summary_sentence and not summary_sentence.endswith("."):
                summary_sentence = f"{summary_sentence}."

            intro_clean = intro_sentence.strip()
            summary_clean = summary_sentence.strip()
            if summary_clean:
                plan_message = f"{intro_clean}\n{summary_clean}"
            else:
                plan_message = intro_clean or intro_sentence

            bullet_lines: list[str] = []
            seen_pairs: set[str] = set()
            for key, value in entries:
                label = key or ""
                content = value
                if not content:
                    continue

                normalized_key = label.strip().title() if label else ""
                normalized_pair = f"{normalized_key.lower()}:{content.strip().lower()}"
                if normalized_pair in seen_pairs:
                    continue
                seen_pairs.add(normalized_pair)

                if not normalized_key:
                    bullet_lines.append(f"- {content.strip()}")
                else:
                    bullet_lines.append(f"- {normalized_key}: {content.strip()}")

            if bullet_lines:
                plan_message = f"{plan_message}\nSettings:\n" + "\n".join(bullet_lines)

    context_payload = getattr(ctx, "context", {}) or {}
    thread_id = context_payload.get("thread_id") or context_payload.get("metadata", {}).get("thread_id")
    job_id = context_payload.get("job_id") or context_payload.get("metadata", {}).get("job_id")

    try:
        user_id = _extract_user_identifier(ctx)
    except RuntimeError as exc:
        logger.warning("announce_plan missing user identifier: %s", exc)
        return "Unable to share plan without user context."

    if not thread_id:
        logger.warning("announce_plan missing thread_id in context; skipping notification")
        return "Plan noted locally."

    payload: Dict[str, Any] = {
        "job_id": job_id or f"plan-{thread_id}",
        "agent_key": "nolan",
        "user_id": user_id,
        "result_data": plan_message,
        "task_type": "chat",
        "metadata": {
            "thread_id": thread_id,
            "intermediate": True,
            "parameters_text": formatted_parameters,
            "next_status": {
                "status": "agent_typing",
                "data": {"agent": "nolan", "stage": "rendering"},
            },
        },
        "intermediate": True,
    }

    def _post_plan() -> None:
        try:
            response = requests.post(
                build_api_url("v1", "internal", "agent-result"),
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
        except Exception as error:  # noqa: BLE001 - external call
            logger.error("Failed to post plan message: %s", error)
            raise

    try:
        await asyncio.to_thread(_post_plan)
        return "Shared plan with the user."
    except Exception:
        return "Failed to notify the user about the plan."


tmates_sdk = TmatesAgentsSDK(
    agent_key="nolan",
    name="Nolan",
    handoff_description=(
        "Video creation and editing assistant specialized in generating and remixing video content based on user prompts."
    ),
    instructions_loader=load_brain_prompt,
    model="gpt-5-mini",
    model_settings=ModelSettings(reasoning=Reasoning(effort="low"), verbosity="low"),
    tools=[generate_video, remix_video, announce_plan, _PINBOARD_CREATE_POST],
)

agent = tmates_sdk.agent
config = tmates_sdk.config
run_prompt = tmates_sdk.run_prompt
