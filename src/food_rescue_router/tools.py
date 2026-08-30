"""Tools for the multi-agent routing system.

Architecture: a coordinator agent (agent.py) delegates to two specialist agents,
each wrapped as a @tool via the Strands "agents-as-tools" pattern -- the coordinator
never touches the raw lookup tools itself, it consults specialists and then acts:

    coordinator
      -> consult_matching_specialist   (specialist agent, tools=[list_food_bank_needs])
      -> consult_logistics_specialist  (specialist agent, tools=[list_available_drivers])
      -> create_match | escalate_donation

Every tool call is a real read or write against shared SQLite state -- the agents
are not summarizing static context, they're querying live capacity/need/availability
and, on create_match / escalate_donation, actually changing what the dashboard and
the three parties (donor, food bank, driver) see. Writes also publish to the SSE
event bus so the dashboard updates instantly instead of on the next poll.
"""
import time
import uuid

from strands import Agent, tool

from .data_store import event_bus, get_conn, load_needs, log_activity, row_to_dict
from .model import build_model


@tool
def list_food_bank_needs(zone: str | None = None) -> list[dict]:
    """List food banks and their current need level per category and remaining capacity.

    Args:
        zone: Optional neighborhood zone to filter by (e.g. "Downtown"). Omit to list all.
    """
    conn = get_conn()
    if zone:
        rows = conn.execute("SELECT * FROM food_banks WHERE zone = ?", (zone,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM food_banks").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = row_to_dict(r)
        d["needs"] = load_needs(d["needs"])
        out.append(d)
    return out


@tool
def list_available_drivers(zone: str | None = None, min_capacity_lbs: int = 0) -> list[dict]:
    """List volunteer drivers who are currently available, optionally filtered by zone
    and minimum vehicle capacity.

    Args:
        zone: Optional neighborhood zone to filter by (e.g. "Downtown"). Omit to list all.
        min_capacity_lbs: Only return drivers whose vehicle can carry at least this many pounds.
    """
    conn = get_conn()
    query = "SELECT * FROM drivers WHERE status = 'available' AND vehicle_capacity_lbs >= ?"
    params: list = [min_capacity_lbs]
    if zone:
        query += " AND zone = ?"
        params.append(zone)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


MATCHING_SPECIALIST_PROMPT = """You are the Matching Specialist for a food-rescue network.
Given one donation (category, quantity, donor zone), use list_food_bank_needs to see real,
current food bank need levels and remaining capacity, then recommend exactly ONE food bank.

Prefer a food bank whose need level for the category is "medium" or "high" and that has
enough remaining capacity for the quantity. Prefer the donor's own zone when a same-zone
option is viable, for a shorter route -- but a better-fit food bank in another zone beats a
poor-fit same-zone one.

Reply with the food bank's id, name, and zone, and one sentence of reasoning. If truly
nothing fits (no food bank has enough capacity, or need for the category is low everywhere),
say so explicitly instead of forcing a pick.
"""

LOGISTICS_SPECIALIST_PROMPT = """You are the Logistics Specialist for a food-rescue network.
Given a pickup (quantity, pickup zone, and the pickup time window), use list_available_drivers
to see real, current driver availability and vehicle capacity, then recommend exactly ONE driver.

Prefer a driver in the pickup zone whose vehicle capacity covers the quantity and who is
available within the time window. A driver from another zone with real spare capacity beats
a same-zone driver whose vehicle can't actually carry the load.

Reply with the driver's id, name, and zone, and one sentence of reasoning. If truly no driver
qualifies (nobody available has enough capacity, or nobody's free in the window), say so
explicitly instead of forcing a pick.
"""


@tool
def consult_matching_specialist(category: str, quantity_lbs: int, donor_zone: str) -> str:
    """Ask the Matching Specialist agent which food bank should receive this donation.
    The specialist independently looks up live food bank data before answering.

    Args:
        category: The donation's food category (produce, bakery, dairy, or prepared).
        quantity_lbs: How many pounds are being donated.
        donor_zone: The donor's neighborhood zone.
    """
    # callback_handler=None: without it this nested Agent defaults to printing raw
    # model tokens to stdout, which crashes on Windows consoles (cp1252) the moment
    # the model emits a non-ASCII character -- same failure mode as the coordinator,
    # see agent.py.
    specialist = Agent(
        model=build_model(), system_prompt=MATCHING_SPECIALIST_PROMPT,
        tools=[list_food_bank_needs], callback_handler=None,
    )
    query = f"Donation: {quantity_lbs} lbs of {category}, donor zone {donor_zone}. Which food bank should take this?"
    return str(specialist(query))


@tool
def consult_logistics_specialist(quantity_lbs: int, pickup_zone: str, window_start: str, window_end: str) -> str:
    """Ask the Logistics Specialist agent which driver should carry this pickup.
    The specialist independently looks up live driver availability before answering.

    Args:
        quantity_lbs: How many pounds need to be carried.
        pickup_zone: The zone the pickup happens in (usually the donor's or chosen food bank's zone).
        window_start: Pickup window start, e.g. "Today 1:00pm".
        window_end: Pickup window end, e.g. "Today 5:00pm".
    """
    specialist = Agent(
        model=build_model(), system_prompt=LOGISTICS_SPECIALIST_PROMPT,
        tools=[list_available_drivers], callback_handler=None,
    )
    query = (
        f"Pickup: {quantity_lbs} lbs, zone {pickup_zone}, window {window_start} to {window_end}. "
        f"Which driver should carry this?"
    )
    return str(specialist(query))


@tool
def create_match(donation_id: str, food_bank_id: str, driver_id: str, pickup_time: str, reasoning: str) -> str:
    """Confirm a match: assign a donation to a food bank and a driver, mark the driver
    unavailable, and notify all three parties (donor, food bank, driver). This is a
    real, final action -- call it only once the specialists have identified a food bank
    and a driver that both actually work.

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
    match could be found (e.g. everyone in range is over capacity or unavailable before
    the food expires). Use this only after both specialists have checked real alternatives.

    Args:
        donation_id: The donation that could not be matched.
        reason: Why no match was found, combining both specialists' findings.
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


COORDINATOR_TOOLS = [consult_matching_specialist, consult_logistics_specialist, create_match, escalate_donation]
