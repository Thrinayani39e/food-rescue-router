"""AgentCore Runtime entrypoint for the food-rescue routing agent.

This is a standalone deployment of the same routing agent, tools, and system prompt
used by the local dashboard app (../../../src/food_rescue_router) -- see the sibling
food_rescue_router/ package here, which is a deployable copy (AgentCore Runtime
packages this app directory in isolation, so it can't import across the repo's
../../../src path). It seeds its own synthetic donors/food banks/drivers on cold
start and keeps state in the container's /tmp for the life of the runtime instance.

Invoke with a payload like:
    {"prompt": "New donation offer:\\n- donation_id: d123\\n- donor: Green Aisle Market (Downtown)\\n"
                "- category: produce\\n- quantity_lbs: 60\\n- pickup_window: Today 1pm to Today 5pm\\n\\n"
                "Route this donation now."}
"""
from collections import OrderedDict
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager
from strands.models.bedrock import BedrockModel

from food_rescue_router.seed_data import seed_if_empty
from food_rescue_router.tools import ALL_TOOLS

app = BedrockAgentCoreApp()
log = app.logger

seed_if_empty()

SYSTEM_PROMPT = """You are the routing agent for a city food-rescue network. You receive one
surplus-food donation offer at a time and must autonomously get it to a food bank that
needs it, via a volunteer driver who can carry it there in time.

Rules:
- Prefer a food bank whose need level for the donation's category is "medium" or "high",
  and that has enough remaining capacity for the quantity offered.
- Prefer a driver in the same zone as the donor (or the matched food bank) whose vehicle
  capacity covers the donation's weight, and who is available within the pickup window.
- Use list_food_bank_needs and list_available_drivers to see current, real options before
  deciding -- do not guess.
- Once you've picked the best food bank and driver, call create_match exactly once to
  confirm it. Pick a pickup_time inside the donation's pickup window.
- If, after checking real alternatives, nothing viable exists (no food bank wants/fits the
  category, or no driver is available/capable), call escalate_donation with a specific reason.
- Always end by having called either create_match or escalate_donation -- never leave a
  donation unresolved.
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
            model=BedrockModel(model_id="us.anthropic.claude-sonnet-4-6"),
            system_prompt=SYSTEM_PROMPT,
            tools=ALL_TOOLS,
            conversation_manager=_make_conversation_manager(),
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
