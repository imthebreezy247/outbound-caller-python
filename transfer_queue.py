"""
transfer_queue - the runtime that turns Emma's "transfer to a human" into
a TaskRouter-routed warm transfer that finds the next Available agent.

Exposes a FastAPI APIRouter that dashboard.py mounts under /api/transfer.

Endpoints:

  POST /api/transfer/prepare          (called by agent.py just before SIP REFER)
       Body: { call_id, lead_phone, first_name, required_state, temperature, prefer_agent_sid? }
       Returns: { routing_id, queue_number }   <- agent.py REFERs the call to queue_number

  POST /api/transfer/voice            (Twilio Voice webhook on the queue number)
       Twilio POSTs form data when the REFERred call lands. We look up the routing intent
       by the caller's E.164 and return TwiML <Enqueue> that creates a Task with the
       routing attributes attached.

  POST /api/transfer/assignment       (TaskRouter Assignment Callback)
       TaskRouter POSTs when a worker is chosen. We return a JSON `dequeue` instruction
       that tells Twilio to ring the worker's cell and bridge.

  POST /api/transfer/assignment_fallback   (TaskRouter Fallback)
       Last-resort handler if the primary assignment URL errored. Returns a safe dequeue.

  POST /api/transfer/event            (Workspace Event Callback)
       Mirror TaskRouter events into routing_log (task.created, reservation.accepted,
       task.completed, etc).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import agent_db
from taskrouter_setup import TEMPERATURE_VALUES

# Seconds a task can sit in the queue before we cancel it and offer a callback.
NO_AGENT_TIMEOUT_SECONDS = float(os.getenv("NO_AGENT_TIMEOUT_SECONDS", "90"))

# Asyncio cancel-timers indexed by task_sid. A timer is scheduled when
# task.created fires and is canceled when reservation.accepted fires, so a task
# that finds an agent never gets force-canceled by us.
_pending_cancels: dict[str, asyncio.Task] = {}

logger = logging.getLogger("transfer-queue")

router = APIRouter(prefix="/api/transfer", tags=["transfer"])


# ---------------------------------------------------------------------------
# Config (read at request time so a `.env.local` edit doesn't require a restart
# during dev)
# ---------------------------------------------------------------------------

def _env(name: str, required: bool = True) -> str:
    v = os.getenv(name, "").strip()
    if required and not v:
        raise HTTPException(500, f"Missing env var: {name}. See TASKROUTER_SETUP.md")
    return v


def _workspace_sid() -> str:
    return _env("TASKROUTER_WORKSPACE_SID")


def _workflow_sid() -> str:
    return _env("TASKROUTER_WORKFLOW_SID")


def _queue_number() -> str:
    return _env("TASKROUTER_QUEUE_NUMBER")


def _wrapup_activity_sid() -> str:
    return _env("TASKROUTER_ACTIVITY_WRAPUP_SID")


def _twilio_client():
    # Lazy import so this module loads even when twilio creds aren't set yet.
    from twilio.rest import Client
    return Client(_env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN"))


# ---------------------------------------------------------------------------
# /prepare — called by Emma right before SIP REFER
# ---------------------------------------------------------------------------

@router.post("/prepare")
async def prepare(request: Request) -> JSONResponse:
    """
    Stash routing intent so the voice-webhook handler can pick it up by caller ID
    when Twilio delivers the REFERred call ~1-2 seconds later.
    """
    body: dict[str, Any] = await request.json()
    required = ("call_id", "lead_phone")
    for k in required:
        if not body.get(k):
            raise HTTPException(400, f"missing field: {k}")

    temperature = (body.get("temperature") or "warm").lower()
    if temperature not in TEMPERATURE_VALUES:
        raise HTTPException(
            400, f"invalid temperature {temperature!r}; must be one of {TEMPERATURE_VALUES}"
        )

    routing_id = agent_db.prepare_routing(
        call_id=body["call_id"],
        lead_phone=body["lead_phone"],
        first_name=body.get("first_name"),
        required_state=body.get("required_state"),
        temperature=temperature,
        prefer_agent_sid=body.get("prefer_agent_sid"),
    )
    logger.info(
        "prepared routing #%d for %s (state=%s temp=%s)",
        routing_id, body["lead_phone"], body.get("required_state"), body.get("temperature"),
    )
    return JSONResponse({"routing_id": routing_id, "queue_number": _queue_number()})


# ---------------------------------------------------------------------------
# /voice — Twilio voice webhook on the queue number
# ---------------------------------------------------------------------------

def _twiml_enqueue(
    workflow_sid: str,
    attributes: dict[str, Any],
    *,
    action_url: str | None = None,
    wait_url: str | None = None,
) -> str:
    """Build the <Enqueue> TwiML that hands the call to TaskRouter.

    `action_url` is invoked when the call exits the queue for ANY reason
    (bridged, hung up, or our cancel timer fired). We use it to play the
    "we'll call you back" message on a no-agent exit.
    """
    attrs_json = json.dumps(attributes).replace('"', "&quot;")
    parts = [f'workflowSid="{workflow_sid}"']
    if action_url:
        parts.append(f'action="{action_url}" method="POST"')
    if wait_url:
        parts.append(f'waitUrl="{wait_url}"')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Enqueue {" ".join(parts)}>'
        f'<Task>{attrs_json}</Task>'
        "</Enqueue>"
        "</Response>"
    )


def _twiml_say_and_hangup(message: str) -> str:
    # XML-escape the message defensively; we use Polly Joanna for a warmer voice
    # than the default Twilio Alice. Falls back to default if voice unavailable.
    safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say voice="Polly.Joanna">{safe}</Say><Hangup/></Response>'
    )


@router.post("/voice")
async def voice_webhook(
    From: str = Form(""),
    CallSid: str = Form(""),
    To: str = Form(""),
) -> PlainTextResponse:
    """
    Twilio voice webhook. `From` is the prospect's E.164; we look up the routing intent
    that Emma prepared a moment ago, then return <Enqueue> TwiML.
    """
    routing = agent_db.latest_routing_for_phone(From, within_seconds=120.0)
    if not routing:
        logger.warning("voice webhook for %s but no recent /prepare — using bare defaults", From)
        attrs = {"required_state": None, "temperature": "warm"}
    else:
        attrs = {
            "routing_id": routing["id"],
            "call_id": routing.get("call_id"),
            "lead_phone": routing["lead_phone"],
            "first_name": routing.get("first_name"),
            "required_state": routing.get("required_state"),
            "temperature": routing.get("temperature") or "warm",
        }
        if routing.get("prefer_agent_sid"):
            attrs["prefer_agent_sid"] = routing["prefer_agent_sid"]

    # When the call exits the queue for any reason, Twilio POSTs to /queue_exit
    # so we can play a fallback message (no-agent) or hang up cleanly (bridged).
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    action_url = f"{base}/api/transfer/queue_exit" if base else None

    twiml = _twiml_enqueue(_workflow_sid(), attrs, action_url=action_url)
    logger.info("enqueueing call_sid=%s from=%s attrs=%s", CallSid, From, attrs)
    return PlainTextResponse(content=twiml, media_type="application/xml")


# ---------------------------------------------------------------------------
# /queue_exit — Twilio invokes when caller leaves the queue (bridged or otherwise)
# ---------------------------------------------------------------------------

@router.post("/queue_exit")
async def queue_exit(
    QueueResult: str = Form(""),
    TaskSid: str = Form(""),
    QueueSid: str = Form(""),
    CallSid: str = Form(""),
    From: str = Form(""),
) -> PlainTextResponse:
    """
    Twilio's <Enqueue action="..."> calls this with QueueResult describing how
    the call left the queue. We respond with TwiML that controls what the caller
    hears next.

    QueueResult values we care about:
      - "bridged"    -> the agent answered; let Twilio bridge silently
      - "leave"/""   -> caller hung up OR our cancel timer fired -> offer callback
      - "error"      -> something went wrong upstream; apologize and hang up
    """
    logger.info("queue_exit call=%s task=%s result=%s", CallSid, TaskSid, QueueResult)

    if QueueResult == "bridged":
        # Nothing to say; Twilio is already bridging the agent leg.
        return PlainTextResponse(
            content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            media_type="application/xml",
        )

    # Non-bridged exits get the callback offer. We've already (or will shortly)
    # written a pending_callbacks row from the task.canceled / no-agent branch.
    msg = (
        "Sorry about that — all of our specialists are on calls right now. "
        "I'll have someone call you back from this number within just a few "
        "minutes. Thanks so much for your patience!"
    )
    return PlainTextResponse(content=_twiml_say_and_hangup(msg), media_type="application/xml")


# ---------------------------------------------------------------------------
# /assignment — TaskRouter assignment callback
# ---------------------------------------------------------------------------

@router.post("/assignment")
async def assignment_callback(
    TaskSid: str = Form(""),
    ReservationSid: str = Form(""),
    WorkerSid: str = Form(""),
    WorkerAttributes: str = Form("{}"),
    TaskAttributes: str = Form("{}"),
) -> JSONResponse:
    """
    TaskRouter assigned a worker. Return a `dequeue` instruction so Twilio dials
    the worker's cell and bridges the queued call.

    We also stash the wrap-up activity SID so when the bridged call ends, the
    worker's activity is automatically set to Wrap-Up (not Available).
    """
    try:
        worker_attrs = json.loads(WorkerAttributes or "{}")
    except json.JSONDecodeError:
        worker_attrs = {}
    contact_uri = worker_attrs.get("contact_uri")
    if not contact_uri:
        logger.error("worker %s has no contact_uri; cannot bridge", WorkerSid)
        # Returning empty response cancels this reservation; TaskRouter will try the next worker.
        return JSONResponse({})

    agent_db.mark_assigned(task_sid=TaskSid, worker_sid=WorkerSid)
    logger.info(
        "assigning task=%s reservation=%s -> worker=%s (%s)",
        TaskSid, ReservationSid, WorkerSid, contact_uri,
    )

    return JSONResponse({
        "instruction": "dequeue",
        "to": contact_uri,
        "from": _queue_number(),
        "post_work_activity_sid": _wrapup_activity_sid(),
        # Time agent's cell rings before we mark the reservation timed-out
        # (workflow's task_reservation_timeout is the authoritative deadline).
        "timeout": 20,
        # If the agent rejects or doesn't answer, TaskRouter will re-issue
        # the reservation to the next eligible worker per the workflow.
    })


@router.post("/assignment_fallback")
async def assignment_fallback(
    TaskSid: str = Form(""),
    ReservationSid: str = Form(""),
    WorkerSid: str = Form(""),
    WorkerAttributes: str = Form("{}"),
) -> JSONResponse:
    """Last-resort handler if /assignment failed. Do the safest thing: dequeue plain."""
    logger.warning("assignment_fallback fired for task=%s worker=%s", TaskSid, WorkerSid)
    try:
        contact_uri = json.loads(WorkerAttributes or "{}").get("contact_uri", "")
    except json.JSONDecodeError:
        contact_uri = ""
    if not contact_uri:
        return JSONResponse({})
    return JSONResponse({
        "instruction": "dequeue",
        "to": contact_uri,
        "from": _queue_number(),
    })


# ---------------------------------------------------------------------------
# /event — Workspace event callback (mirrors TaskRouter state into routing_log)
# ---------------------------------------------------------------------------

# Map TaskRouter event types -> what we do with them.
# https://www.twilio.com/docs/taskrouter/api/event/reference
_INTERESTING_EVENTS = {
    "task.created",
    "task.canceled",
    "task.completed",
    "task.wrapup",
    "task.deleted",
    "reservation.accepted",
    "reservation.rejected",
    "reservation.timeout",
    "reservation.canceled",
    "worker.activity.update",
}


# ---------------------------------------------------------------------------
# Programmatic task-cancel timer
# ---------------------------------------------------------------------------

async def _cancel_task_after_timeout(task_sid: str) -> None:
    """
    Wait NO_AGENT_TIMEOUT_SECONDS. If the task is still pending then, cancel it.
    The resulting task.canceled event causes our handler to write a callback row.
    """
    try:
        await asyncio.sleep(NO_AGENT_TIMEOUT_SECONDS)
        # Lazy import — avoids a cold-import cost on every dashboard reload.
        from twilio.base.exceptions import TwilioRestException

        workspace_sid = os.getenv("TASKROUTER_WORKSPACE_SID", "")
        if not workspace_sid:
            logger.warning("no workspace sid; cannot cancel task %s", task_sid)
            return
        try:
            _twilio_client().taskrouter.v1.workspaces(workspace_sid).tasks(task_sid).update(
                assignment_status="canceled",
                reason="no_agent_available",
            )
            logger.info("canceled queued task %s after %.0fs timeout", task_sid, NO_AGENT_TIMEOUT_SECONDS)
        except TwilioRestException as e:
            # If the task was already accepted/completed concurrently with our
            # cancel, Twilio returns 4xx. That's fine — race resolved in our favor.
            logger.info("task %s could not be canceled (likely already terminal): %s", task_sid, e)
    except asyncio.CancelledError:
        # An accept beat us to the punch; that's the happy path.
        pass
    finally:
        _pending_cancels.pop(task_sid, None)


def _schedule_cancel(task_sid: str) -> None:
    """Start (or replace) the no-agent timer for a freshly-created task."""
    if not task_sid:
        return
    existing = _pending_cancels.pop(task_sid, None)
    if existing and not existing.done():
        existing.cancel()
    _pending_cancels[task_sid] = asyncio.create_task(_cancel_task_after_timeout(task_sid))


def _clear_cancel(task_sid: str) -> None:
    """Stop the no-agent timer once the task reaches a terminal/accepted state."""
    t = _pending_cancels.pop(task_sid, None)
    if t and not t.done():
        t.cancel()


@router.get("/log")
async def routing_log_endpoint(limit: int = 50) -> list[dict[str, Any]]:
    """Recent routing decisions, freshest first. Drives the admin /agents page."""
    limit = max(1, min(limit, 500))
    return agent_db.routing_history(limit=limit)


# ---------------------------------------------------------------------------
# Pending callbacks (Phase 3)
# ---------------------------------------------------------------------------

@router.get("/callbacks")
async def list_callbacks(
    status: str = "pending",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List callbacks. status='all' to drop the status filter."""
    limit = max(1, min(limit, 500))
    return agent_db.list_pending_callbacks(
        limit=limit, status=None if status == "all" else status
    )


