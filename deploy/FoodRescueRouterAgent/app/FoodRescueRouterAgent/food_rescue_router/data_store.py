"""SQLite-backed state for donors, food banks, drivers, donations, matches, and the
activity log -- identical schema to the local dashboard app's data_store.py. This copy
is deployed standalone into the AgentCore Runtime container, so it uses a container-local
path (/tmp is guaranteed writable in AgentCore Runtime) rather than a path relative to the
main project, and its state is separate from the local dashboard's rescue.db.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("RESCUE_DB_PATH", "/tmp/rescue.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS donors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    zone TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS food_banks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    zone TEXT NOT NULL,
    hours TEXT NOT NULL,
    needs TEXT NOT NULL,          -- JSON: {category: "low"|"medium"|"high"}
    capacity_lbs INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS drivers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    zone TEXT NOT NULL,
    vehicle_capacity_lbs INTEGER NOT NULL,
    available_from TEXT NOT NULL,
    available_to TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available'   -- available | assigned
);

CREATE TABLE IF NOT EXISTS donations (
    id TEXT PRIMARY KEY,
    donor_id TEXT NOT NULL,
    category TEXT NOT NULL,
    quantity_lbs INTEGER NOT NULL,
    pickup_window_start TEXT NOT NULL,
    pickup_window_end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | matched | escalated
    resolution_detail TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    donation_id TEXT NOT NULL,
    food_bank_id TEXT NOT NULL,
    driver_id TEXT NOT NULL,
    pickup_time TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def log_activity(actor: str, action: str, detail: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO activity_log (ts, actor, action, detail) VALUES (?, ?, ?, ?)",
        (time.time(), actor, action, detail),
    )
    conn.commit()
    conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def dump_needs(needs: dict) -> str:
    return json.dumps(needs)


def load_needs(needs_json: str) -> dict:
    return json.loads(needs_json)
