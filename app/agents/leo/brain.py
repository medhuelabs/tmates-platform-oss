import asyncio
import logging
from typing import Annotated, Any, Dict, Optional

import requests
from agents import ModelSettings, RunContextWrapper, function_tool
from openai.types.shared import Reasoning

from app.agents.leo.prompts.brain.loader import load_brain_prompt
from app.agents.leo.tools import (
    AspectRatio,
    GenerateImageResult,
    ImageSize,
    PersonGenerationPolicy,
    _extract_user_identifier,
    generate_image_tool,
)
from app.core.api_urls import build_api_url
from app.sdk.agents.tmates_agents_sdk import TmatesAgentsSDK
from app.tools import build_create_pinboard_post_tool


_PINBOARD_CREATE_POST = build_create_pinboard_post_tool(agent_key="leo")
logger = logging.getLogger(__name__)


@function_tool
async def generate_image(
    ctx: RunContextWrapper[Any],
    prompt: Annotated[str, "Detailed description of the image to create."],
    model: Annotated[
        Optional[str],
        "Imagen model identifier such as `imagen-3.0-generate-002`.",
    ] = None,
    prompt_img: Annotated[
        Optional[str],
        "Base64-encoded reference image to guide generation (optional).",
    ] = None,
    prompt_img_mime_type: Annotated[
        Optional[str],
        "MIME type for the reference image when provided (e.g., `image/png`).",
    ] = None,
    aspect_ratio: Annotated[
        Optional[AspectRatio],
        "Aspect ratio preset supported by Imagen (e.g., `1:1`, `16:9`).",
    ] = None,
    image_size: Annotated[
        Optional[ImageSize],
        "Resolution preset for Imagen 4 models (for example, `1K`, `2K`).",
    ] = None,
    number_of_images: Annotated[
        int,
        "How many images to request (1-4).",
    ] = 1,
    person_generation: Annotated[
        Optional[PersonGenerationPolicy],
        "Person generation policy (`dont_allow`, `allow_adult`, or `allow_all`).",
    ] = None,
) -> GenerateImageResult:
    """Generate image assets with the Gemini Imagen API and persist them."""

    return await generate_image_tool(
        ctx,
        prompt=prompt,
        model=model,
        prompt_image_base64=prompt_img,
        prompt_image_mime_type=prompt_img_mime_type,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        number_of_images=number_of_images,
        person_generation=person_generation,
    )


@function_tool
async def announce_plan(
    ctx: RunContextWrapper[Any],
    message: Annotated[str, "Summary of the generation plan to share with the user."],
    parameters: Annotated[Optional[str], "Optional plain-text details about the settings Leo will use."] = None,
) -> str:
    """Inform the user about the planned image generation settings."""

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
        "agent_key": "leo",
        "user_id": user_id,
        "result_data": plan_message,
        "task_type": "chat",
        "metadata": {
            "thread_id": thread_id,
            "intermediate": True,
            "parameters_text": formatted_parameters,
            "next_status": {
                "status": "agent_typing",
                "data": {"agent": "leo", "stage": "generating"},
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
    agent_key="leo",
    name="Leo",
    handoff_description="Generate and manage images using AI Gemini Imagen",
    instructions_loader=load_brain_prompt,
    model="gpt-5-mini",
    model_settings=ModelSettings(reasoning=Reasoning(effort="low"), verbosity="low"),
    tools=[generate_image, announce_plan, _PINBOARD_CREATE_POST],
)

agent = tmates_sdk.agent
config = tmates_sdk.config
run_prompt = tmates_sdk.run_prompt
