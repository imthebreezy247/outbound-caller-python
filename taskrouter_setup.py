"""
taskrouter_setup - one-time provisioning + idempotent sync for the Twilio TaskRouter
side of the queueing system.

Creates (or finds, if they already exist) the Workspace, Activities, TaskQueues,
and Workflow, then syncs every agent in agent_db.agents into a TaskRouter Worker.

Usage:

  # First time: prints SIDs to add to .env.local
  python taskrouter_setup.py init

  # After adding/removing/editing agents in the DB:
  python taskrouter_setup.py sync-workers

  # Sanity check what's currently provisioned:
  python taskrouter_setup.py print-config

  # Add an agent directly from the CLI (also creates the Worker):
  python taskrouter_setup.py add-agent \\
      --name "Maria Lopez" --email maria@agency.com \\
      --cell +12025551234 --states TX,FL,GA --languages en,es

Required env (in .env.local):
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  PUBLIC_BASE_URL              - your public https URL (ngrok in dev), no trailing slash.
                                  Used to register assignment + event callbacks.

Set after running `init`:
  TASKROUTER_WORKSPACE_SID
  TASKROUTER_WORKFLOW_SID
  TASKROUTER_QUEUE_DEFAULT_SID
  TASKROUTER_QUEUE_MANAGER_SID
  TASKROUTER_ACTIVITY_AVAILABLE_SID
  TASKROUTER_ACTIVITY_BUSY_SID
  TASKROUTER_ACTIVITY_WRAPUP_SID
  TASKROUTER_ACTIVITY_LUNCH_SID
  TASKROUTER_ACTIVITY_OFFLINE_SID

Phase 1 routing rules (will be extended in phase 2/3):
  1. Sticky-callback filter: if task.prefer_agent_sid is set, prefer that worker for 30s.
  2. Compliance filter: route compliance-flagged tasks to the manager queue.
  3. State-license filter: match task.required_state to worker.state_licenses.
  4. Default: route to the default queue, any worker.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from twilio.rest import Client

import agent_db

load_dotenv(dotenv_path=".env.local")

WORKSPACE_FRIENDLY_NAME = os.getenv("TASKROUTER_WORKSPACE_NAME", "Clairvo-Transfers")
WORKFLOW_FRIENDLY_NAME = "Clairvo Health Insurance Routing"
QUEUE_DEFAULT_NAME = "all_agents"
QUEUE_MANAGER_NAME = "manager_escalation"

# Temperature -> base TaskRouter priority. Higher = picked first when multiple
# tasks are queued. Compliance escalations beat hot leads because they're a
# legal/manager issue; hot beats warm because the prospect is ready now.
# When you change this map, re-run `python taskrouter_setup.py init` to push the
# new workflow JSON to Twilio.
TEMPERATURE_PRIORITIES = {
    "compliance": 12,
    "hot": 10,
    "callback": 8,    # scheduled callback (set by Phase 3 dispatcher)
    "warm": 5,
}
TEMPERATURE_VALUES = tuple(TEMPERATURE_PRIORITIES.keys())

# Activity friendly names — kept identical to agent_db.Activity so the strings
# round-trip between our DB and Twilio without translation.
ACT_AVAILABLE = agent_db.Activity.AVAILABLE
ACT_BUSY = agent_db.Activity.BUSY
ACT_WRAPUP = agent_db.Activity.WRAP_UP
ACT_LUNCH = agent_db.Activity.LUNCH
ACT_OFFLINE = agent_db.Activity.OFFLINE


# ---------------------------------------------------------------------------
# Client + context
# ---------------------------------------------------------------------------

@dataclass
class ProvisionedIds:
    workspace_sid: str
    workflow_sid: str
    queue_default_sid: str
    queue_manager_sid: str
    act_available_sid: str
    act_busy_sid: str
    act_wrapup_sid: str
    act_lunch_sid: str
    act_offline_sid: str


def _client() -> Client:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    tok = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not tok:
        raise SystemExit("Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env.local")
    return Client(sid, tok)


def _public_base_url() -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        raise SystemExit(
            "Set PUBLIC_BASE_URL in .env.local (e.g. an ngrok https URL pointing at "
            "your local dashboard on port 8080). The assignment callback must be "
            "reachable from Twilio."
        )
    return base


# ---------------------------------------------------------------------------
# Provisioning (idempotent)
# ---------------------------------------------------------------------------

def _get_or_create_workspace(client: Client) -> str:
    base = _public_base_url()
    event_url = f"{base}/api/transfer/event"
    for ws in client.taskrouter.v1.workspaces.list(friendly_name=WORKSPACE_FRIENDLY_NAME):
        # Keep event callback URL in sync if PUBLIC_BASE_URL changed.
        if ws.event_callback_url != event_url:
            client.taskrouter.v1.workspaces(ws.sid).update(event_callback_url=event_url)
        return ws.sid
    ws = client.taskrouter.v1.workspaces.create(
        friendly_name=WORKSPACE_FRIENDLY_NAME,
        event_callback_url=event_url,
        multi_task_enabled=False,  # one task at a time per worker, by design
    )
    return ws.sid


def _get_or_create_activity(client: Client, workspace_sid: str, name: str, available: bool) -> str:
    for act in client.taskrouter.v1.workspaces(workspace_sid).activities.list(friendly_name=name):
        return act.sid
    act = client.taskrouter.v1.workspaces(workspace_sid).activities.create(
        friendly_name=name, available=available
    )
    return act.sid


def _get_or_create_queue(client: Client, workspace_sid: str, name: str, target_expr: str) -> str:
    for q in client.taskrouter.v1.workspaces(workspace_sid).task_queues.list(friendly_name=name):
        # Sync the target expression in case we changed it.
        if q.target_workers != target_expr:
            client.taskrouter.v1.workspaces(workspace_sid).task_queues(q.sid).update(
                target_workers=target_expr
            )
        return q.sid
    q = client.taskrouter.v1.workspaces(workspace_sid).task_queues.create(
        friendly_name=name, target_workers=target_expr
    )
    return q.sid


def _workflow_config(queue_default_sid: str, queue_manager_sid: str) -> dict[str, Any]:
    """
    Phase 2 workflow with priority lanes. Filters are evaluated top-down; first
    match wins. Within a matched filter, targets are tried in order — a target
    falls through to the next when its `timeout` elapses without a reservation.

    Filters:
      1. compliance_escalation  -> manager queue, priority 12
      2. hot_lead               -> default queue, priority 10 (state-matched first, fallback any)
      3. sticky_callback        -> default queue, priority 8 (preferred worker, falls back to state-matched)
      4. warm_state_match       -> default queue, priority 5 (state-matched only)
      default                   -> default queue, priority 5

    Expression language: https://www.twilio.com/docs/taskrouter/workflows/expressions
    Worker attributes: state_licenses (array), languages (array), is_manager (bool)
    Task attributes:   required_state (str), temperature (str), prefer_agent_sid (str)
    """
    p = TEMPERATURE_PRIORITIES
    return {
        "task_routing": {
            "filters": [
                # 1. Compliance escalations go to managers regardless of state.
                {
                    "filter_friendly_name": "compliance_escalation",
                    "expression": "temperature == 'compliance'",
                    "targets": [
                        {
                            "queue": queue_manager_sid,
                            "expression": "worker.is_manager == true",
                            "priority": p["compliance"],
                        }
                    ],
                },
                # 2. Hot leads: try state-matched agent for 45s, then any agent.
                {
                    "filter_friendly_name": "hot_lead",
                    "expression": "temperature == 'hot'",
                    "targets": [
                        {
                            "queue": queue_default_sid,
                            "expression": (
                                "task.required_state == null "
                                "OR worker.state_licenses HAS task.required_state"
                            ),
                            "priority": p["hot"],
                            "timeout": 45,
                        },
                        {
                            # Fall through to any agent rather than abandon a hot lead.
                            "queue": queue_default_sid,
                            "priority": p["hot"],
                        },
                    ],
                },
                # 3. Sticky callback: prefer the originally assigned agent, then
                #    fall through to any state-matched agent at a lower priority.
                {
                    "filter_friendly_name": "sticky_callback",
                    "expression": "prefer_agent_sid != null",
                    "targets": [
                        {
                            "queue": queue_default_sid,
                            "expression": "worker.sid == task.prefer_agent_sid",
                            "priority": p["callback"],
                            "timeout": 30,
                        },
                        {
                            "queue": queue_default_sid,
                            "expression": (
                                "task.required_state == null "
                                "OR worker.state_licenses HAS task.required_state"
                            ),
                            # Demote slightly when falling through sticky so a hot
                            # lead that arrives during the fall-through still wins.
                            "priority": p["callback"] - 2,
                        },
                    ],
                },
                # 4. Warm + state required: only state-matched workers.
                {
                    "filter_friendly_name": "warm_state_match",
                    "expression": "required_state != null",
                    "targets": [
                        {
                            "queue": queue_default_sid,
                            "expression": "worker.state_licenses HAS task.required_state",
                            "priority": p["warm"],
                        }
                    ],
                },
            ],
            # Catch-all: any worker, warm priority. Used when required_state is null.
            "default_filter": {"queue": queue_default_sid, "priority": p["warm"]},
        }
    }


def _get_or_create_workflow(client: Client, workspace_sid: str, queue_default_sid: str, queue_manager_sid: str) -> str:
    base = _public_base_url()
    assignment_url = f"{base}/api/transfer/assignment"
    fallback_url = f"{base}/api/transfer/assignment_fallback"
    cfg_json = json.dumps(_workflow_config(queue_default_sid, queue_manager_sid))

    for wf in client.taskrouter.v1.workspaces(workspace_sid).workflows.list(
        friendly_name=WORKFLOW_FRIENDLY_NAME
    ):
        client.taskrouter.v1.workspaces(workspace_sid).workflows(wf.sid).update(
            configuration=cfg_json,
            assignment_callback_url=assignment_url,
            fallback_assignment_callback_url=fallback_url,
            task_reservation_timeout=20,  # ring the agent's cell up to 20s before reassigning
        )
        return wf.sid

    wf = client.taskrouter.v1.workspaces(workspace_sid).workflows.create(
        friendly_name=WORKFLOW_FRIENDLY_NAME,
        configuration=cfg_json,
        assignment_callback_url=assignment_url,
        fallback_assignment_callback_url=fallback_url,
        task_reservation_timeout=20,
    )
    return wf.sid


def provision_all() -> ProvisionedIds:
    """Idempotently create or update every TaskRouter resource we need."""
    client = _client()
    workspace_sid = _get_or_create_workspace(client)

    act_available = _get_or_create_activity(client, workspace_sid, ACT_AVAILABLE, available=True)
    act_busy = _get_or_create_activity(client, workspace_sid, ACT_BUSY, available=False)
    act_wrapup = _get_or_create_activity(client, workspace_sid, ACT_WRAPUP, available=False)
    act_lunch = _get_or_create_activity(client, workspace_sid, ACT_LUNCH, available=False)
    act_offline = _get_or_create_activity(client, workspace_sid, ACT_OFFLINE, available=False)

    # Queues target workers based on attribute expressions.
    queue_default_sid = _get_or_create_queue(
        client, workspace_sid, QUEUE_DEFAULT_NAME, target_expr="1==1"
    )
    queue_manager_sid = _get_or_create_queue(
        client, workspace_sid, QUEUE_MANAGER_NAME, target_expr="is_manager == true"
    )

    workflow_sid = _get_or_create_workflow(
        client, workspace_sid, queue_default_sid, queue_manager_sid
    )

    return ProvisionedIds(
        workspace_sid=workspace_sid,
        workflow_sid=workflow_sid,
        queue_default_sid=queue_default_sid,
        queue_manager_sid=queue_manager_sid,
        act_available_sid=act_available,
        act_busy_sid=act_busy,
        act_wrapup_sid=act_wrapup,
        act_lunch_sid=act_lunch,
        act_offline_sid=act_offline,
    )


# ---------------------------------------------------------------------------
# Worker sync
# ---------------------------------------------------------------------------

def _worker_attributes(agent: dict) -> str:
    """JSON-serialize the worker attributes we care about for the workflow."""
    return json.dumps(
        {
            "contact_uri": agent["cell_phone"],  # used as the dial target by Twilio
            "agent_id": agent["id"],
            "name": agent["name"],
            "email": agent.get("email"),
            "state_licenses": agent["state_licenses"],
            "languages": agent["languages"],
            "is_manager": agent["is_manager"],
        }
    )


def sync_workers(workspace_sid: str, offline_activity_sid: str) -> dict[str, str]:
    """
    For each active agent in agent_db, create or update a matching TaskRouter Worker.
    Returns {agent_id_str: worker_sid}.
    """
    client = _client()
    out: dict[str, str] = {}
    for agent in agent_db.list_agents(active_only=True):
        attrs = _worker_attributes(agent)
        existing_sid = agent.get("worker_sid")
        if existing_sid:
            try:
                client.taskrouter.v1.workspaces(workspace_sid).workers(existing_sid).update(
                    friendly_name=agent["name"],
                    attributes=attrs,
                )
                out[str(agent["id"])] = existing_sid
                continue
            except Exception as e:
                print(f"  ! worker {existing_sid} for {agent['name']} not found upstream ({e}); recreating")
        worker = client.taskrouter.v1.workspaces(workspace_sid).workers.create(
            friendly_name=agent["name"],
            attributes=attrs,
            activity_sid=offline_activity_sid,  # always start Offline; agents toggle to Available
        )
        agent_db.attach_worker_sid(agent["id"], worker.sid)
        out[str(agent["id"])] = worker.sid
        print(f"  + created worker {worker.sid} for {agent['name']}")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_env_block(ids: ProvisionedIds) -> None:
    print("\n# === Add these to .env.local ===")
    print(f"TASKROUTER_WORKSPACE_SID={ids.workspace_sid}")
    print(f"TASKROUTER_WORKFLOW_SID={ids.workflow_sid}")
    print(f"TASKROUTER_QUEUE_DEFAULT_SID={ids.queue_default_sid}")
    print(f"TASKROUTER_QUEUE_MANAGER_SID={ids.queue_manager_sid}")
    print(f"TASKROUTER_ACTIVITY_AVAILABLE_SID={ids.act_available_sid}")
    print(f"TASKROUTER_ACTIVITY_BUSY_SID={ids.act_busy_sid}")
    print(f"TASKROUTER_ACTIVITY_WRAPUP_SID={ids.act_wrapup_sid}")
    print(f"TASKROUTER_ACTIVITY_LUNCH_SID={ids.act_lunch_sid}")
    print(f"TASKROUTER_ACTIVITY_OFFLINE_SID={ids.act_offline_sid}")


def cmd_init(_args: argparse.Namespace) -> None:
    ids = provision_all()
    print("provisioning complete:")
    print(f"  workspace      {ids.workspace_sid}")
    print(f"  workflow       {ids.workflow_sid}")
    print(f"  queue/default  {ids.queue_default_sid}")
    print(f"  queue/manager  {ids.queue_manager_sid}")
    for name, sid in [
        ("Available", ids.act_available_sid),
        ("Busy", ids.act_busy_sid),
        ("Wrap-Up", ids.act_wrapup_sid),
        ("Lunch", ids.act_lunch_sid),
        ("Offline", ids.act_offline_sid),
    ]:
        print(f"  activity/{name:9s} {sid}")
    sync_workers(ids.workspace_sid, ids.act_offline_sid)
    _print_env_block(ids)


def cmd_sync_workers(_args: argparse.Namespace) -> None:
    workspace_sid = os.getenv("TASKROUTER_WORKSPACE_SID")
    offline_sid = os.getenv("TASKROUTER_ACTIVITY_OFFLINE_SID")
    if not workspace_sid or not offline_sid:
        raise SystemExit("Run `init` first, then put the printed SIDs into .env.local")
    sync_workers(workspace_sid, offline_sid)


def cmd_print_config(_args: argparse.Namespace) -> None:
    workspace_sid = os.getenv("TASKROUTER_WORKSPACE_SID")
    if not workspace_sid:
        raise SystemExit("TASKROUTER_WORKSPACE_SID not set; run `init` first")
    client = _client()
    ws = client.taskrouter.v1.workspaces(workspace_sid).fetch()
    print(f"Workspace: {ws.friendly_name} ({ws.sid})")
    print(f"  event_callback_url: {ws.event_callback_url}")
    print("\nActivities:")
    for a in client.taskrouter.v1.workspaces(workspace_sid).activities.list():
        print(f"  - {a.friendly_name:12s} {a.sid}  available={a.available}")
    print("\nTaskQueues:")
    for q in client.taskrouter.v1.workspaces(workspace_sid).task_queues.list():
        print(f"  - {q.friendly_name:24s} {q.sid}")
        print(f"      target_workers: {q.target_workers}")
    print("\nWorkflows:")
    for wf in client.taskrouter.v1.workspaces(workspace_sid).workflows.list():
        print(f"  - {wf.friendly_name}  {wf.sid}")
        print(f"      assignment_callback: {wf.assignment_callback_url}")
    print("\nWorkers (from Twilio):")
    for w in client.taskrouter.v1.workspaces(workspace_sid).workers.list():
        attrs = json.loads(w.attributes or "{}")
        print(
            f"  - {w.friendly_name:24s} {w.sid}  "
            f"activity={w.activity_name}  "
            f"states={attrs.get('state_licenses')}  "
            f"contact={attrs.get('contact_uri')}"
        )


def cmd_add_agent(args: argparse.Namespace) -> None:
    workspace_sid = os.getenv("TASKROUTER_WORKSPACE_SID")
    offline_sid = os.getenv("TASKROUTER_ACTIVITY_OFFLINE_SID")
    if not workspace_sid or not offline_sid:
        raise SystemExit("Run `init` first")
    states = [s.strip().upper() for s in (args.states or "").split(",") if s.strip()]
    langs = [s.strip().lower() for s in (args.languages or "en").split(",") if s.strip()]
    agent_id = agent_db.register_agent(
        name=args.name,
        cell_phone=args.cell,
        email=args.email,
        state_licenses=states,
        languages=langs,
        is_manager=args.manager,
    )
    print(f"registered agent_id={agent_id} ({args.name})")
    sync_workers(workspace_sid, offline_sid)


def main() -> None:
    parser = argparse.ArgumentParser(description="Twilio TaskRouter provisioning")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="provision workspace+activities+queues+workflow, sync workers").set_defaults(func=cmd_init)
    sub.add_parser("sync-workers", help="re-sync agent_db.agents to TaskRouter Workers").set_defaults(func=cmd_sync_workers)
    sub.add_parser("print-config", help="dump current TaskRouter resources").set_defaults(func=cmd_print_config)
    add = sub.add_parser("add-agent", help="register a new agent and sync workers")
    add.add_argument("--name", required=True)
    add.add_argument("--cell", required=True, help="E.164, e.g. +12025551234")
    add.add_argument("--email", default=None)
    add.add_argument("--states", default="", help="comma-separated, e.g. TX,FL,GA")
    add.add_argument("--languages", default="en", help="comma-separated, e.g. en,es")
    add.add_argument("--manager", action="store_true", help="receives compliance-flag escalations")
    add.set_defaults(func=cmd_add_agent)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
