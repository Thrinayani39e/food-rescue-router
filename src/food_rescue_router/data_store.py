"""SQLite-backed state for donors, food banks, drivers, donations, matches, and the activity log."""
import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent.parent / "rescue.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS donors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    zone TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS food_banks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    zone TEXT NOT NULL,
    hours TEXT NOT NULL,
    needs TEXT NOT NULL,          -- JSON: {category: "low"|"medium"|"high"}
    capacity_lbs INTEGER NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS drivers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    zone TEXT NOT NULL,
    vehicle_capacity_lbs INTEGER NOT NULL,
    available_from TEXT NOT NULL,
    available_to TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available',   -- available | assigned
    lat REAL NOT NULL,
    lon REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS donations (
    id TEXT PRIMARY KEY,
    donor_id TEXT NOT NULL,
    category TEXT NOT NULL,
    quantity_lbs INTEGER NOT NULL,
    pickup_window_start TEXT NOT NULL,
    pickup_window_end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | matched | escalated
    resolution_detail TEXT NOT NULL DEFAULT '',  -- e.g. "-> Downtown Community Pantry via Sam R." or an escalation reason
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
    actor TEXT NOT NULL,          -- e.g. "agent", "donor:d1"
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class EventBus:
    """Thread-safe pub/sub so agent tool calls (running in a worker thread) can push
    live updates to SSE clients (served on the main asyncio event loop). A tool call
    happens inside FastAPI's threadpool, not on the loop, so publishing has to hop
    threads via call_soon_threadsafe rather than calling asyncio APIs directly.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        if self._loop is None:
            return
        for q in list(self._subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, event)


event_bus = EventBus()


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
    ts = time.time()
    conn = get_conn()
    conn.execute(
        "INSERT INTO activity_log (ts, actor, action, detail) VALUES (?, ?, ?, ?)",
        (ts, actor, action, detail),
    )
    conn.commit()
    conn.close()
    event_bus.publish({"type": "activity", "ts": ts, "actor": actor, "action": action, "detail": detail})


def row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def dump_needs(needs: dict) -> str:
    return json.dumps(needs)


def load_needs(needs_json: str) -> dict:
    return json.loads(needs_json)