@router.post("/callbacks")
async def add_callback(request: Request) -> JSONResponse:
    """
    Add a callback row. Called by Emma's schedule_callback tool when the prospect
    asks to be called back, or manually by managers.

    Required: lead_phone
    Optional: first_name, required_state, prefer_agent_sid, requested_at_local,
              scheduled_for (unix ts), source, reason
    """
    body: dict[str, Any] = await request.json()
    if not body.get("lead_phone"):
        raise HTTPException(400, "missing field: lead_phone")
    cb_id = agent_db.add_pending_callback(
        lead_phone=body["lead_phone"],
        first_name=body.get("first_name"),
        required_state=body.get("required_state"),
        prefer_agent_sid=body.get("prefer_agent_sid"),
        source=body.get("source", "prospect_requested"),
        reason=body.get("reason"),
        requested_at_local=body.get("requested_at_local"),
        scheduled_for=body.get("scheduled_for"),
    )
    logger.info("added pending callback #%d for %s", cb_id, body["lead_phone"])
    return JSONResponse({"id": cb_id})


@router.post("/callbacks/claim")
async def claim_callbacks(request: Request) -> list[dict[str, Any]]:
    """
    Atomically claim up to `limit` pending callbacks that are due now. The
    dialer calls this, places the calls, and reports back via the completed /
    gave_up endpoints.

    Body: {"limit": 50}
    """
    body = await request.json() if (await request.body()) else {}
    limit = int(body.get("limit", 25))
    import time as _time
    return agent_db.claim_due_callbacks(now_ts=_time.time(), limit=max(1, min(limit, 100)))


