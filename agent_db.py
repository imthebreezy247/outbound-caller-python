"""
agent_db - persistence for human agents, their presence state, and call-routing audit.

Extends the existing calls.db (same SQLite file as transcript_logger) with three new tables:

  agents          - one row per human agent. Mirrors Twilio TaskRouter Worker, holds
                    cell phone + state licenses + languages for routing.
  agent_activity  - append-only log of every Available/Busy/Wrap-Up/Lunch/Offline
                    transition. Drives utilization reports + the dead-man's switch.
  routing_log     - one row per warm-transfer attempt. Stitches together the journey:
                    AI agent prepares -> Twilio queue receives -> TaskRouter assigns ->
                    agent accepts/declines -> call bridges -> wrap-up. Joins to calls(id)
                    by call_id, and to TaskRouter by task_sid.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, cast

DB_PATH = Path("calls.db")
_LOCK = threading.Lock()


# Activity enum mirrors TaskRouter Activity friendlyNames. Keep in sync with
# taskrouter_setup.py — these strings are written verbatim into Twilio.
class Activity:
    AVAILABLE = "Available"
    BUSY = "Busy"
    WRAP_UP = "Wrap-Up"
    LUNCH = "Lunch"
    OFFLINE = "Offline"
    ALL = (AVAILABLE, BUSY, WRAP_UP, LUNCH, OFFLINE)


def _init() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          email TEXT UNIQUE,
          cell_phone TEXT NOT NULL,             -- E.164, e.g. +12025551234
          worker_sid TEXT UNIQUE,                -- TaskRouter Worker SID (WK...)
          state_licenses TEXT DEFAULT '[]',      -- JSON array, e.g. ["TX","FL","GA"]
          languages TEXT DEFAULT '["en"]',       -- JSON array
          is_manager INTEGER DEFAULT 0,          -- 1 = receives compliance-flag escalations
          active INTEGER DEFAULT 1,              -- 0 = retired; never routed to
          current_activity TEXT DEFAULT 'Offline',
          activity_changed_at REAL,
          last_heartbeat REAL,                   -- agent_app posts every 30s
          created_at REAL DEFAULT (strftime('%s','now')),
          updated_at REAL DEFAULT (strftime('%s','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_agents_worker_sid ON agents(worker_sid);
        CREATE INDEX IF NOT EXISTS idx_agents_active_activity ON agents(active, current_activity);

        CREATE TABLE IF NOT EXISTS agent_activity (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          agent_id INTEGER NOT NULL,
          activity TEXT NOT NULL,
          started_at REAL NOT NULL,
          ended_at REAL,                          -- null while current
          reason TEXT,                            -- 'manual', 'auto_busy_on_call', 'wrap_up_timeout', 'heartbeat_lost'
          FOREIGN KEY (agent_id) REFERENCES agents(id)
        );
        CREATE INDEX IF NOT EXISTS idx_activity_agent_started ON agent_activity(agent_id, started_at DESC);

        CREATE TABLE IF NOT EXISTS routing_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          call_id TEXT,                           -- Stephen's internal call_id, joins to calls.id
          task_sid TEXT UNIQUE,                   -- TaskRouter Task SID (WT...)
          lead_phone TEXT NOT NULL,               -- E.164, used to look up routing intent at the Twilio voice webhook
          first_name TEXT,
          required_state TEXT,                    -- 2-letter state for license-skill match
          temperature TEXT DEFAULT 'warm',        -- hot | warm | compliance | callback
          prefer_agent_sid TEXT,                  -- sticky callback target
          agent_id INTEGER,                       -- filled in once assigned
          worker_sid TEXT,                        -- TaskRouter worker that took it
          prepared_at REAL,                       -- AI agent called /transfer/prepare
          enqueued_at REAL,                       -- Twilio voice webhook returned <Enqueue>
          assigned_at REAL,                       -- TaskRouter fired Assignment Callback
          accepted_at REAL,                       -- agent's cell picked up
          completed_at REAL,                      -- call bridged ended
          outcome TEXT,                           -- bridged | abandoned | no_agent | declined_all | failed
          FOREIGN KEY (agent_id) REFERENCES agents(id),
          FOREIGN KEY (call_id) REFERENCES calls(id)
        );
        CREATE INDEX IF NOT EXISTS idx_routing_lead_phone ON routing_log(lead_phone, prepared_at DESC);
        CREATE INDEX IF NOT EXISTS idx_routing_task_sid ON routing_log(task_sid);
        CREATE INDEX IF NOT EXISTS idx_routing_call_id ON routing_log(call_id);

        CREATE TABLE IF NOT EXISTS pending_callbacks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          lead_phone TEXT NOT NULL,             -- E.164
          first_name TEXT,
          required_state TEXT,
          prefer_agent_sid TEXT,                -- sticky to the original agent if known
          source TEXT NOT NULL,                 -- 'no_agent_timeout' | 'prospect_requested' | 'manual'
          reason TEXT,                          -- free-text context ("queue timed out", "prospect requested 3pm")
          requested_at_local TEXT,              -- prospect's stated time as a string (we don't parse for MVP)
          scheduled_for REAL,                   -- earliest dial time (unix ts); null = ASAP
          created_at REAL DEFAULT (strftime('%s','now')),
          dispatched_at REAL,                   -- when the dialer picked it up
          dispatched_call_id TEXT,
          attempts INTEGER DEFAULT 0,
          last_attempt_at REAL,
          status TEXT DEFAULT 'pending',        -- pending | dispatched | completed | gave_up
          related_routing_id INTEGER,           -- the original routing row that timed out (if applicable)
          FOREIGN KEY (related_routing_id) REFERENCES routing_log(id)
        );
        CREATE INDEX IF NOT EXISTS idx_callbacks_status_scheduled ON pending_callbacks(status, scheduled_for);
        CREATE INDEX IF NOT EXISTS idx_callbacks_lead_phone ON pending_callbacks(lead_phone);
        """)
        # WAL gives us better concurrent read perf while transcript_logger writes.
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")


