"""Tools for the multi-agent routing system.

Architecture: a coordinator agent (agent.py) checks food safety, then delegates to two
specialist agents, each wrapped as a @tool via the Strands "agents-as-tools" pattern --
the coordinator never touches the raw lookup tools itself, it consults specialists and
then acts:

    coordinator
      -> check_food_safety_window          (perishability vs. pickup window)
      -> consult_matching_specialist       (specialist agent, tools=[list_food_bank_needs])
      -> consult_logistics_specialist      (specialist agent, tools=[list_available_drivers])
      -> create_match | escalate_donation

Every tool call is a real read or write against shared SQLite state -- the agents
are not summarizing static context, they're querying live capacity/need/availability
(with real great-circle distance, not zone-string matching -- see geo.py) and, on
create_match / escalate_donation, actually changing what the dashboard and the three
parties (donor, food bank, driver) see. Writes also publish to the SSE event bus so
the dashboard updates instantly instead of on the next poll.
"""
import re
import time
import uuid
from datetime import datetime

from strands import Agent, tool

from .data_store import event_bus, get_conn, load_needs, log_activity, row_to_dict
from .geo import haversine_miles
from .model import build_model

# Real perishability limits: how long each category can safely sit between pickup
# and delivery. Not decorative -- check_food_safety_window enforces it, and a donation
# can fail on this even when a food bank and driver would otherwise both work.
MAX_SAFE_HOURS = {
    "produce": 48,
    "bakery": 24,
    "dairy": 6,
    "prepared": 2,
}

_TIME_RE = re.compile(r"today\s+(\d{1,2}):(\d{2})\s*([ap]m)", re.IGNORECASE)


def _parse_today_time(text: str) -> datetime | None:
    m = _TIME_RE.search(text)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return datetime(2000, 1, 1, hour, minute)


@tool
def check_food_safety_window(category: str, window_start: str, window_end: str) -> str:
    """Check whether the donation's pickup window is safe for its food category, given
    real perishability limits -- dairy and prepared meals spoil far faster than produce
    or bakery. This can fail even when a food bank and driver are both otherwise available;
    call it before consulting the specialists, since a window that's already unsafe makes
    the rest of the search moot.

    Args:
        category: The donation's food category (produce, bakery, dairy, or prepared).
        window_start: Pickup window start, e.g. "Today 1:00pm".
        window_end: Pickup window end, e.g. "Today 5:00pm".
    """
    limit = MAX_SAFE_HOURS.get(category, 24)
    start, end = _parse_today_time(window_start), _parse_today_time(window_end)
    if start is None or end is None:
        return f"Could not parse the pickup window ({window_start} to {window_end}); proceed with caution -- {category} has a {limit}h safe handling limit."
    hours = (end - start).total_seconds() / 3600
    if hours <= 0:
        hours += 24
    if hours <= limit:
        return f"SAFE: window is {hours:.1f}h, within the {limit}h safe handling limit for {category}."
    return (
        f"UNSAFE: window is {hours:.1f}h, exceeding the {limit}h safe handling limit for {category} -- "
        f"food sitting this long risks spoilage even if a food bank and driver are otherwise available. "
        f"Strongly consider escalating rather than routing."
    )


@tool
def list_food_bank_needs(donor_id: str) -> list[dict]:
    """List all food banks with real distance in miles from the donor, current need level
    per category, and remaining capacity -- sorted nearest first.

    Args:
        donor_id: The donating organization's id (e.g. "d1").
    """
    conn = get_conn()
    donor = conn.execute("SELECT lat, lon FROM donors WHERE id = ?", (donor_id,)).fetchone()
    rows = conn.execute("SELECT * FROM food_banks").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = row_to_dict(r)
        d["needs"] = load_needs(d["needs"])
        if donor is not None:
            d["distance_miles"] = round(haversine_miles(donor["lat"], donor["lon"], d["lat"], d["lon"]), 1)
        out.append(d)
    if donor is not None:
        out.sort(key=lambda x: x["distance_miles"])
    return out


