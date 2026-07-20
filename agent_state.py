"""
agent_state - agent-facing presence + the wrap-up timer.

Two halves:

  1. APIRouter mounted at /api/agents:
       GET    /api/agents                 - list all agents + activity + last heartbeat
       GET    /api/agents/{id}             - one agent
       PUT    /api/agents/{id}/activity    - agent flips Available/Lunch/Offline
       POST   /api/agents/{id}/heartbeat   - agent web app pings every 30s

     These are the endpoints the agent's browser hits when they tap their status.

  2. Wrap-up timer:
       After a call ends, TaskRouter sets the worker's activity to Wrap-Up via
       `post_work_activity_sid`. We schedule an async task to flip them back to
       Available after WRAPUP_SECONDS, unless the agent has manually moved
       themselves to Lunch/Offline in the meantime.

  3. Stale-heartbeat reaper:
       Periodic task launched at app startup. Any non-Offline agent whose last
       heartbeat is older than HEARTBEAT_STALE_SECONDS gets auto-flipped Offline
       so we don't ring a closed laptop.

Activity rules (who can set what):
  AGENT (via UI)    -> Available, Lunch, Offline
  TWILIO (via event) -> Busy (on reservation accepted), Wrap-Up (post-call)
  SYSTEM (timer)    -> Available (auto-end of wrap-up), Offline (heartbeat lost)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Body, HTTPException

import agent_db

logger = logging.getLogger("agent-state")

router = APIRouter(prefix="/api/agents", tags=["agents"])

WRAPUP_SECONDS = float(os.getenv("WRAPUP_SECONDS", "30"))
HEARTBEAT_STALE_SECONDS = float(os.getenv("HEARTBEAT_STALE_SECONDS", "90"))
REAPER_INTERVAL_SECONDS = float(os.getenv("REAPER_INTERVAL_SECONDS", "60"))

# Activities agents are allowed to set from the UI.
_AGENT_SETTABLE = {agent_db.Activity.AVAILABLE, agent_db.Activity.LUNCH, agent_db.Activity.OFFLINE}

# Outstanding wrap-up auto-flip timers, keyed by agent_id, so we can cancel
# one if the agent manually moves themselves before it fires.
_wrapup_tasks: dict[int, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# TaskRouter sync — push local activity changes up to the Worker
# ---------------------------------------------------------------------------

def _activity_to_twilio_sid(activity: str) -> str | None:
    env_key = {
        agent_db.Activity.AVAILABLE: "TASKROUTER_ACTIVITY_AVAILABLE_SID",
        agent_db.Activity.BUSY:      "TASKROUTER_ACTIVITY_BUSY_SID",
        agent_db.Activity.WRAP_UP:   "TASKROUTER_ACTIVITY_WRAPUP_SID",
        agent_db.Activity.LUNCH:     "TASKROUTER_ACTIVITY_LUNCH_SID",
        agent_db.Activity.OFFLINE:   "TASKROUTER_ACTIVITY_OFFLINE_SID",
    }.get(activity)
    return os.getenv(env_key, "") if env_key else None


def _push_to_taskrouter(worker_sid: str, activity: str) -> None:
    """Update the TaskRouter Worker's activity. No-op if creds/sids missing."""
    workspace_sid = os.getenv("TASKROUTER_WORKSPACE_SID", "")
    activity_sid = _activity_to_twilio_sid(activity)
    if not (workspace_sid and activity_sid and worker_sid):
        logger.debug("skipping taskrouter push (workspace=%s activity_sid=%s worker=%s)",
                     workspace_sid, activity_sid, worker_sid)
        return
    try:
        from twilio.rest import Client
        client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        client.taskrouter.v1.workspaces(workspace_sid).workers(worker_sid).update(
            activity_sid=activity_sid
        )
    except Exception as e:
        logger.error("taskrouter push failed for worker=%s -> %s: %s", worker_sid, activity, e)


