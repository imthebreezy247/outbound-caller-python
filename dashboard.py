"""
Live Dashboard for AI Outbound Caller Agents

A local web dashboard that provides:
- Real-time call status feed
- Dispatch new outbound calls
- View active rooms and participants
- Live event log from agents

Run: python dashboard.py
Then open: http://localhost:8000
"""

import os
import json
import uuid
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

from livekit import api

load_dotenv(dotenv_path=".env.local")

logger = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Outbound Caller Dashboard")

# ── Shared state ──────────────────────────────────────────────────────────────

# Connected dashboard WebSocket clients
ws_clients: list[WebSocket] = []

# In-memory event log (newest first, capped at 500)
event_log: list[dict[str, Any]] = []
MAX_LOG = 500

# Track active calls
active_calls: dict[str, dict[str, Any]] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def lk_api() -> api.LiveKitAPI:
    """Create a LiveKit API client from env vars."""
    return api.LiveKitAPI(
        url=os.getenv("LIVEKIT_URL", ""),
        api_key=os.getenv("LIVEKIT_API_KEY", ""),
        api_secret=os.getenv("LIVEKIT_API_SECRET", ""),
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def broadcast(event: dict):
    """Send an event to all connected dashboard clients."""
    event_log.insert(0, event)
    if len(event_log) > MAX_LOG:
        event_log.pop()
    payload = json.dumps(event)
    dead: list[WebSocket] = []
    for ws in ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.remove(ws)


async def push_event(event_type: str, data: dict):
    """Create and broadcast a timestamped event."""
    await broadcast({"type": event_type, "ts": now_iso(), **data})


# ── Public API for agent.py to push events ────────────────────────────────────

async def agent_event(event_type: str, data: dict):
    """Called from agent.py to push live events to the dashboard."""
    call_id = data.get("phone_number", "unknown")

    if event_type == "call_started":
        active_calls[call_id] = {
            "phone_number": data.get("phone_number"),
            "status": "ringing",
            "started_at": now_iso(),
            "room": data.get("room", ""),
        }
    elif event_type == "call_connected":
        if call_id in active_calls:
            active_calls[call_id]["status"] = "connected"
    elif event_type == "call_transferring":
        if call_id in active_calls:
            active_calls[call_id]["status"] = "transferring"
    elif event_type in ("call_ended", "call_error"):
        active_calls.pop(call_id, None)

    await push_event(event_type, data)


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return DASHBOARD_HTML


@app.get("/api/status")
async def get_status():
    """Return current active calls and recent events."""
    return {
        "active_calls": list(active_calls.values()),
        "recent_events": event_log[:50],
    }


@app.get("/api/rooms")
async def get_rooms():
    """List active LiveKit rooms."""
    lk = lk_api()
    try:
        rooms = await lk.room.list_rooms(api.ListRoomsRequest())
        return {
            "rooms": [
                {
                    "name": r.name,
                    "num_participants": r.num_participants,
                    "created_at": r.creation_time,
                }
                for r in rooms
            ]
        }
    finally:
        await lk.aclose()


@app.post("/api/dispatch")
async def dispatch_call(payload: dict):
    """Dispatch an outbound call via the LiveKit agent."""
    phone_number = payload.get("phone_number", "").strip()
    transfer_to = payload.get("transfer_to", "").strip()

    if not phone_number:
        return {"error": "phone_number is required"}, 400

    if not transfer_to:
        transfer_to = os.getenv("MAX_PHONE_NUMBER", "+19412314887")

    metadata = json.dumps({
        "phone_number": phone_number,
        "transfer_to": transfer_to,
    })

    room_name = f"outbound-call-{phone_number.replace('+', '')}-{uuid.uuid4().hex[:8]}"

    lk = lk_api()
    try:
        dispatch = await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="outbound-caller",
                room=room_name,
                metadata=metadata,
            )
        )

        await push_event("dispatch", {
            "phone_number": phone_number,
            "transfer_to": transfer_to,
            "room": room_name,
            "dispatch_id": dispatch.id,
        })

        return {
            "success": True,
            "dispatch_id": dispatch.id,
            "room": room_name,
        }
    except Exception as e:
        logger.error(f"Dispatch error: {e}")
        return {"error": str(e)}
    finally:
        await lk.aclose()


# ── WebSocket for live feed ───────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    # Send recent history on connect
    try:
        await ws.send_text(json.dumps({
            "type": "init",
            "active_calls": list(active_calls.values()),
            "recent_events": event_log[:50],
        }))
        while True:
            # Keep connection alive; client can also send messages
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in ws_clients:
            ws_clients.remove(ws)


# ── Periodic room polling (updates active call list from LiveKit) ─────────────

