# TaskRouter setup — agent queueing

Wires Mike's warm transfer to a Twilio TaskRouter queue that routes the live call
to the next Available human agent based on state license and priority.

End-to-end flow once provisioned:

```
Mike decides to transfer
   → POST /api/transfer/prepare        (stashes routing intent)
   → SIP REFER to <queue number>       (LiveKit asks Twilio to bridge there)
Twilio voice webhook on queue number
   → POST /api/transfer/voice          (returns <Enqueue> TwiML w/ task attrs)
TaskRouter picks a matching Available worker
   → POST /api/transfer/assignment     (we return `dequeue` to worker's cell)
Worker's cell rings, they pick up, call bridges
Call ends → worker auto-set to Wrap-Up → auto-flipped to Available after 30s
```

---

## One-time setup

### 1. Twilio prerequisites

You need:
- A Twilio account with TaskRouter enabled (it's on by default for new accounts).
- A Twilio phone number that will host the queue. **It does not have to be the
  same number Mike dials from.** It can be the same; up to you.
- Your Twilio `ACCOUNT_SID` and `AUTH_TOKEN` in `.env.local`
  (already scaffolded under `# ============ Twilio (for trunk setup ...`).

### 2. Make the dashboard publicly reachable

TaskRouter has to POST to your endpoints. In dev, expose `dashboard.py` via
ngrok:

```bash
# In one terminal:
uvicorn dashboard:app --host 0.0.0.0 --port 8080

# In another:
ngrok http 8080
```

Copy the `https://<id>.ngrok-free.app` URL into `.env.local`:

```
PUBLIC_BASE_URL=https://<id>.ngrok-free.app
```

In prod, use your actual public URL.

### 3. Provision TaskRouter resources

```bash
python taskrouter_setup.py init
```

This idempotently creates the Workspace, Activities (Available/Busy/Wrap-Up/
Lunch/Offline), TaskQueues (default + manager_escalation), and Workflow with
the phase-1 routing rules. It then prints a block of env vars; paste them into
`.env.local`.

Verify with:

```bash
python taskrouter_setup.py print-config
```

### 4. Configure the queue phone number

In the Twilio console, go to **Phone Numbers → Active Numbers → \[your queue
number\] → Voice Configuration**, and set:

| Field | Value |
|---|---|
| A call comes in | Webhook |
| URL | `{PUBLIC_BASE_URL}/api/transfer/voice` |
| HTTP method | POST |

Save. Put the number into `.env.local`:

```
TASKROUTER_QUEUE_NUMBER=+12025550000
```

### 5. Add agents

From the CLI (also creates the TaskRouter Worker):

```bash
python taskrouter_setup.py add-agent \
    --name "Maria Lopez" \
    --email maria@agency.com \
    --cell +12025551234 \
    --states TX,FL,GA \
    --languages en,es
```

For a manager (receives compliance escalations):

```bash
python taskrouter_setup.py add-agent \
    --name "Chris" --cell +12025550111 \
    --states TX,FL,GA,NC,OH --manager
```

Re-sync workers anytime after editing agents in the DB directly:

```bash
python taskrouter_setup.py sync-workers
```

---

## How agents go online

Each agent opens `http://<your-dashboard>/agent/<their-id>` in any browser on
any device. They tap **Available** when they start work, **Lunch** for breaks,
**Offline** when done.

The page heartbeats every 30s. If they close the tab or lose connection for
more than 90s, the reaper auto-flips them to Offline so calls don't ring a
closed laptop.

Calls arrive at the agent's **cell phone** (the number registered with
`--cell` above). The browser page is just the presence toggle.

Admins see everyone at `/agents`.

---

## What goes where

| File | Purpose |
|---|---|
| `agent_db.py` | SQLite tables `agents`, `agent_activity`, `routing_log` |
| `taskrouter_setup.py` | Provision Workspace/Workflow/Queues + sync Workers |
| `transfer_queue.py` | FastAPI routes Twilio hits during a transfer |
| `agent_state.py` | Agent presence API + wrap-up timer + heartbeat reaper |
| `dashboard.py` | Mounts the routers + `/agent/{id}` and `/agents` pages |
| `agent.py` | `transfer_call()` now calls `/api/transfer/prepare` first |

---

## Verifying the wiring (no real call)

