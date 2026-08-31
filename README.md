# Windfall — Food-Rescue Router

An autonomous **multi-agent** system, built on the **Strands Agents SDK** and Amazon
Bedrock, that routes surplus-food donations (from grocers and restaurants) to the food
bank that needs them and a volunteer driver who can carry them there — end to end, with
no human approval step, and only escalating to a human coordinator when no real match
exists. A coordinator agent delegates to two specialist agents (Matching and
Logistics), each independently reasoning over live data, and the result streams live
to a dashboard with a real-time map of the network.

**Windfall** is the product brand — the dashboard, the "Log a windfall" flow, the
network map. "Food-Rescue Router" is the underlying repo/system name. Visual design
and copy for the dashboard were designed with Claude Design and integrated here
against the real backend (no placeholder data ships in the running app — every number
on screen is computed from live state).

Built for the **Agents for Humans Hackathon** (Good Neighbor track).

**Live demo**: [http://32.196.16.210](http://32.196.16.210) — the actual dashboard,
deployed on an AWS EC2 instance, running the real multi-agent Coordinator against live
Amazon Bedrock calls. Not a mockup — submit a donation and watch it route for real.

## Why this exists

Food-rescue coordination today runs on phone calls and spreadsheets, so good matches
get missed or made too late. This agent is given the same information a human
coordinator would gather — which food banks currently need what, and which drivers
are free and able to carry it — and acts on it directly: it commits to a match (and
notifies the donor, food bank, and driver) or explicitly escalates, every time.

This is a **Good Neighbor** agent, not a personal-productivity tool: every successful
run benefits three separate parties, plus the community the food bank serves.

> **Data note:** the donors, food banks, and drivers in this demo are synthetic —
> fictional stand-ins for a city-scale network, not a real partner organization's
> data. See [docs/architecture.md](docs/architecture.md).

## The problem, in numbers

This isn't a supply problem — it's a coordination-speed problem:

- **29% of the US food supply went unsold or uneaten in 2024** — about 114 billion
  meals' worth — much of it still perfectly edible when it's thrown out
  ([ReFED, 2026 US Food Waste Report](https://refed.org/food-waste/the-problem/)).
- **47.9 million Americans lived in food-insecure households in 2024**, including
  14.1 million children — 1 in 7 households nationally
  ([USDA Economic Research Service, Household Food Security in the United States
  2024](https://www.ers.usda.gov/topics/food-nutrition-assistance/food-security-in-the-us/key-statistics-graphics);
  [Feeding America](https://www.feedingamerica.org/research)).
- Surplus food is usually only donatable within a narrow safe-handling window before
  it has to be discarded (see `check_food_safety_window` in
  [tools.py](src/food_rescue_router/tools.py)) — which is exactly why this has to be
  a fast, autonomous match, not a phone-tag process that takes hours to find a food
  bank with space and a driver who's free.

Windfall doesn't create more surplus food or more donated food — it closes the gap
between a donation being offered and it actually reaching someone, before the safety
window closes.

## How it works

1. A donor submits a surplus-food offer (category, quantity, pickup window) through
   the dashboard.
2. A **coordinator agent** consults a **Matching Specialist** (its own Strands `Agent`,
   wrapped as a tool) to pick the best food bank, using live need/capacity data.
3. The coordinator consults a **Logistics Specialist** (same pattern) to pick the best
   driver, using live availability/capacity data for the chosen zone and time window.
4. It reasons about the combined result and either:
   - calls `create_match`, which assigns the donation, marks the driver busy, and
     notifies all three parties, or
   - calls `escalate_donation` with a specific reason, if either specialist found
     nothing viable.
5. The dashboard updates **live** (Server-Sent Events, not polling) — the
   donor/food-bank/driver panels, the activity feed, and an animated route on the map
   all update the instant the agent acts.

## Architecture

```mermaid
flowchart LR
    Donor([Donor submits offer\ncategory, qty, zone, window]) --> API["FastAPI\nPOST /donations"]
    API --> Coord["Coordinator Agent"]

    Coord -->|consult| Match["Matching Specialist\n(own Agent + own tool)"]
    Match -->|list_food_bank_needs| State[(SQLite state)]

    Coord -->|consult| Log["Logistics Specialist\n(own Agent + own tool)"]
    Log -->|list_available_drivers| State

    Coord -->|create_match| Confirmed[["Match confirmed"]]
    Coord -->|escalate_donation| Escalate[["Escalated to coordinator"]]

    Confirmed --> N1([Donor notified])
    Confirmed --> N2([Food bank notified])
    Confirmed --> N3([Driver notified])

    State --> Bus[["SSE event bus"]]
    Bus --> Dash["Live dashboard + map"]
```

Full design, deployment topology, and the standalone AgentCore diagram are in
[docs/architecture.md](docs/architecture.md).

## Features

- **Multi-agent delegation, not one agent with two lookup tools.** The coordinator
  never queries food bank or driver data itself — it consults two separate Strands
  `Agent`s (Matching Specialist, Logistics Specialist), each wrapped as a `@tool`,
  each independently reasoning over live data before answering.
- **Autonomous routing, not a chat interface.** The coordinator's system prompt
  requires consulting both specialists and then always ending by calling
  `create_match` or `escalate_donation` — it cannot just describe what it would do,
  it has to act.
- **Real constraint reasoning.** The Matching Specialist checks need level *and*
  remaining capacity; the Logistics Specialist checks zone *and* vehicle capacity
  *and* availability window against the actual pickup window. The system correctly
  escalates (rather than force a bad match) when, for example, no driver's vehicle
  capacity covers the donation weight.
- **Three-party notification on match.** A confirmed match writes distinct,
  addressed notifications for the donor, the food bank, and the driver — the
  dashboard shows all three updating live, making the multi-party benefit
  (the point of a Good Neighbor agent) visible at a glance.
- **Real-time, not polling.** A server-side SSE stream pushes every activity, match,
  and escalation to the dashboard the instant it happens — including an animated
  route on a live map (real Austin, TX coordinates) showing the donation traveling
  from donor to food bank.
- **Deployed twice, same logic.** The identical coordinator + specialists run both
  in-process in the local dashboard app and standalone on AWS Bedrock AgentCore
  Runtime — see [AgentCore deployment](#agentcore-deployment) below.

## Run it locally

Requires Python 3.12+ and AWS credentials with Amazon Bedrock model access (Claude
Sonnet) in your target region.

```bash
python -m venv .venv
./.venv/Scripts/activate        # or: source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

python run.py                   # serves the dashboard + API on http://127.0.0.1:8787
```

Open http://127.0.0.1:8787, submit a donation offer, and watch the agent route it.

Environment variables (optional):
- `BEDROCK_MODEL_ID` — defaults to `us.anthropic.claude-sonnet-4-6`
- `AWS_REGION` — defaults to `us-east-1`

## Tests

```bash
python -m pytest tests/
```

Unit tests cover the deterministic parts (seed data, tools, data store). The agent's
reasoning loop is exercised live via the API, since it depends on a real model call.

## Project layout

See [docs/architecture.md](docs/architecture.md) for the full design.

```
src/food_rescue_router/
  agent.py        # Coordinator Agent definition, system prompt, routing entrypoint
  tools.py        # Specialist agents (agents-as-tools) + the two terminal actions
  model.py        # Shared Bedrock model config
  data_store.py   # SQLite access + the SSE event bus
  seed_data.py    # synthetic donors / food banks / drivers (with real coordinates)
  api.py          # FastAPI app (POST /donations, GET /state, GET /events)
frontend/
  index.html         # Windfall dashboard: hero stats, map, Coordinator panel,
                      # ledger, activity feed, network directories
  windfall-map.js     # <windfall-map> custom element -- real Leaflet map,
                      # data-driven from GET /state, animates routes on match
  assets/             # Windfall logo mark + lockup (SVG)
```

## AgentCore deployment

`deploy/FoodRescueRouterAgent/` is a standalone AWS Bedrock AgentCore Runtime
deployment of the same agent, tools, and system prompt — **live and deployed**
(`arn:aws:bedrock-agentcore:us-east-1:141353495650:runtime/FoodRescueRouterAgent_FoodRescueRouterAgent-FDKqFqAfP3`).
Verified end to end with a real `agentcore invoke` call. See
[docs/deploy-agentcore.md](docs/deploy-agentcore.md) for how to run it locally or
redeploy it.

## Dashboard deployment

The full dashboard (`Dockerfile` at the repo root) is built as a container image,
pushed to Amazon ECR, and run on an EC2 instance (`t3.small`, IAM instance role
scoped to `bedrock:InvokeModel`/`Converse` and ECR pull only) with a security group
open on port 80. See the live link above. To redeploy: `docker build`, `docker push`
to the ECR repo, then `docker pull` + `docker run` on the instance (or replace the
instance with a fresh one running the same user-data).

## Status

Core agent + API + dashboard working end to end against live Bedrock, deployed
publicly. Routing agent also deployed standalone to AWS Bedrock AgentCore Runtime
and verified live. Demo video in progress — see the hackathon build plan.

## License

MIT — see [LICENSE](LICENSE).