@tool
def list_available_drivers(origin_id: str, min_capacity_lbs: int = 0) -> list[dict]:
    """List available drivers with real distance in miles from the given origin --
    sorted nearest first.

    Args:
        origin_id: Where the pickup happens: a donor id (e.g. "d1") or a food bank id (e.g. "fb1").
        min_capacity_lbs: Only return drivers whose vehicle can carry at least this many pounds.
    """
    conn = get_conn()
    origin = conn.execute("SELECT lat, lon FROM donors WHERE id = ?", (origin_id,)).fetchone()
    if origin is None:
        origin = conn.execute("SELECT lat, lon FROM food_banks WHERE id = ?", (origin_id,)).fetchone()
    rows = conn.execute(
        "SELECT * FROM drivers WHERE status = 'available' AND vehicle_capacity_lbs >= ?",
        (min_capacity_lbs,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = row_to_dict(r)
        if origin is not None:
            d["distance_miles"] = round(haversine_miles(origin["lat"], origin["lon"], d["lat"], d["lon"]), 1)
        out.append(d)
    if origin is not None:
        out.sort(key=lambda x: x["distance_miles"])
    return out


MATCHING_SPECIALIST_PROMPT = """You are the Matching Specialist for a food-rescue network.
Given one donation (donor id, category, quantity), use list_food_bank_needs to see real
food banks with their actual distance in miles from the donor, current need level, and
remaining capacity, then recommend exactly ONE food bank.

Prefer a food bank whose need level for the category is "medium" or "high" and that has
enough remaining capacity for the quantity. Weigh real distance against fit: a modestly
farther food bank with high need beats a nearby one with low need, but don't recommend a
food bank so far away the trip stops making sense when a comparable closer option exists.

Reply with the food bank's id, name, distance in miles, and one sentence of reasoning. If
truly nothing fits (no food bank has enough capacity, or need for the category is low
everywhere), say so explicitly instead of forcing a pick.
"""

LOGISTICS_SPECIALIST_PROMPT = """You are the Logistics Specialist for a food-rescue network.
Given a pickup (origin, quantity, and the pickup time window), use list_available_drivers
to see real drivers with their actual distance in miles from the origin and vehicle
capacity, then recommend exactly ONE driver.

Prefer the closest driver whose vehicle capacity covers the quantity and who is available
within the time window. A farther driver with real spare capacity beats a closer one whose
vehicle can't actually carry the load.

Reply with the driver's id, name, distance in miles, and one sentence of reasoning. If truly
no driver qualifies (nobody available has enough capacity, or nobody's free in the window),
say so explicitly instead of forcing a pick.
"""


@tool
def consult_matching_specialist(donor_id: str, category: str, quantity_lbs: int) -> str:
    """Ask the Matching Specialist agent which food bank should receive this donation.
    The specialist independently looks up live food bank data (with real distances) before
    answering.

    Args:
        donor_id: The donating organization's id (e.g. "d1").
        category: The donation's food category (produce, bakery, dairy, or prepared).
        quantity_lbs: How many pounds are being donated.
    """
    # callback_handler=None: without it this nested Agent defaults to printing raw
    # model tokens to stdout, which crashes on Windows consoles (cp1252) the moment
    # the model emits a non-ASCII character -- same failure mode as the coordinator,
    # see agent.py.
    specialist = Agent(
        model=build_model(), system_prompt=MATCHING_SPECIALIST_PROMPT,
        tools=[list_food_bank_needs], callback_handler=None,
    )
    query = f"Donation from donor {donor_id}: {quantity_lbs} lbs of {category}. Which food bank should take this?"
    return str(specialist(query))


@tool
def consult_logistics_specialist(origin_id: str, quantity_lbs: int, window_start: str, window_end: str) -> str:
    """Ask the Logistics Specialist agent which driver should carry this pickup.
    The specialist independently looks up live driver availability (with real distances)
    before answering.

    Args:
        origin_id: Where the pickup happens -- the chosen food bank's id if the Matching
            Specialist found one, otherwise the donor's id.
        quantity_lbs: How many pounds need to be carried.
        window_start: Pickup window start, e.g. "Today 1:00pm".
        window_end: Pickup window end, e.g. "Today 5:00pm".
    """
    specialist = Agent(
        model=build_model(), system_prompt=LOGISTICS_SPECIALIST_PROMPT,
        tools=[list_available_drivers], callback_handler=None,
    )
    query = (
        f"Pickup near {origin_id}: {quantity_lbs} lbs, window {window_start} to {window_end}. "
        f"Which driver should carry this?"
    )
    return str(specialist(query))


@tool
def create_match(donation_id: str, food_bank_id: str, driver_id: str, pickup_time: str, reasoning: str) -> str:
    """Confirm a match: assign a donation to a food bank and a driver, mark the driver
    unavailable, and notify all three parties (donor, food bank, driver). This is a
    real, final action -- call it only once the specialists have identified a food bank
    and a driver that both actually work, and the food safety window check passed.

    Args:
        donation_id: The donation being routed.
        food_bank_id: The chosen food bank's id.
        driver_id: The chosen driver's id.
        pickup_time: Human-readable pickup time, e.g. "Today 2:30pm".
        reasoning: One or two sentences combining the specialists' reasoning.
    """
    conn = get_conn()
    match_id = str(uuid.uuid4())[:8]
    conn.execute(
        "INSERT INTO matches (id, donation_id, food_bank_id, driver_id, pickup_time, reasoning, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (match_id, donation_id, food_bank_id, driver_id, pickup_time, reasoning, time.time()),
    )
    conn.execute("UPDATE drivers SET status = 'assigned' WHERE id = ?", (driver_id,))

    donor_row = conn.execute(
        "SELECT donors.name AS donor_name, donations.quantity_lbs AS quantity_lbs "
        "FROM donations JOIN donors ON donors.id = donations.donor_id WHERE donations.id = ?",
        (donation_id,),
    ).fetchone()
    if donor_row is not None:
        conn.execute(
            "UPDATE food_banks SET capacity_lbs = MAX(0, capacity_lbs - ?) WHERE id = ?",
            (donor_row["quantity_lbs"], food_bank_id),
        )
    fb_row = conn.execute("SELECT name FROM food_banks WHERE id = ?", (food_bank_id,)).fetchone()
    driver_row = conn.execute("SELECT name FROM drivers WHERE id = ?", (driver_id,)).fetchone()

    donor_name = donor_row["donor_name"] if donor_row else donation_id
    fb_name = fb_row["name"] if fb_row else food_bank_id
    driver_name = driver_row["name"] if driver_row else driver_id

    conn.execute(
        "UPDATE donations SET status = 'matched', resolution_detail = ? WHERE id = ?",
        (f"-> {fb_name} via {driver_name} at {pickup_time}", donation_id),
    )
    conn.commit()
    conn.close()

    log_activity("agent", "matched", f"{reasoning} Pickup {pickup_time}.")
    log_activity(f"donor:{donation_id}", "notified", f"Pickup confirmed by {driver_name} at {pickup_time} for {fb_name}.")
    log_activity(f"food_bank:{food_bank_id}", "notified", f"Incoming delivery from {donor_name} via {driver_name} at {pickup_time}.")
    log_activity(f"driver:{driver_id}", "notified", f"Assigned pickup: {donor_name} -> {fb_name} at {pickup_time}.")

    event_bus.publish({
        "type": "match", "donation_id": donation_id, "food_bank_id": food_bank_id,
        "driver_id": driver_id, "pickup_time": pickup_time,
    })

    return f"Match {match_id} confirmed: {donor_name} -> {fb_name} via {driver_name} at {pickup_time}."


@tool
def escalate_donation(donation_id: str, reason: str) -> str:
    """Escalate a donation to the human coordinator because no viable food bank/driver
    match could be found, or the food safety window check failed. Use this only after
    both specialists have checked real alternatives (unless the safety check alone rules
    the donation out).

    Args:
        donation_id: The donation that could not be matched.
        reason: Why no match was found, combining the safety check and both specialists' findings.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE donations SET status = 'escalated', resolution_detail = ? WHERE id = ?",
        (reason, donation_id),
    )
    conn.commit()
    conn.close()
    log_activity("agent", "escalated", f"Donation {donation_id}: {reason}")
    event_bus.publish({"type": "escalated", "donation_id": donation_id})
    return f"Donation {donation_id} escalated to human coordinator: {reason}"


COORDINATOR_TOOLS = [
    check_food_safety_window,
    consult_matching_specialist,
    consult_logistics_specialist,
    create_match,
    escalate_donation,
]