async def poll_rooms():
    """Poll LiveKit every 5s to sync active rooms with the dashboard."""
    while True:
        await asyncio.sleep(5)
        try:
            lk = lk_api()
            rooms = await lk.room.list_rooms(api.ListRoomsRequest())
            room_names = {r.name for r in rooms}

            # Update active calls based on actual rooms
            for call_id in list(active_calls.keys()):
                call = active_calls[call_id]
                if call.get("room") and call["room"] not in room_names:
                    active_calls.pop(call_id, None)
                    await push_event("call_ended", {
                        "phone_number": call_id,
                        "room": call.get("room", ""),
                        "reason": "room_closed",
                    })

            # Broadcast room count update
            await push_event("rooms_update", {
                "count": len(rooms),
                "rooms": [
                    {
                        "name": r.name,
                        "participants": r.num_participants,
                    }
                    for r in rooms
                ],
            })
            await lk.aclose()
        except Exception as e:
            logger.debug(f"Poll error: {e}")


@app.on_event("startup")
async def startup():
    asyncio.create_task(poll_rooms())


# ── Dashboard HTML ────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Outbound Caller Dashboard</title>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #242836;
    --border: #2e3345;
    --text: #e4e6f0;
    --text-dim: #8b8fa3;
    --accent: #6c5ce7;
    --accent-glow: #6c5ce740;
    --green: #00d68f;
    --green-dim: #00d68f30;
    --red: #ff6b6b;
    --red-dim: #ff6b6b30;
    --yellow: #ffd93d;
    --yellow-dim: #ffd93d30;
    --blue: #4dabf7;
    --blue-dim: #4dabf730;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: 'Segoe UI', -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }

  /* Header */
  .header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header h1 {
    font-size: 20px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .header h1 .dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  .header .status-bar {
    display: flex; gap: 20px; font-size: 13px; color: var(--text-dim);
  }
  .header .status-bar span { display: flex; align-items: center; gap: 6px; }

  /* Layout */
  .container {
    display: grid;
    grid-template-columns: 340px 1fr;
    height: calc(100vh - 57px);
  }

  /* Sidebar */
  .sidebar {
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .sidebar-section {
    padding: 16px;
    border-bottom: 1px solid var(--border);
  }
  .sidebar-section h2 {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-dim);
    margin-bottom: 12px;
  }

  /* Dispatch form */
  .form-group { margin-bottom: 10px; }
  .form-group label {
    display: block;
    font-size: 12px;
    color: var(--text-dim);
    margin-bottom: 4px;
  }
  .form-group input {
    width: 100%;
    padding: 8px 12px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-size: 14px;
    outline: none;
    transition: border-color 0.2s;
  }
  .form-group input:focus { border-color: var(--accent); }
  .form-group input::placeholder { color: var(--text-dim); }
  .btn {
    width: 100%;
    padding: 10px;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
  }
  .btn-primary {
    background: var(--accent);
    color: white;
  }
  .btn-primary:hover { background: #5b4bd5; box-shadow: 0 0 20px var(--accent-glow); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .dispatch-status {
    margin-top: 8px;
    font-size: 12px;
    min-height: 18px;
  }

  /* Active calls */
  .calls-list {
    flex: 1;
    overflow-y: auto;
    padding: 0 16px 16px;
  }
  .call-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    margin-bottom: 8px;
    animation: slideIn 0.3s ease;
  }
  @keyframes slideIn {
    from { opacity: 0; transform: translateY(-10px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .call-card .phone {
    font-size: 15px;
    font-weight: 600;
    margin-bottom: 4px;
  }
  .call-card .meta {
    font-size: 12px;
    color: var(--text-dim);
    display: flex;
    justify-content: space-between;
  }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
  }
  .badge-ringing { background: var(--yellow-dim); color: var(--yellow); }
  .badge-connected { background: var(--green-dim); color: var(--green); }
  .badge-transferring { background: var(--blue-dim); color: var(--blue); }
  .no-calls {
    text-align: center;
    color: var(--text-dim);
    font-size: 13px;
    padding: 20px;
  }

  /* Main content - event feed */
  .main {
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .feed-header {
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .feed-header h2 { font-size: 15px; font-weight: 600; }
  .feed-header .controls { display: flex; gap: 8px; }
  .feed-header .controls button {
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 4px 12px;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
  }
  .feed-header .controls button:hover { color: var(--text); border-color: var(--text-dim); }

  .feed {
    flex: 1;
    overflow-y: auto;
    padding: 12px 20px;
  }
  .event {
    display: flex;
    gap: 12px;
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    animation: fadeIn 0.3s ease;
    font-size: 13px;
  }
  @keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
  }
  .event .time {
    color: var(--text-dim);
    font-size: 12px;
    font-family: 'Consolas', monospace;
    min-width: 80px;
    flex-shrink: 0;
  }
  .event .tag {
    font-size: 11px;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 3px;
    min-width: 90px;
    text-align: center;
    flex-shrink: 0;
  }
  .tag-dispatch { background: var(--accent); color: white; }
  .tag-call_started { background: var(--yellow-dim); color: var(--yellow); }
  .tag-call_connected { background: var(--green-dim); color: var(--green); }
  .tag-call_ended { background: var(--red-dim); color: var(--red); }
  .tag-call_error { background: var(--red-dim); color: var(--red); }
  .tag-call_transferring { background: var(--blue-dim); color: var(--blue); }
  .tag-rooms_update { background: var(--surface2); color: var(--text-dim); }
  .tag-agent_log { background: var(--surface2); color: var(--text-dim); }
  .event .msg { color: var(--text); line-height: 1.4; }

  /* Rooms strip */
  .rooms-strip {
    background: var(--surface);
    border-top: 1px solid var(--border);
    padding: 10px 20px;
    font-size: 12px;
    color: var(--text-dim);
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
  }
  .room-chip {
    background: var(--surface2);
    border: 1px solid var(--border);
    padding: 4px 10px;
    border-radius: 4px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .room-chip .room-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--green);
  }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--text-dim); }