_init()


# ---------------------------------------------------------------------------
# Agents CRUD
# ---------------------------------------------------------------------------

def register_agent(
    *,
    name: str,
    cell_phone: str,
    email: str | None = None,
    state_licenses: Iterable[str] = (),
    languages: Iterable[str] = ("en",),
    is_manager: bool = False,
    worker_sid: str | None = None,
) -> int:
    """Insert a new agent. Returns the agent_id. Idempotent on email if provided."""
    now = time.time()
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        if email:
            existing = c.execute("SELECT id FROM agents WHERE email = ?", (email,)).fetchone()
            if existing:
                return cast(int, existing["id"])
        cur = c.execute(
            """INSERT INTO agents
               (name, email, cell_phone, worker_sid, state_licenses, languages,
                is_manager, current_activity, activity_changed_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name,
                email,
                cell_phone,
                worker_sid,
                json.dumps(sorted({s.upper() for s in state_licenses})),
                json.dumps(sorted({lng.lower() for lng in languages})),
                1 if is_manager else 0,
                Activity.OFFLINE,
                now,
                now,
                now,
            ),
        )
        return int(cur.lastrowid or 0)


def attach_worker_sid(agent_id: int, worker_sid: str) -> None:
    """Called once the TaskRouter Worker is created. Idempotent."""
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "UPDATE agents SET worker_sid = ?, updated_at = ? WHERE id = ?",
            (worker_sid, time.time(), agent_id),
        )


def list_agents(active_only: bool = True) -> list[dict]:
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        q = "SELECT * FROM agents"
        if active_only:
            q += " WHERE active = 1"
        q += " ORDER BY name ASC"
        rows = c.execute(q).fetchall()
    return [d for r in rows if (d := _row_to_agent_dict(r)) is not None]


def get_agent(agent_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return _row_to_agent_dict(row) if row else None


def get_agent_by_worker_sid(worker_sid: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM agents WHERE worker_sid = ?", (worker_sid,)).fetchone()
        return _row_to_agent_dict(row) if row else None


def _row_to_agent_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    d["state_licenses"] = json.loads(d.get("state_licenses") or "[]")
    d["languages"] = json.loads(d.get("languages") or "[]")
    d["is_manager"] = bool(d.get("is_manager"))
    d["active"] = bool(d.get("active"))
    return d


# ---------------------------------------------------------------------------
# Activity transitions
# ---------------------------------------------------------------------------

def set_activity(agent_id: int, activity: str, reason: str = "manual") -> None:
    """
    Close the open activity row (if any) and open a new one. Idempotent: if the
    agent is already in `activity`, this is a no-op (no churn in the log).
    """
    if activity not in Activity.ALL:
        raise ValueError(f"unknown activity: {activity!r} (allowed: {Activity.ALL})")
    now = time.time()
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT current_activity FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"no agent with id={agent_id}")
        if row["current_activity"] == activity:
            return
        c.execute(
            "UPDATE agent_activity SET ended_at = ? WHERE agent_id = ? AND ended_at IS NULL",
            (now, agent_id),
        )
        c.execute(
            "INSERT INTO agent_activity(agent_id, activity, started_at, reason) VALUES (?,?,?,?)",
            (agent_id, activity, now, reason),
        )
        c.execute(
            "UPDATE agents SET current_activity = ?, activity_changed_at = ?, updated_at = ? WHERE id = ?",
            (activity, now, now, agent_id),
        )


def heartbeat(agent_id: int) -> None:
    """Agent web app pings this every 30s. Dead-man's switch reads `last_heartbeat`."""
    now = time.time()
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "UPDATE agents SET last_heartbeat = ?, updated_at = ? WHERE id = ?",
            (now, now, agent_id),
        )