```bash
# 1. Provision + add a test agent.
python taskrouter_setup.py init
python taskrouter_setup.py add-agent --name "Test" --cell +15551234567 --states TX

# 2. Boot the dashboard.
uvicorn dashboard:app --host 0.0.0.0 --port 8080 --reload

# 3. Open the agent page, click Available.
#    http://localhost:8080/agent/1

# 4. Simulate Mike's prepare call:
curl -X POST http://localhost:8080/api/transfer/prepare \
     -H 'content-type: application/json' \
     -d '{"call_id":"test_001","lead_phone":"+15555550100","first_name":"Pat","required_state":"TX","temperature":"warm"}'

# 5. Check the routing_log got the intent:
sqlite3 calls.db "SELECT * FROM routing_log ORDER BY id DESC LIMIT 1;"

# 6. Use Twilio CLI or console to send a test call to your queue number.
#    The voice webhook will fire and TaskRouter will try to dial the test
#    agent's cell. (Or use the TaskRouter "Test Task" feature in the
#    Twilio console to create a task directly.)
```

---

## Priority lanes (Phase 2)

Every transfer carries a `temperature` set by Mike at the moment he calls his
`transfer_call` tool. The temperature drives both the queue priority and which
filter the workflow uses to find an agent.

| temperature  | priority | who picks it up                                            | when Mike uses it                                                                |
|--------------|---------:|------------------------------------------------------------|----------------------------------------------------------------------------------|
| `compliance` |    12    | **manager queue only** (`is_manager == true`)              | TCPA/DNC concern, "let me speak to your supervisor", lawyer threats              |
| `hot`        |    10    | state-matched agent first (45s); falls back to any agent   | prospect ready to enroll, asked for pricing, said "I want this now"              |
| `callback`   |     8    | the previously-assigned agent first (30s); then state match | scheduled callback (Phase 3 dispatcher sets this + `prefer_agent_sid`)            |
| `warm`       |     5    | state-matched agent                                        | default — the qualified-lead path after STEP 6                                   |

The priority numbers and filter ordering live in `taskrouter_setup._workflow_config()`.
When you edit them, re-run `python taskrouter_setup.py init` to push the new
workflow JSON to Twilio. The init command is idempotent — it updates the existing
workflow rather than creating duplicates.

### How Mike picks the temperature

Mike reads the docstring on his `transfer_call` tool and chooses. The
docstring lives at [agent.py — `transfer_call`](agent.py) and explicitly
warns the LLM not to mark a friendly prospect as `hot` — only explicit
urgency / readiness-to-buy qualifies.

To tighten his judgment, edit the docstring (the LLM reads it as part of the
tool schema on every call) or add a brief instruction line in the STRICT CALL
FLOW section of his system prompt.

### Seeing it in action

The `/agents` admin page now has a **Recent routings** panel showing every
recent transfer with its temperature color-coded (red=hot, gold=compliance,
green=warm, blue=callback), the lead's state, the agent who took it, and the
prepare→accept latency. Refreshes every 3 seconds.

---

## Callback fallback + sticky routing (Phase 3)

Two new flows now share one storage table (`pending_callbacks` in `calls.db`):

### A. No-agent-in-90s fallback

When a warm transfer lands in the queue and no agent picks up within
`NO_AGENT_TIMEOUT_SECONDS` (default 90s):

1. `task.created` event arrives → we schedule an asyncio timer keyed by
   `task_sid`.
2. If `reservation.accepted` fires before the timer, the timer is canceled
   and the call bridges normally.
3. Otherwise the timer fires, calls Twilio with
   `assignment_status="canceled" reason="no_agent_available"`, and the queued
   caller is removed from the queue.
4. Twilio invokes the `<Enqueue action="...">` URL → `/api/transfer/queue_exit`
   responds with TwiML that says *"all our specialists are on calls — we'll
   call you back in a few minutes"* and hangs up.
5. The `task.canceled` event arrives → handler looks up the routing row and
   writes a `pending_callbacks` entry with `source='no_agent_timeout'`,
   carrying `lead_phone`, `first_name`, `required_state`, and the original
   `prefer_agent_sid` (if any) forward.

### B. Prospect-requested callback

Mike has a new tool, `schedule_callback(requested_time)`, that he calls
when the prospect says something like "now's not a good time, try
tomorrow". The tool POSTs to `/api/transfer/callbacks` with
`source='prospect_requested'`. After the tool call Mike confirms the
callback verbally and ends the call.