# ---------------------------------------------------------------------------
# Wrap-up timer
# ---------------------------------------------------------------------------

async def _wrapup_auto_flip(agent_id: int) -> None:
    """Sleep WRAPUP_SECONDS, then flip back to Available iff still in Wrap-Up."""
    try:
        await asyncio.sleep(WRAPUP_SECONDS)
        agent = agent_db.get_agent(agent_id)
        if not agent:
            return
        if agent["current_activity"] != agent_db.Activity.WRAP_UP:
            # Agent moved themselves (e.g., to Lunch) during wrap-up; respect that.
            return
        agent_db.set_activity(agent_id, agent_db.Activity.AVAILABLE, reason="wrap_up_timeout")
        if agent.get("worker_sid"):
            _push_to_taskrouter(agent["worker_sid"], agent_db.Activity.AVAILABLE)
        logger.info("agent %d auto-flipped Wrap-Up -> Available", agent_id)
    except asyncio.CancelledError:
        pass
    finally:
        _wrapup_tasks.pop(agent_id, None)


def on_wrapup_started(agent_id: int) -> None:
    """Called by /api/transfer/event when TaskRouter sets a worker to Wrap-Up."""
    old = _wrapup_tasks.pop(agent_id, None)
    if old and not old.done():
        old.cancel()
    _wrapup_tasks[agent_id] = asyncio.create_task(_wrapup_auto_flip(agent_id))


# ---------------------------------------------------------------------------
# Stale-heartbeat reaper (started by dashboard at app boot)
# ---------------------------------------------------------------------------

async def _reaper_loop() -> None:
    while True:
        try:
            reaped = agent_db.reap_stale_heartbeats(stale_seconds=HEARTBEAT_STALE_SECONDS)
            for agent_id in reaped:
                agent = agent_db.get_agent(agent_id)
                if agent and agent.get("worker_sid"):
                    _push_to_taskrouter(agent["worker_sid"], agent_db.Activity.OFFLINE)
                logger.info("reaped stale agent %d -> Offline", agent_id)
        except Exception:
            logger.exception("reaper loop error")
        await asyncio.sleep(REAPER_INTERVAL_SECONDS)


def start_background_tasks() -> asyncio.Task:
    """Call from FastAPI startup. Returns the reaper task so caller can cancel on shutdown."""
    return asyncio.create_task(_reaper_loop())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
def list_agents_endpoint() -> list[dict[str, Any]]:
    return agent_db.list_agents(active_only=True)


@router.get("/{agent_id}")
def get_agent_endpoint(agent_id: int) -> dict[str, Any]:
    agent = agent_db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"no agent with id={agent_id}")
    return agent


@router.put("/{agent_id}/activity")
def set_activity_endpoint(agent_id: int, payload: dict = Body(...)) -> dict[str, Any]:
    """
    Agent UI calls this to flip Available/Lunch/Offline. Pushes the change
    to TaskRouter so the routing decisions see it.
    """
    activity = (payload or {}).get("activity", "")
    if activity not in _AGENT_SETTABLE:
        raise HTTPException(400, f"agents may only set {_AGENT_SETTABLE!r}; got {activity!r}")
    agent = agent_db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"no agent with id={agent_id}")

    agent_db.set_activity(agent_id, activity, reason="manual")
    # If the agent is leaving Wrap-Up early (Lunch / Offline), cancel pending auto-flip.
    task = _wrapup_tasks.pop(agent_id, None)
    if task and not task.done():
        task.cancel()

    if agent.get("worker_sid"):
        _push_to_taskrouter(agent["worker_sid"], activity)

    return agent_db.get_agent(agent_id) or {}


@router.post("/{agent_id}/heartbeat")
def heartbeat_endpoint(agent_id: int) -> dict[str, Any]:
    if not agent_db.get_agent(agent_id):
        raise HTTPException(404, f"no agent with id={agent_id}")
    agent_db.heartbeat(agent_id)
    return {"ok": True}
