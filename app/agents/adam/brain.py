from agents import ModelSettings
from openai.types.shared import Reasoning

from app.agents.adam.prompts.brain.loader import load_brain_prompt
from app.sdk.agents.tmates_agents_sdk import TmatesAgentsSDK
from app.tools import build_create_pinboard_post_tool


_PINBOARD_CREATE_POST = build_create_pinboard_post_tool(agent_key="adam")


def _build_runtime() -> TmatesAgentsSDK:
    return TmatesAgentsSDK(
        agent_key="adam",
        name="Adam",
        handoff_description="Chat with the user",
        instructions_loader=load_brain_prompt,
        model="gpt-5-mini",
        model_settings=ModelSettings(reasoning=Reasoning(effort="low"), verbosity="low"),
        tools=[_PINBOARD_CREATE_POST],
    )


tmates_sdk = _build_runtime()
agent = tmates_sdk.agent
config = tmates_sdk.config
run_prompt = tmates_sdk.run_prompt
