"""The routing agent: given one surplus-food donation offer, autonomously picks a
food bank and a volunteer driver (or escalates), and confirms the match -- no human
approval step in the loop.
"""
import os

from strands import Agent
from strands.models import BedrockModel

from .tools import ALL_TOOLS

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


def build_agent() -> Agent:
    model = BedrockModel(
        model_id=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        temperature=0.2,
    )
    # No console callback handler: this runs inside the API server, and the SDK's default
    # handler streams raw model tokens to stdout, which crashes on Windows consoles (cp1252)
    # if the model emits non-ASCII characters.
    return Agent(model=model, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT, callback_handler=None)


def route_donation(donation: dict) -> str:
    """Run the agent end-to-end on one donation offer. Returns the agent's final message."""
    agent = build_agent()
    prompt = (
        f"New donation offer:\n"
        f"- donation_id: {donation['id']}\n"
        f"- donor: {donation['donor_name']} ({donation['zone']})\n"
        f"- category: {donation['category']}\n"
        f"- quantity_lbs: {donation['quantity_lbs']}\n"
        f"- pickup_window: {donation['pickup_window_start']} to {donation['pickup_window_end']}\n\n"
        f"Route this donation now."
    )
    result = agent(prompt)
    return str(result)
