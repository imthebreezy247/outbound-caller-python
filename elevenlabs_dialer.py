"""
ElevenLabs Conversational AI dialer: read contacts from .xlsx, scrub, place calls
via ElevenLabs + Twilio.

This replaces dialer.py's LiveKit dispatch with the ElevenLabs outbound call API.
Your Twilio number is registered in the ElevenLabs dashboard; ElevenLabs handles
the SIP/TTS/STT pipeline and uses your Twilio trunk for caller ID.

Usage:
  python elevenlabs_dialer.py leads.xlsx                    # dial all rows
  python elevenlabs_dialer.py leads.xlsx --limit 50         # first 50
  python elevenlabs_dialer.py leads.xlsx --concurrent 3     # 3 calls in flight
  python elevenlabs_dialer.py leads.xlsx --dry-run          # print what it would do
  python elevenlabs_dialer.py leads.xlsx --no-scrub         # skip scrubbing
  python elevenlabs_dialer.py leads.xlsx --scrub-only       # scrub and report

Required env vars (in .env.local):
  ELEVENLABS_API_KEY       - your ElevenLabs API key
  ELEVENLABS_AGENT_ID      - agent ID from the ElevenLabs dashboard
  ELEVENLABS_PHONE_NUMBER_ID - phone number ID (from ElevenLabs Phone Numbers tab)

Optional env vars:
  TWILIO_PHONE_NUMBER      - used only for logging / display
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

from dialer import load_contacts, normalize_phone

load_dotenv(dotenv_path=".env.local")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")
ELEVENLABS_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_PHONE_NUMBER_ID", "")
ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"


async def dispatch_call(client: httpx.AsyncClient, contact: dict) -> dict:
    """Place a single outbound call via ElevenLabs Twilio integration."""
    payload = {
        "agent_id": ELEVENLABS_AGENT_ID,
        "agent_phone_number_id": ELEVENLABS_PHONE_NUMBER_ID,
        "to_number": contact["phone_number"],
        "conversation_initiation_client_data": {
            "dynamic_variables": {
                "name": contact.get("first_name", "there"),
                "zip": contact.get("zip") or "",
                "dob": contact.get("dob") or "",
                "state": contact.get("state") or "",
            }
        },
    }
    resp = await client.post(
        f"{ELEVENLABS_API_BASE}/convai/twilio/outbound_call",
        json=payload,
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


async def run(contacts: list[dict], concurrent: int, pause: float, dry_run: bool) -> None:
    if dry_run:
        for c in contacts:
            print(f"[DRY] would dial {c['phone_number']}  {c['first_name']}  zip={c.get('zip')}  dob={c.get('dob')}")
        return

    sem = asyncio.Semaphore(concurrent)
    async with httpx.AsyncClient() as client:
        async def _one(c: dict) -> None:
            async with sem:
                try:
                    result = await dispatch_call(client, c)
                    call_sid = result.get("call_sid", result.get("callSid", "?"))
                    print(f"[OK] {c['phone_number']}  {c['first_name']}  call_sid={call_sid}")
                except httpx.HTTPStatusError as e:
                    print(f"[FAIL] {c['phone_number']}: {e.response.status_code} {e.response.text[:200]}")
                except Exception as e:
                    print(f"[FAIL] {c['phone_number']}: {e}")
                await asyncio.sleep(pause)

        await asyncio.gather(*[_one(c) for c in contacts])


async def scrub_and_run(
    contacts: list[dict],
    concurrent: int,
    pause: float,
    dry_run: bool,
    no_scrub: bool,
    scrub_only: bool,
) -> None:
    if not no_scrub:
        from scrubber import scrub_contacts_full
        print(f"scrubbing {len(contacts)} contacts...")
        contacts, stats = await scrub_contacts_full(contacts, skip_previously_dialed=True)
        print(stats.summary())
        if scrub_only:
            print("\n--scrub-only mode: not dialing.")
            for c in contacts[:20]:
                print(f"  [OK] {c['phone_number']}  {c['first_name']}  zip={c.get('zip')}")
            if len(contacts) > 20:
                print(f"  ... and {len(contacts) - 20} more")
            return
    await run(contacts, concurrent, pause, dry_run)


def _preflight() -> None:
    missing = []
    if not ELEVENLABS_API_KEY:
        missing.append("ELEVENLABS_API_KEY")
    if not ELEVENLABS_AGENT_ID:
        missing.append("ELEVENLABS_AGENT_ID")
    if not ELEVENLABS_PHONE_NUMBER_ID:
        missing.append("ELEVENLABS_PHONE_NUMBER_ID")
    if missing:
        raise SystemExit(f"Missing env vars: {', '.join(missing)}. Add them to .env.local")


def main() -> None:
    ap = argparse.ArgumentParser(description="ElevenLabs outbound dialer")
    ap.add_argument("file", type=Path, help=".xlsx or .csv contact list")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrent", type=int, default=2, help="concurrent calls (start low)")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between dispatches per worker")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-scrub", action="store_true", help="skip the scrubbing pipeline")
    ap.add_argument("--scrub-only", action="store_true", help="scrub and report, don't dial")
    args = ap.parse_args()

    _preflight()

    contacts = load_contacts(args.file)
    if args.limit:
        contacts = contacts[: args.limit]
    print(f"loaded {len(contacts)} valid contacts from {args.file}")
    print(f"agent: {ELEVENLABS_AGENT_ID}  phone: {ELEVENLABS_PHONE_NUMBER_ID}")

    asyncio.run(scrub_and_run(contacts, args.concurrent, args.pause, args.dry_run, args.no_scrub, args.scrub_only))


if __name__ == "__main__":
    main()