</style>
</head>
<body>

<div class="header">
  <h1><span class="dot" id="connDot"></span> Outbound Caller Dashboard</h1>
  <div class="status-bar">
    <span>WS: <strong id="wsStatus">connecting...</strong></span>
    <span>Active Calls: <strong id="callCount">0</strong></span>
    <span>Rooms: <strong id="roomCount">0</strong></span>
  </div>
</div>

<div class="container">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-section">
      <h2>Dispatch New Call</h2>
      <div class="form-group">
        <label>Phone Number (E.164)</label>
        <input type="tel" id="phoneNumber" placeholder="+1XXXXXXXXXX" />
      </div>
      <div class="form-group">
        <label>Transfer To (optional)</label>
        <input type="tel" id="transferTo" placeholder="+1YYYYYYYYYY" />
      </div>
      <button class="btn btn-primary" id="dispatchBtn" onclick="dispatchCall()">
        Dispatch Call
      </button>
      <div class="dispatch-status" id="dispatchStatus"></div>
    </div>

    <div class="sidebar-section">
      <h2>Active Calls</h2>
    </div>
    <div class="calls-list" id="callsList">
      <div class="no-calls">No active calls</div>
    </div>
  </div>

  <!-- Main feed -->
  <div class="main">
    <div class="feed-header">
      <h2>Live Event Feed</h2>
      <div class="controls">
        <button onclick="clearFeed()">Clear</button>
        <button onclick="toggleAutoscroll()" id="scrollBtn">Auto-scroll: ON</button>
      </div>
    </div>
    <div class="feed" id="feed"></div>
    <div class="rooms-strip" id="roomsStrip">
      <span>Rooms: none</span>
    </div>
  </div>
</div>

