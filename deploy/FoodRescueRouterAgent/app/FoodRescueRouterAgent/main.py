"""AgentCore Runtime entrypoint for the food-rescue routing coordinator.

This is a standalone deployment of the same multi-agent system (coordinator +
Matching Specialist + Logistics Specialist, see food_rescue_router/tools.py) used by
the local dashboard app (../../../src/food_rescue_router) -- see the sibling
food_rescue_router/ package here, which is a deployable copy (AgentCore Runtime
packages this app directory in isolation, so it can't import across the repo's
../../../src path). It seeds its own synthetic donors/food banks/drivers on cold
start and keeps state in the container's /tmp for the life of the runtime instance.

Invoke with a payload like:
    {"prompt": "New donation offer:\\n- donation_id: d123\\n- donor_id: d1\\n"
                "- donor: Green Aisle Market (Downtown)\\n"
                "- category: produce\\n- quantity_lbs: 60\\n- pickup_window: Today 1pm to Today 5pm\\n\\n"
                "Route this donation now."}
"""
import os
from collections import OrderedDict
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager

from food_rescue_router.memory import get_session_manager
from food_rescue_router.model import build_model
from food_rescue_router.seed_data import seed_if_empty
from food_rescue_router.tools import COORDINATOR_TOOLS

app = BedrockAgentCoreApp()
log = app.logger

seed_if_empty()

SYSTEM_PROMPT = """You are the coordinator for a city food-rescue network. You receive one
surplus-food donation offer at a time and must autonomously get it to a food bank that
needs it, via a volunteer driver who can carry it there in time.

Work in this order:
1. Call check_food_safety_window with the donation's category and pickup window. If it
   comes back UNSAFE, that alone can justify escalating -- perishable food sitting past
   its safe handling limit is a real problem even if a food bank and driver are free.
2. Call consult_matching_specialist with the donor id, category, and quantity. It uses
   real distances, not zone labels, and will name one food bank (or say none fit).
3. Call consult_logistics_specialist with the origin id (the chosen food bank's id if one
   was found, otherwise the donor's id), the quantity, and the donation's pickup window.
   It will name one driver (or say none qualify).
4. If the safety check passed and both specialists found a real fit: call create_match
   exactly once, with a pickup_time inside the donation's window and reasoning that
   combines the safety check and both specialists' points.
5. If the safety check failed, or either specialist found nothing viable: call
   escalate_donation with a specific reason combining whatever was found.

Always end by having called either create_match or escalate_donation -- never leave a
donation unresolved, and never skip the safety check or either specialist.

Write your final summary in a clear, professional tone: short prose or a compact
bullet list, minimal formatting. Do not use emoji.
"""


def _make_conversation_manager():
    return NullConversationManager()


# Reuses one Agent per session_id so each session keeps its own in-process
# conversation history (best-effort; resets on cold start). The cache is bounded
# to 128 sessions with LRU eviction so a single process serving many sessions
# cannot leak history between them or grow without limit.
def agent_factory():
    cache = OrderedDict()

    def get_or_create_agent(session_id):
        if session_id in cache:
            cache.move_to_end(session_id)
            return cache[session_id]
        if len(cache) >= 128:
            cache.popitem(last=False)
        cache[session_id] = Agent(
            model=build_model(),
            system_prompt=SYSTEM_PROMPT,
            tools=COORDINATOR_TOOLS,
            conversation_manager=_make_conversation_manager(),
            session_manager=get_session_manager(os.environ.get("AWS_REGION", "us-east-1")),
            callback_handler=None,
        )
        return cache[session_id]

    return get_or_create_agent


get_or_create_agent = agent_factory()


def _extract_prompt(payload: dict):
    """Accept validated harness messages or a plain prompt string."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if "messages" in payload:
        return payload["messages"]
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    return prompt


@app.entrypoint
async def invoke(payload: dict, context: Any):
    log.info("Invoking food-rescue routing agent...")

    session_id = getattr(context, "session_id", "default-session")
    agent = get_or_create_agent(session_id)
    prompt = _extract_prompt(payload)

    async for event in agent.stream_async(prompt):
        if not isinstance(event, dict) or "event" not in event:
            continue
        cbs = event["event"].get("contentBlockStart")
        if cbs is not None and not cbs.get("start"):
            continue
        yield event


if __name__ == "__main__":
    app.run()