def reap_stale_heartbeats(stale_seconds: float = 90.0) -> list[int]:
    """
    Flip any non-Offline agent whose last heartbeat is older than `stale_seconds`
    to Offline. Returns list of agent_ids that got reaped. Call from a periodic
    task (e.g. once per minute from dashboard).
    """
    cutoff = time.time() - stale_seconds
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """SELECT id FROM agents
               WHERE active = 1
                 AND current_activity != ?
                 AND COALESCE(last_heartbeat, 0) < ?""",
            (Activity.OFFLINE, cutoff),
        ).fetchall()
    reaped = []
    for row in rows:
        set_activity(int(row["id"]), Activity.OFFLINE, reason="heartbeat_lost")
        reaped.append(int(row["id"]))
    return reaped


# ---------------------------------------------------------------------------
# Routing log
# ---------------------------------------------------------------------------

def prepare_routing(
    *,
    call_id: str,
    lead_phone: str,
    first_name: str | None,
    required_state: str | None,
    temperature: str = "warm",
    prefer_agent_sid: str | None = None,
) -> int:
    """
    Called by the AI agent (Stephen) right before SIP-REFERring the call to the
    queue number. Stores routing intent so the Twilio voice webhook can look
    it up by lead_phone when the bridged call arrives.
    """
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            """INSERT INTO routing_log
               (call_id, lead_phone, first_name, required_state, temperature,
                prefer_agent_sid, prepared_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                call_id,
                lead_phone,
                first_name,
                (required_state or "").upper() or None,
                temperature,
                prefer_agent_sid,
                time.time(),
            ),
        )
        return int(cur.lastrowid or 0)


def latest_routing_for_phone(lead_phone: str, within_seconds: float = 120.0) -> dict | None:
    """
    Twilio voice webhook calls this when the REFERred call arrives. Returns
    the most recent routing intent for that caller, prepared within the window.
    """
    cutoff = time.time() - within_seconds
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            """SELECT * FROM routing_log
               WHERE lead_phone = ? AND prepared_at >= ?
               ORDER BY prepared_at DESC LIMIT 1""",
            (lead_phone, cutoff),
        ).fetchone()
        return dict(row) if row else None


def mark_enqueued(routing_id: int, task_sid: str) -> None:
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "UPDATE routing_log SET task_sid = ?, enqueued_at = ? WHERE id = ?",
            (task_sid, time.time(), routing_id),
        )


def mark_assigned(task_sid: str, worker_sid: str) -> None:
    now = time.time()
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        agent = c.execute(
            "SELECT id FROM agents WHERE worker_sid = ?", (worker_sid,)
        ).fetchone()
        agent_id = cast(int, agent["id"]) if agent else None
        c.execute(
            """UPDATE routing_log
               SET worker_sid = ?, agent_id = ?, assigned_at = ?
               WHERE task_sid = ?""",
            (worker_sid, agent_id, now, task_sid),
        )


def mark_accepted(task_sid: str) -> None:
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "UPDATE routing_log SET accepted_at = ? WHERE task_sid = ?",
            (time.time(), task_sid),
        )


def mark_completed(task_sid: str, outcome: str) -> None:
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "UPDATE routing_log SET completed_at = ?, outcome = ? WHERE task_sid = ?",
            (time.time(), outcome, task_sid),
        )


def routing_history(limit: int = 200) -> list[dict]:
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """SELECT r.*, a.name AS agent_name
               FROM routing_log r
               LEFT JOIN agents a ON a.id = r.agent_id
               ORDER BY r.prepared_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Pending callbacks (Phase 3)
