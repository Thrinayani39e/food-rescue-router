"""Tests for the deterministic parts (data store, seed data, tools) that don't require
a live Bedrock model call. The agent's reasoning loop (agent.py) is exercised manually /
in the demo, not unit tested here, since it depends on a real LLM call.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import food_rescue_router.data_store as data_store  # noqa: E402


def _use_temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(data_store, "DB_PATH", tmp_path / "test.db")


def test_seed_and_query(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    from food_rescue_router.seed_data import seed_if_empty
    seed_if_empty()

    conn = data_store.get_conn()
    donors = conn.execute("SELECT * FROM donors").fetchall()
    food_banks = conn.execute("SELECT * FROM food_banks").fetchall()
    drivers = conn.execute("SELECT * FROM drivers").fetchall()
    conn.close()

    assert len(donors) == 4
    assert len(food_banks) == 3
    assert len(drivers) == 4


def test_list_food_bank_needs_sorts_by_real_distance(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    from food_rescue_router.seed_data import seed_if_empty
    from food_rescue_router.tools import list_food_bank_needs
    seed_if_empty()

    ranked = list_food_bank_needs("d1")  # Green Aisle Market, Downtown
    assert len(ranked) == 3
    assert all("distance_miles" in fb for fb in ranked)
    # sorted ascending by real distance, not by zone label
    distances = [fb["distance_miles"] for fb in ranked]
    assert distances == sorted(distances)
    # the donor's own (Downtown) food bank should be closest
    assert ranked[0]["zone"] == "Downtown"


def test_list_available_drivers_sorts_by_real_distance(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    from food_rescue_router.seed_data import seed_if_empty
    from food_rescue_router.tools import list_available_drivers
    seed_if_empty()

    ranked = list_available_drivers("d1")
    assert len(ranked) == 4
    distances = [d["distance_miles"] for d in ranked]
    assert distances == sorted(distances)


def test_food_safety_window_flags_perishable_overrun(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    from food_rescue_router.tools import check_food_safety_window

    safe = check_food_safety_window("produce", "Today 1:00pm", "Today 5:00pm")
    assert safe.startswith("SAFE")

    unsafe = check_food_safety_window("dairy", "Today 9:00am", "Today 5:00pm")  # 8h > 6h dairy limit
    assert unsafe.startswith("UNSAFE")


def test_create_match_updates_state_and_logs(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    from food_rescue_router.seed_data import seed_if_empty
    from food_rescue_router.tools import create_match
    seed_if_empty()

    conn = data_store.get_conn()
    conn.execute(
        "INSERT INTO donations (id, donor_id, category, quantity_lbs, pickup_window_start, "
        "pickup_window_end, status, created_at) VALUES ('don1','d1','produce',50,'1pm','5pm','pending',0)"
    )
    conn.commit()
    conn.close()

    result = create_match("don1", "fb1", "v1", "Today 2pm", "Same zone, high need for produce.")
    assert "Match" in result

    conn = data_store.get_conn()
    donation = conn.execute("SELECT status FROM donations WHERE id='don1'").fetchone()
    driver = conn.execute("SELECT status FROM drivers WHERE id='v1'").fetchone()
    activity_count = conn.execute("SELECT COUNT(*) AS n FROM activity_log").fetchone()["n"]
    conn.close()

    assert donation["status"] == "matched"
    assert driver["status"] == "assigned"
    assert activity_count >= 4  # matched + 3 notifications


def test_create_match_depletes_food_bank_capacity(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    from food_rescue_router.seed_data import seed_if_empty
    from food_rescue_router.tools import create_match
    seed_if_empty()

    conn = data_store.get_conn()
    before = conn.execute("SELECT capacity_lbs FROM food_banks WHERE id='fb1'").fetchone()["capacity_lbs"]
    conn.execute(
        "INSERT INTO donations (id, donor_id, category, quantity_lbs, pickup_window_start, "
        "pickup_window_end, status, created_at) VALUES ('don2','d1','produce',50,'1pm','5pm','pending',0)"
    )
    conn.commit()
    conn.close()

    create_match("don2", "fb1", "v1", "Today 2pm", "test")

    conn = data_store.get_conn()
    after = conn.execute("SELECT capacity_lbs FROM food_banks WHERE id='fb1'").fetchone()["capacity_lbs"]
    donation = conn.execute("SELECT resolution_detail FROM donations WHERE id='don2'").fetchone()
    conn.close()

    assert after == before - 50
    assert "fb1" not in donation["resolution_detail"]  # human-readable name, not raw id
    assert "Downtown Community Pantry" in donation["resolution_detail"]


def test_reset_and_seed_clears_and_reseeds(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    from food_rescue_router.seed_data import reset_and_seed, seed_if_empty
    from food_rescue_router.tools import create_match
    seed_if_empty()

    conn = data_store.get_conn()
    conn.execute(
        "INSERT INTO donations (id, donor_id, category, quantity_lbs, pickup_window_start, "
        "pickup_window_end, status, created_at) VALUES ('don3','d1','produce',50,'1pm','5pm','pending',0)"
    )
    conn.commit()
    conn.close()
    create_match("don3", "fb1", "v1", "Today 2pm", "test")

    reset_and_seed()

    conn = data_store.get_conn()
    donations = conn.execute("SELECT COUNT(*) AS n FROM donations").fetchone()["n"]
    matches = conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]
    fb1_capacity = conn.execute("SELECT capacity_lbs FROM food_banks WHERE id='fb1'").fetchone()["capacity_lbs"]
    driver = conn.execute("SELECT status FROM drivers WHERE id='v1'").fetchone()
    conn.close()

    assert donations == 0
    assert matches == 0
    assert fb1_capacity == 400  # back to seed value
    assert driver["status"] == "available"
