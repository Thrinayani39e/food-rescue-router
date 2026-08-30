"""FastAPI backend: intake endpoint that triggers the agent, and a state endpoint
the dashboard polls to show donors, food banks, drivers, matches, and the live
activity feed updating as the agent acts.
"""
import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import route_donation
from .data_store import get_conn, load_needs, log_activity, row_to_dict
from .seed_data import seed_if_empty

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

app = FastAPI(title="Food-Rescue Router")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.on_event("startup")
def startup() -> None:
    seed_if_empty()


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
        "SELECT * FROM donations ORDER BY created_at DESC"
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


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