<script>
  let ws;
  let autoscroll = true;
  let activeCalls = {};

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);

    ws.onopen = () => {
      document.getElementById('wsStatus').textContent = 'connected';
      document.getElementById('connDot').style.background = 'var(--green)';
    };

    ws.onclose = () => {
      document.getElementById('wsStatus').textContent = 'disconnected';
      document.getElementById('connDot').style.background = 'var(--red)';
      setTimeout(connect, 2000);
    };

    ws.onmessage = (e) => {
      const data = JSON.parse(e.data);

      if (data.type === 'init') {
        // Load initial state
        if (data.active_calls) {
          data.active_calls.forEach(c => {
            activeCalls[c.phone_number] = c;
          });
          renderCalls();
        }
        if (data.recent_events) {
          data.recent_events.reverse().forEach(ev => addEvent(ev, false));
        }
        return;
      }

      handleEvent(data);
    };
  }

  function handleEvent(ev) {
    const phone = ev.phone_number || '';

    switch (ev.type) {
      case 'dispatch':
        break;
      case 'call_started':
        activeCalls[phone] = {
          phone_number: phone,
          status: 'ringing',
          started_at: ev.ts,
          room: ev.room || '',
        };
        break;
      case 'call_connected':
        if (activeCalls[phone]) activeCalls[phone].status = 'connected';
        break;
      case 'call_transferring':
        if (activeCalls[phone]) activeCalls[phone].status = 'transferring';
        break;
      case 'call_ended':
      case 'call_error':
        delete activeCalls[phone];
        break;
      case 'rooms_update':
        updateRooms(ev.rooms || []);
        document.getElementById('roomCount').textContent = ev.count || 0;
        break;
    }

    renderCalls();
    addEvent(ev, true);
  }

  function renderCalls() {
    const el = document.getElementById('callsList');
    const calls = Object.values(activeCalls);
    document.getElementById('callCount').textContent = calls.length;

    if (calls.length === 0) {
      el.innerHTML = '<div class="no-calls">No active calls</div>';
      return;
    }

    el.innerHTML = calls.map(c => `
      <div class="call-card">
        <div class="phone">${esc(c.phone_number)}</div>
        <div class="meta">
          <span class="badge badge-${c.status}">${c.status}</span>
          <span>${timeAgo(c.started_at)}</span>
        </div>
      </div>
    `).join('');
  }

  function addEvent(ev, animate) {
    const feed = document.getElementById('feed');
    const div = document.createElement('div');
    div.className = 'event';
    if (!animate) div.style.animation = 'none';

    const time = ev.ts ? new Date(ev.ts).toLocaleTimeString() : '--:--:--';
    const tag = ev.type || 'info';
    const msg = formatEvent(ev);

    div.innerHTML = `
      <span class="time">${time}</span>
      <span class="tag tag-${tag}">${tag.replace(/_/g, ' ')}</span>
      <span class="msg">${msg}</span>
    `;

    feed.appendChild(div);
    if (autoscroll) feed.scrollTop = feed.scrollHeight;
  }

  function formatEvent(ev) {
    switch (ev.type) {
      case 'dispatch':
        return `Dispatched call to <strong>${esc(ev.phone_number)}</strong> (transfer: ${esc(ev.transfer_to || 'none')}) - Room: ${esc(ev.room || '')}`;
      case 'call_started':
        return `Calling <strong>${esc(ev.phone_number)}</strong>...`;
      case 'call_connected':
        return `<strong>${esc(ev.phone_number)}</strong> answered the call`;
      case 'call_transferring':
        return `Transferring <strong>${esc(ev.phone_number)}</strong> to ${esc(ev.transfer_to || 'agent')}`;
      case 'call_ended':
        return `Call ended: <strong>${esc(ev.phone_number)}</strong> (${esc(ev.reason || 'completed')})`;
      case 'call_error':
        return `Call error: <strong>${esc(ev.phone_number)}</strong> - ${esc(ev.error || 'unknown')}`;
      case 'rooms_update':
        return `${ev.count || 0} active room(s)`;
      case 'agent_log':
        return `[${esc(ev.phone_number || '')}] ${esc(ev.message || '')}`;
      default:
        return JSON.stringify(ev);
    }
  }

  function updateRooms(rooms) {
    const el = document.getElementById('roomsStrip');
    if (rooms.length === 0) {
      el.innerHTML = '<span>Rooms: none</span>';
      return;
    }
    el.innerHTML = rooms.map(r => `
      <div class="room-chip">
        <span class="room-dot"></span>
        ${esc(r.name)} (${r.participants || 0})
      </div>
    `).join('');
  }

  async function dispatchCall() {
    const btn = document.getElementById('dispatchBtn');
    const status = document.getElementById('dispatchStatus');
    const phone = document.getElementById('phoneNumber').value.trim();
    const transfer = document.getElementById('transferTo').value.trim();

    if (!phone) {
      status.innerHTML = '<span style="color:var(--red)">Enter a phone number</span>';
      return;
    }

    btn.disabled = true;
    status.innerHTML = '<span style="color:var(--yellow)">Dispatching...</span>';

    try {
      const res = await fetch('/api/dispatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone_number: phone,
          transfer_to: transfer || undefined,
        }),
      });
      const data = await res.json();
      if (data.success) {
        status.innerHTML = `<span style="color:var(--green)">Dispatched! Room: ${esc(data.room)}</span>`;
        document.getElementById('phoneNumber').value = '';
      } else {
        status.innerHTML = `<span style="color:var(--red)">Error: ${esc(data.error || 'unknown')}</span>`;
      }
    } catch (e) {
      status.innerHTML = `<span style="color:var(--red)">Network error</span>`;
    }

    btn.disabled = false;
  }

  function clearFeed() {
    document.getElementById('feed').innerHTML = '';
  }

  function toggleAutoscroll() {
    autoscroll = !autoscroll;
    document.getElementById('scrollBtn').textContent = `Auto-scroll: ${autoscroll ? 'ON' : 'OFF'}`;
  }

  function timeAgo(iso) {
    if (!iso) return '';
    const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    return `${Math.floor(diff / 3600)}h ago`;
  }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = String(s || '');
    return d.innerHTML;
  }

  // Enter key dispatches call
  document.getElementById('phoneNumber').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') dispatchCall();
  });

  connect();
</script>
</body>
</html>"""


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  Outbound Caller Dashboard")
    print("  http://localhost:8000")
    print("=" * 50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
