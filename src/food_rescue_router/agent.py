"""The coordinator agent: given one surplus-food donation offer, delegates to a
Matching Specialist and a Logistics Specialist (each a separate Strands Agent,
wrapped as a tool -- see tools.py), then commits to a match or escalates. No
human approval step in the loop.
"""
import os

from strands import Agent

from .memory import get_session_manager
from .model import build_model
from .tools import COORDINATOR_TOOLS

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

You have memory of earlier donations you've routed in this session (via AgentCore
Memory) -- if a driver or food bank you're about to recommend was already committed
to another load moments ago, notice that rather than treating every donation as if it
were the network's first.

Write your final summary in a clear, professional tone: short prose or a compact
bullet list, minimal formatting. Do not use emoji.
"""


def build_agent() -> Agent:
    # No console callback handler: this runs inside the API server, and the SDK's default
    # handler streams raw model tokens to stdout, which crashes on Windows consoles (cp1252)
    # if the model emits non-ASCII characters.
    session_manager = get_session_manager(os.environ.get("AWS_REGION", "us-east-1"))
    return Agent(
        model=build_model(), tools=COORDINATOR_TOOLS, system_prompt=SYSTEM_PROMPT,
        callback_handler=None, session_manager=session_manager,
    )


def route_donation(donation: dict) -> str:
    """Run the coordinator end-to-end on one donation offer. Returns its final message."""
    agent = build_agent()
    prompt = (
        f"New donation offer:\n"
        f"- donation_id: {donation['id']}\n"
        f"- donor_id: {donation['donor_id']}\n"
        f"- donor: {donation['donor_name']} ({donation['zone']})\n"
        f"- category: {donation['category']}\n"
        f"- quantity_lbs: {donation['quantity_lbs']}\n"
        f"- pickup_window: {donation['pickup_window_start']} to {donation['pickup_window_end']}\n\n"
        f"Route this donation now."
    )
    result = agent(prompt)
    return str(result)
