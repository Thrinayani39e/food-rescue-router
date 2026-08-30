"""Synthetic but realistic donors, food banks, and volunteer drivers used for the demo.

No real organizations are represented -- names and details are fictional stand-ins
for a city-scale food-rescue network, built because this submission has no direct
partner org to source live data from.
"""
from .data_store import dump_needs, get_conn, init_db

DONORS = [
    ("d1", "Green Aisle Market", "grocer", "Downtown"),
    ("d2", "Corner Bakery Co.", "restaurant", "Eastside"),
    ("d3", "Union Produce Wholesale", "grocer", "Northgate"),
    ("d4", "Riverside Diner", "restaurant", "Downtown"),
]

FOOD_BANKS = [
    ("fb1", "Downtown Community Pantry", "Downtown", "Mon-Sat 9am-5pm",
     {"produce": "high", "bakery": "medium", "dairy": "high", "prepared": "low"}, 400),
    ("fb2", "Eastside Family Shelf", "Eastside", "Tue-Sun 10am-6pm",
     {"produce": "medium", "bakery": "high", "dairy": "low", "prepared": "high"}, 250),
    ("fb3", "Northgate Neighbors Pantry", "Northgate", "Mon-Fri 8am-4pm",
     {"produce": "high", "bakery": "low", "dairy": "medium", "prepared": "medium"}, 600),
]

DRIVERS = [
    ("v1", "Sam R.", "Downtown", 150, "08:00", "18:00"),
    ("v2", "Priya K.", "Eastside", 100, "07:00", "15:00"),
    ("v3", "Marcus T.", "Northgate", 300, "10:00", "20:00"),
    ("v4", "Alicia G.", "Downtown", 80, "12:00", "22:00"),
]


def seed_if_empty() -> None:
    init_db()
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) AS n FROM donors").fetchone()["n"]
    if count:
        conn.close()
        return

    conn.executemany(
        "INSERT INTO donors (id, name, kind, zone) VALUES (?, ?, ?, ?)", DONORS
    )
    conn.executemany(
        "INSERT INTO food_banks (id, name, zone, hours, needs, capacity_lbs) VALUES (?, ?, ?, ?, ?, ?)",
        [(id_, name, zone, hours, dump_needs(needs), cap) for id_, name, zone, hours, needs, cap in FOOD_BANKS],
    )
    conn.executemany(
        "INSERT INTO drivers (id, name, zone, vehicle_capacity_lbs, available_from, available_to) VALUES (?, ?, ?, ?, ?, ?)",
        DRIVERS,
    )
    conn.commit()
    conn.close()
