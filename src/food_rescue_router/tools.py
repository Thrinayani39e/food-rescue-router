"""Tools the routing agent uses to see the world and take action.

Each @tool is a real read or write against the shared SQLite state -- the agent
is not summarizing static context, it is querying live capacity/need/availability
and, on create_match / escalate_donation, actually changing what the dashboard
and the three parties (donor, food bank, driver) see.
"""
import time
import uuid

from strands import tool

from .data_store import get_conn, load_needs, log_activity, row_to_dict


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


@tool
def create_match(donation_id: str, food_bank_id: str, driver_id: str, pickup_time: str, reasoning: str) -> str:
    """Confirm a match: assign a donation to a food bank and a driver, mark the driver
    unavailable, and notify all three parties (donor, food bank, driver). This is a
    real, final action -- call it only once you have picked the best food bank and driver.

    Args:
        donation_id: The donation being routed.
        food_bank_id: The chosen food bank's id.
        driver_id: The chosen driver's id.
        pickup_time: Human-readable pickup time, e.g. "Today 2:30pm".
        reasoning: One or two sentences on why this food bank and driver were chosen.
    """
    conn = get_conn()
    match_id = str(uuid.uuid4())[:8]
    conn.execute(
        "INSERT INTO matches (id, donation_id, food_bank_id, driver_id, pickup_time, reasoning, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (match_id, donation_id, food_bank_id, driver_id, pickup_time, reasoning, time.time()),
    )
    conn.execute("UPDATE donations SET status = 'matched' WHERE id = ?", (donation_id,))
    conn.execute("UPDATE drivers SET status = 'assigned' WHERE id = ?", (driver_id,))

    donor_row = conn.execute(
        "SELECT donors.name AS donor_name FROM donations JOIN donors ON donors.id = donations.donor_id "
        "WHERE donations.id = ?",
        (donation_id,),
    ).fetchone()
    fb_row = conn.execute("SELECT name FROM food_banks WHERE id = ?", (food_bank_id,)).fetchone()
    driver_row = conn.execute("SELECT name FROM drivers WHERE id = ?", (driver_id,)).fetchone()
    conn.commit()
    conn.close()

    donor_name = donor_row["donor_name"] if donor_row else donation_id
    fb_name = fb_row["name"] if fb_row else food_bank_id
    driver_name = driver_row["name"] if driver_row else driver_id

    log_activity("agent", "matched", f"{reasoning} Pickup {pickup_time}.")
    log_activity(f"donor:{donation_id}", "notified", f"Pickup confirmed by {driver_name} at {pickup_time} for {fb_name}.")
    log_activity(f"food_bank:{food_bank_id}", "notified", f"Incoming delivery from {donor_name} via {driver_name} at {pickup_time}.")
    log_activity(f"driver:{driver_id}", "notified", f"Assigned pickup: {donor_name} -> {fb_name} at {pickup_time}.")

    return f"Match {match_id} confirmed: {donor_name} -> {fb_name} via {driver_name} at {pickup_time}."


@tool
def escalate_donation(donation_id: str, reason: str) -> str:
    """Escalate a donation to the human coordinator because no viable food bank/driver
    match could be found (e.g. everyone in range is over capacity or unavailable before
    the food expires). Use this only after checking real alternatives.

    Args:
        donation_id: The donation that could not be matched.
        reason: Why no match was found.
    """
    conn = get_conn()
    conn.execute("UPDATE donations SET status = 'escalated' WHERE id = ?", (donation_id,))
    conn.commit()
    conn.close()
    log_activity("agent", "escalated", f"Donation {donation_id}: {reason}")
    return f"Donation {donation_id} escalated to human coordinator: {reason}"


ALL_TOOLS = [list_food_bank_needs, list_available_drivers, create_match, escalate_donation]
