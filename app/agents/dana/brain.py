"""Runtime brain for Dana built on the shared TmatesAgentsSDK."""

from __future__ import annotations

from agents import ModelSettings
from openai.types.shared import Reasoning

from app.agents.dana import tools
from app.agents.dana.config import DEFAULT_MODEL
from app.agents.dana.prompts.brain.loader import load_brain_prompt
from app.sdk.agents.tmates_agents_sdk import TmatesAgentsSDK
from app.tools import build_create_pinboard_post_tool


_PINBOARD_CREATE_POST = build_create_pinboard_post_tool(agent_key="dana")


def _build_runtime() -> TmatesAgentsSDK:
    return TmatesAgentsSDK(
        agent_key="dana",
        name="Dana",
        handoff_description="Read, write and manage Gmail inbox",
        instructions_loader=load_brain_prompt,
        model=DEFAULT_MODEL,
        model_settings=ModelSettings(reasoning=Reasoning(effort="medium"), verbosity="medium"),
        tools=[
            tools.request_gmail_login_link,
            tools.gmail_connection_status,
            tools.gmail_search_messages,
            tools.gmail_read_message,
            tools.gmail_send_email,
            _PINBOARD_CREATE_POST,
        ],
    )


tmates_sdk = _build_runtime()
agent = tmates_sdk.agent
config = tmates_sdk.config
run_prompt = tmates_sdk.run_prompt
