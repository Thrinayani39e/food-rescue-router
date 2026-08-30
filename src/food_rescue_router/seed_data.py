"""Synthetic but realistic donors, food banks, and volunteer drivers used for the demo.

No real organizations are represented -- names and details are fictional stand-ins
for a city-scale food-rescue network, built because this submission has no direct
partner org to source live data from. Coordinates are real Austin, TX-area points
(Downtown / East Austin / North Austin) so the live map renders against real
streets -- the map is honest about being a demo dataset, not a real partnership.
"""
from .data_store import dump_needs, event_bus, get_conn, init_db

DONORS = [
    ("d1", "Green Aisle Market", "grocer", "Downtown", 30.2700, -97.7450),
    ("d2", "Corner Bakery Co.", "restaurant", "Eastside", 30.2650, -97.7050),
    ("d3", "Union Produce Wholesale", "grocer", "Northgate", 30.3250, -97.7400),
    ("d4", "Riverside Diner", "restaurant", "Downtown", 30.2630, -97.7400),
]

FOOD_BANKS = [
    ("fb1", "Downtown Community Pantry", "Downtown", "Mon-Sat 9am-5pm",
     {"produce": "high", "bakery": "medium", "dairy": "high", "prepared": "low"}, 400, 30.2680, -97.7400),
    ("fb2", "Eastside Family Shelf", "Eastside", "Tue-Sun 10am-6pm",
     {"produce": "medium", "bakery": "high", "dairy": "low", "prepared": "high"}, 250, 30.2690, -97.7000),
    ("fb3", "Northgate Neighbors Pantry", "Northgate", "Mon-Fri 8am-4pm",
     {"produce": "high", "bakery": "low", "dairy": "medium", "prepared": "medium"}, 600, 30.3220, -97.7450),
]

DRIVERS = [
    ("v1", "Sam R.", "Downtown", 150, "08:00", "18:00", 30.2660, -97.7420),
    ("v2", "Priya K.", "Eastside", 100, "07:00", "15:00", 30.2670, -97.7020),
    ("v3", "Marcus T.", "Northgate", 300, "10:00", "20:00", 30.3180, -97.7420),
    ("v4", "Alicia G.", "Downtown", 80, "12:00", "22:00", 30.2710, -97.7410),
]


def _insert_seed_rows(conn) -> None:
    conn.executemany(
        "INSERT INTO donors (id, name, kind, zone, lat, lon) VALUES (?, ?, ?, ?, ?, ?)", DONORS
    )
    conn.executemany(
        "INSERT INTO food_banks (id, name, zone, hours, needs, capacity_lbs, lat, lon) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (id_, name, zone, hours, dump_needs(needs), cap, lat, lon)
            for id_, name, zone, hours, needs, cap, lat, lon in FOOD_BANKS
        ],
    )
    conn.executemany(
        "INSERT INTO drivers (id, name, zone, vehicle_capacity_lbs, available_from, available_to, lat, lon) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        DRIVERS,
    )


def seed_if_empty() -> None:
    init_db()
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) AS n FROM donors").fetchone()["n"]
    if count:
        conn.close()
        return
    _insert_seed_rows(conn)
    conn.commit()
    conn.close()


def reset_and_seed() -> None:
    """Wipe all state and reseed from scratch -- lets a demo be re-run from a clean slate
    without restarting the server, so a bad take doesn't require a full restart.
    """
    init_db()
    conn = get_conn()
    for table in ("matches", "donations", "drivers", "food_banks", "donors", "activity_log"):
        conn.execute(f"DELETE FROM {table}")
    _insert_seed_rows(conn)
    conn.commit()
    conn.close()
    event_bus.publish({"type": "reset"})
