"""The coordinator agent: given one surplus-food donation offer, delegates to a
Matching Specialist and a Logistics Specialist (each a separate Strands Agent,
wrapped as a tool -- see tools.py), then commits to a match or escalates. No
human approval step in the loop.
"""
from strands import Agent

from .model import build_model
from .tools import COORDINATOR_TOOLS

SYSTEM_PROMPT = """You are the coordinator for a city food-rescue network. You receive one
surplus-food donation offer at a time and must autonomously get it to a food bank that
needs it, via a volunteer driver who can carry it there in time.

You do not look up food banks or drivers yourself -- you delegate:
1. Call consult_matching_specialist with the donation's category, quantity, and donor zone.
   It will name one food bank (or say none fit).
2. Call consult_logistics_specialist with the quantity, the pickup zone (the food bank's zone
   if one was found, otherwise the donor's zone), and the donation's pickup window. It will
   name one driver (or say none qualify).
3. If both specialists found a real fit: call create_match exactly once, with a pickup_time
   inside the donation's window and reasoning that combines both specialists' points.
4. If either specialist found nothing viable: call escalate_donation with a specific reason
   combining what both specialists found.

Always end by having called either create_match or escalate_donation -- never leave a
donation unresolved, and never skip consulting both specialists first.

Write your final summary in a clear, professional tone: short prose or a compact
bullet list, minimal formatting. Do not use emoji.
"""


def build_agent() -> Agent:
    # No console callback handler: this runs inside the API server, and the SDK's default
    # handler streams raw model tokens to stdout, which crashes on Windows consoles (cp1252)
    # if the model emits non-ASCII characters.
    return Agent(model=build_model(), tools=COORDINATOR_TOOLS, system_prompt=SYSTEM_PROMPT, callback_handler=None)


def route_donation(donation: dict) -> str:
    """Run the coordinator end-to-end on one donation offer. Returns its final message."""
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