# ---------------------------------------------------------------------------

def add_pending_callback(
    *,
    lead_phone: str,
    first_name: str | None = None,
    required_state: str | None = None,
    prefer_agent_sid: str | None = None,
    source: str = "manual",
    reason: str | None = None,
    requested_at_local: str | None = None,
    scheduled_for: float | None = None,
    related_routing_id: int | None = None,
) -> int:
    """
    Schedule a callback. Sources:
      'no_agent_timeout'   - queue exhausted with no agent available
      'prospect_requested' - prospect asked Stephen to be called back
      'manual'             - manager queued from /agents
    """
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            """INSERT INTO pending_callbacks
               (lead_phone, first_name, required_state, prefer_agent_sid,
                source, reason, requested_at_local, scheduled_for,
                related_routing_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                lead_phone,
                first_name,
                (required_state or "").upper() or None,
                prefer_agent_sid,
                source,
                reason,
                requested_at_local,
                scheduled_for,
                related_routing_id,
            ),
        )
        return cast(int, cur.lastrowid)


def list_pending_callbacks(limit: int = 100, status: str | None = "pending") -> list[dict]:
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        if status:
            rows = c.execute(
                """SELECT * FROM pending_callbacks
                   WHERE status = ?
                   ORDER BY COALESCE(scheduled_for, created_at) ASC LIMIT ?""",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM pending_callbacks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def claim_due_callbacks(now_ts: float, limit: int = 50) -> list[dict]:
    """
    Returns pending callbacks whose scheduled_for has elapsed (or is null = ASAP),
    marking each as dispatched in the same transaction so two dialers don't grab
    the same row. Caller is expected to actually place the calls.
    """
    out: list[dict] = []
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """SELECT * FROM pending_callbacks
               WHERE status = 'pending'
                 AND (scheduled_for IS NULL OR scheduled_for <= ?)
               ORDER BY COALESCE(scheduled_for, created_at) ASC LIMIT ?""",
            (now_ts, limit),
        ).fetchall()
        for r in rows:
            c.execute(
                """UPDATE pending_callbacks
                   SET status='dispatched', dispatched_at=?, attempts=attempts+1, last_attempt_at=?
                   WHERE id = ?""",
                (now_ts, now_ts, r["id"]),
            )
            out.append(dict(r))
    return out


def mark_callback_dispatched(callback_id: int, dispatched_call_id: str | None = None) -> None:
    """Manually mark a callback row as dispatched (when a caller picks one up out-of-band)."""
    now = time.time()
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.execute(
            """UPDATE pending_callbacks
               SET status='dispatched', dispatched_at=?, dispatched_call_id=?,
                   attempts=attempts+1, last_attempt_at=?
               WHERE id = ?""",
            (now, dispatched_call_id, now, callback_id),
        )


def mark_callback_completed(callback_id: int) -> None:
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "UPDATE pending_callbacks SET status='completed' WHERE id = ?",
            (callback_id,),
        )


def mark_callback_gave_up(callback_id: int, reason: str | None = None) -> None:
    with _LOCK, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "UPDATE pending_callbacks SET status='gave_up', reason = COALESCE(?, reason) WHERE id = ?",
            (reason, callback_id),
        )


def get_routing_by_task(task_sid: str) -> dict | None:
    """Look up a routing_log row by Twilio task SID. Used by the queue-timeout handler."""
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM routing_log WHERE task_sid = ?", (task_sid,)
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Routing-time queries (read by Twilio webhook handlers)
# ---------------------------------------------------------------------------

def pick_eligible_workers(required_state: str | None, language: str = "en") -> list[dict]:
    """
    Returns Available agents whose state_licenses include `required_state` (or all if None)
    and whose languages include `language`. Sorted by activity_changed_at ascending so the
    least-recently-Available agent gets the next call (LRU fairness).
    """
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """SELECT * FROM agents
               WHERE active = 1 AND current_activity = ?
               ORDER BY COALESCE(activity_changed_at, created_at) ASC""",
            (Activity.AVAILABLE,),
        ).fetchall()
    out: list[dict] = []
    rs = (required_state or "").upper()
    for row in rows:
        agent = _row_to_agent_dict(row)
        if not agent:
            continue
        if rs and rs not in agent["state_licenses"]:
            continue
        if language not in agent["languages"]:
            continue
        out.append(agent)
    return out