### C. Sticky routing

When the workflow sees `task.prefer_agent_sid != null`, the `sticky_callback`
filter tries that exact worker first (30s) before falling through to a
state-matched agent. To make a redial sticky:

```python
# In whatever dispatcher you write that picks up rows from /api/transfer/callbacks/claim:
# When you redial via the existing dialer.py pipeline, also POST the prepare
# call with prefer_agent_sid set to the WK... that originally took the call.
{
  "call_id": "...",
  "lead_phone": "+15555550100",
  "temperature": "callback",
  "prefer_agent_sid": "WKxxxxxxxxxx",  # from pending_callbacks.prefer_agent_sid
}
```

### Wiring a dispatcher

This codebase exposes the data; placing the actual outbound calls is up to
you. The minimal dispatcher loop:

```python
import time, httpx, asyncio
from dialer import dispatch_call  # existing LiveKit dispatch helper

async def callback_dispatcher_loop():
    async with httpx.AsyncClient() as http:
        while True:
            claimed = (await http.post(
                "http://localhost:8080/api/transfer/callbacks/claim",
                json={"limit": 5},
            )).json()
            for cb in claimed:
                contact = {
                    "phone_number": cb["lead_phone"],
                    "first_name": cb["first_name"] or "there",
                    "state": cb["required_state"],
                    "prefer_agent_sid": cb["prefer_agent_sid"],  # propagates to /prepare
                }
                try:
                    await dispatch_call(lk_client, contact)
                    await http.put(f"http://localhost:8080/api/transfer/callbacks/{cb['id']}/completed")
                except Exception as e:
                    await http.put(
                        f"http://localhost:8080/api/transfer/callbacks/{cb['id']}/gave_up",
                        json={"reason": str(e)},
                    )
            await asyncio.sleep(15)
```

Drop that as a small script (`callback_runner.py`) and run it alongside
`dashboard.py` and `agent.py`. It atomically claims callbacks via the
`claim` endpoint so two dispatchers wouldn't double-dial the same lead.

### Endpoints reference

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/transfer/callbacks` | GET | List by status (`pending`, `dispatched`, `completed`, `gave_up`, `all`) |
| `/api/transfer/callbacks` | POST | Add one (used by Mike's tool) |
| `/api/transfer/callbacks/claim` | POST | Atomically claim due-now rows for a batch dispatch |
| `/api/transfer/callbacks/{id}/completed` | PUT | Mark success |
| `/api/transfer/callbacks/{id}/gave_up` | PUT | Mark failure |

### Operational gotchas

- **Restarting `dashboard.py` cancels in-flight no-agent timers.** Any task
  that's queued at the moment of restart won't get the 90s cancel — it'll
  sit until manually canceled in the Twilio console. Acceptable for an MVP;
  fixable with a startup reconciliation pass over `tasks?assignment_status=pending`.
- **`PUBLIC_BASE_URL` must be set** for the `<Enqueue action="...">` URL to
  be reachable. Without it we don't add the action attribute and the caller
  hears no fallback message (the call just drops).
- **`prefer_agent_sid` should be a Worker SID (`WK...`), not your internal
  agent_id.** When a callback row is created from `task.canceled`, we
  inherit the routing row's `prefer_agent_sid` (Twilio Worker SID) — verify
  this is what your dispatcher passes back through `/api/transfer/prepare`.

---

## Remaining limitations (not on any roadmap yet)

- No browser softphone — agents take calls on their cell. (User-chosen.)
- No automatic retry ladder for failed callbacks (manual dispatch only).
- No state-derived priority bumps for queued tasks aging past 60s.

---

## Common gotchas

**"voice webhook fires but routing returns defaults"** — `/api/transfer/prepare`
wasn't reached, or the call arrived more than 120s after prepare. Check the
`prepared_at` timestamp in `routing_log` vs the call arrival time.

**"agent's cell never rings"** — verify the Worker's `contact_uri` attribute is
the agent's E.164 cell. Run `python taskrouter_setup.py print-config` to inspect.

**"tasks pile up but never route"** — every Worker is Offline. Open the agent
page and tap Available, or check `/agents` for live status.

**"events callback returns 500"** — `dashboard.py` couldn't import
`transfer_queue` or `agent_state` (likely missing env var raising on import).
Run `python -c "import dashboard"` to surface the error.
