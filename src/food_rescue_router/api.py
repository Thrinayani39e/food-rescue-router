"""FastAPI backend: intake endpoint that triggers the coordinator agent, a state
endpoint the dashboard fetches on load, and an SSE stream that pushes live updates
(activity, matches, escalations, resets) the instant the agent acts, instead of
making the dashboard poll for them.
"""
import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import route_donation
from .data_store import event_bus, get_conn, load_needs, log_activity, row_to_dict
from .seed_data import reset_and_seed, seed_if_empty

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

# How often the live public deployment quietly resets itself. There's no reset button
# in the UI (dropped on purpose -- a manual control doesn't help a demo that's supposed
# to run unattended), so without this the shared SQLite state would only ever accumulate:
# food banks would drain to zero capacity and every driver would end up "assigned"
# forever the first time enough visitors clicked around it. Auto-reset is what keeps a
# public, no-login demo link clean for whoever tries it next. Set AUTO_RESET_MINUTES=0
# to disable it entirely (e.g. for a long local recording session).
AUTO_RESET_MINUTES = int(os.environ.get("AUTO_RESET_MINUTES", "20"))

app = FastAPI(title="Food-Rescue Router")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


async def _auto_reset_loop() -> None:
    while AUTO_RESET_MINUTES > 0:
        await asyncio.sleep(AUTO_RESET_MINUTES * 60)
        reset_and_seed()
        event_bus.publish({"type": "auto_reset"})


@app.on_event("startup")
async def startup() -> None:
    seed_if_empty()
    event_bus.bind_loop(asyncio.get_running_loop())
    asyncio.create_task(_auto_reset_loop())


@app.get("/events")
async def events():
    """SSE stream: one line per activity/match/escalation/reset, as it happens."""
    async def gen():
        queue = event_bus.subscribe()
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


class DonationIn(BaseModel):
    donor_id: str
    category: str
    quantity_lbs: int
    pickup_window_start: str
    pickup_window_end: str


@app.post("/donations")
def create_donation(body: DonationIn):
    conn = get_conn()
    donor = conn.execute("SELECT * FROM donors WHERE id = ?", (body.donor_id,)).fetchone()
    if donor is None:
        conn.close()
        return {"error": f"unknown donor_id {body.donor_id}"}

    donation_id = str(uuid.uuid4())[:8]
    conn.execute(
        "INSERT INTO donations (id, donor_id, category, quantity_lbs, pickup_window_start, "
        "pickup_window_end, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (donation_id, body.donor_id, body.category, body.quantity_lbs,
         body.pickup_window_start, body.pickup_window_end, time.time()),
    )
    conn.commit()
    conn.close()

    log_activity(
        f"donor:{body.donor_id}", "offered",
        f"{donor['name']} offered {body.quantity_lbs} lbs of {body.category}.",
    )

    result = route_donation({
        "id": donation_id,
        "donor_id": body.donor_id,
        "donor_name": donor["name"],
        "zone": donor["zone"],
        "category": body.category,
        "quantity_lbs": body.quantity_lbs,
        "pickup_window_start": body.pickup_window_start,
        "pickup_window_end": body.pickup_window_end,
    })

    return {"donation_id": donation_id, "agent_result": result}


@app.get("/state")
def get_state():
    conn = get_conn()
    donors = [row_to_dict(r) for r in conn.execute("SELECT * FROM donors").fetchall()]

    food_banks = []
    for r in conn.execute("SELECT * FROM food_banks").fetchall():
        d = row_to_dict(r)
        d["needs"] = load_needs(d["needs"])
        food_banks.append(d)

    drivers = [row_to_dict(r) for r in conn.execute("SELECT * FROM drivers").fetchall()]
    donations = [row_to_dict(r) for r in conn.execute(
        "SELECT donations.*, donors.name AS donor_name FROM donations "
        "JOIN donors ON donors.id = donations.donor_id ORDER BY donations.created_at DESC"
    ).fetchall()]
    matches = [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM matches ORDER BY created_at DESC"
    ).fetchall()]
    activity = [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM activity_log ORDER BY ts DESC LIMIT 50"
    ).fetchall()]
    conn.close()

    return {
        "donors": donors,
        "food_banks": food_banks,
        "drivers": drivers,
        "donations": donations,
        "matches": matches,
        "activity": activity,
    }


@app.post("/reset")
def reset_demo():
    """Wipe all donations/matches/activity and reseed fresh donors/food banks/drivers,
    so a demo can be re-run cleanly without restarting the server.
    """
    reset_and_seed()
    return {"status": "reset"}


@app.get("/config")
def get_config():
    """Public, client-safe config -- the Supabase anon key is designed to be exposed
    to the browser (auth and row-level security happen on Supabase's side, not by
    keeping this secret). Sign-in is optional and additive: the dashboard and the
    coordinator work exactly the same with no Supabase project configured, so this
    just returns empty strings until SUPABASE_URL/SUPABASE_ANON_KEY are set, and the
    frontend hides the sign-in UI entirely when they're empty.
    """
    return {
        "supabase_url": os.environ.get("SUPABASE_URL", ""),
        "supabase_anon_key": os.environ.get("SUPABASE_ANON_KEY", ""),
    }


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