@router.put("/callbacks/{callback_id}/completed")
async def callback_completed(callback_id: int) -> dict[str, str]:
    agent_db.mark_callback_completed(callback_id)
    return {"status": "completed"}


@router.put("/callbacks/{callback_id}/gave_up")
async def callback_gave_up(callback_id: int, payload: dict[str, Any] | None = None) -> dict[str, str]:
    reason = (payload or {}).get("reason")
    agent_db.mark_callback_gave_up(callback_id, reason=reason)
    return {"status": "gave_up"}


@router.post("/event")
async def workspace_event(request: Request) -> PlainTextResponse:
    """
    Mirror selected workspace events into our local routing_log + agents.activity.
    Twilio doesn't require a response body; 200 OK is enough.
    """
    form = await request.form()

    def _f(key: str) -> str:
        # form.get can return UploadFile for multipart payloads; TaskRouter sends
        # plain form-urlencoded so values are always strings, but mypy doesn't know.
        v = form.get(key, "")
        return v if isinstance(v, str) else ""

    event_type = _f("EventType")
    if event_type not in _INTERESTING_EVENTS:
        return PlainTextResponse("ok")

    task_sid = _f("TaskSid")
    worker_sid = _f("WorkerSid")

    try:
        if event_type == "task.created" and task_sid:
            _schedule_cancel(task_sid)
        elif event_type == "reservation.accepted" and task_sid:
            agent_db.mark_accepted(task_sid)
            _clear_cancel(task_sid)
        elif event_type in ("task.completed", "task.canceled", "task.deleted") and task_sid:
            outcome = {
                "task.completed": "bridged",
                "task.canceled": "no_agent",   # may be overridden below if it was caller-abandon
                "task.deleted": "failed",
            }[event_type]
            cancel_reason = _f("TaskCanceledReason") or _f("Reason")
            if event_type == "task.canceled" and cancel_reason and cancel_reason != "no_agent_available":
                # Cancel originated from outside our timer — caller hung up,
                # supervisor canceled, etc.
                outcome = "abandoned"
            agent_db.mark_completed(task_sid, outcome=outcome)
            _clear_cancel(task_sid)
            # On a no_agent cancel, queue a callback so the prospect gets called back.
            if event_type == "task.canceled" and outcome == "no_agent":
                routing = agent_db.get_routing_by_task(task_sid)
                if routing:
                    agent_db.add_pending_callback(
                        lead_phone=routing["lead_phone"],
                        first_name=routing.get("first_name"),
                        required_state=routing.get("required_state"),
                        prefer_agent_sid=routing.get("prefer_agent_sid"),
                        source="no_agent_timeout",
                        reason=f"queue timed out after {NO_AGENT_TIMEOUT_SECONDS:.0f}s",
                        related_routing_id=routing["id"],
                    )
                    logger.info("queued no-agent callback for %s", routing["lead_phone"])
        elif event_type == "worker.activity.update" and worker_sid:
            # Reflect Twilio-side activity changes back into our DB.
            # `WorkerActivityName` is the new activity friendly_name.
            new_activity = _f("WorkerActivityName")
            agent = agent_db.get_agent_by_worker_sid(worker_sid)
            if agent and new_activity in agent_db.Activity.ALL:
                agent_db.set_activity(
                    agent["id"], new_activity, reason="twilio_event"
                )
                # Kick off the wrap-up auto-flip timer if Twilio just set the
                # worker to Wrap-Up post-call. Local import avoids circular
                # imports between transfer_queue + agent_state.
                if new_activity == agent_db.Activity.WRAP_UP:
                    from agent_state import on_wrapup_started
                    on_wrapup_started(agent["id"])
    except Exception as e:
        # Don't 500 back to Twilio — they'll retry forever. Log and move on.
        logger.exception("event handler error for %s: %s", event_type, e)

    return PlainTextResponse("ok")
