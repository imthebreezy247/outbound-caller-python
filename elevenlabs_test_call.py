"""
Single-call test for the ElevenLabs agent. Calls YOUR number so you can hear
the agent without touching real leads.

Usage:
  python elevenlabs_test_call.py                              # default: call 941-949-7026
  python elevenlabs_test_call.py --to 8454217593 --name Chris # ad-hoc number
  python elevenlabs_test_call.py --pick                       # interactive picker

Required env vars (in .env.local):
  ELEVENLABS_API_KEY
  ELEVENLABS_AGENT_ID
  ELEVENLABS_PHONE_NUMBER_ID
"""
from __future__ import annotations

import argparse
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.local")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")
ELEVENLABS_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_PHONE_NUMBER_ID", "")

DEFAULT_TEST_NUMBER = "+19419497026"
DEFAULT_TEST_NAME = "Chris"
PRESET_NUMBERS = {
    "1": (DEFAULT_TEST_NUMBER, DEFAULT_TEST_NAME),
    "2": ("+19413230041", "Chris"),
    "3": ("+19415180701", "Chris"),
    "4": ("+18454217593", "Chris"),
}


def normalize(raw: str) -> str:
    s = re.sub(r"\D", "", raw)
    if len(s) == 10:
        return "+1" + s
    if len(s) == 11 and s.startswith("1"):
        return "+" + s
    if raw.startswith("+"):
        return raw
    raise ValueError(f"unparseable phone: {raw}")


def fire(phone: str, first_name: str, zip_code: str | None = None, dob: str | None = None) -> None:
    payload = {
        "agent_id": ELEVENLABS_AGENT_ID,
        "agent_phone_number_id": ELEVENLABS_PHONE_NUMBER_ID,
        "to_number": phone,
        "conversation_initiation_client_data": {
            "dynamic_variables": {
                "name": first_name,
                "zip": zip_code or "",
                "dob": dob or "",
            }
        },
    }
    print(f"calling {phone} as {first_name} via ElevenLabs agent {ELEVENLABS_AGENT_ID}...")
    resp = httpx.post(
        "https://api.elevenlabs.io/v1/convai/twilio/outbound-call",
        json=payload,
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )
    if resp.status_code >= 400:
        print(f"[ERROR] {resp.status_code}: {resp.text}")
        return
    data = resp.json()
    print(f"[OK] call placed")
    print(f"     response: {data}")
    print(f"     check ElevenLabs dashboard -> Analysis -> Calls for the transcript")


def main() -> None:
    ap = argparse.ArgumentParser(description="ElevenLabs single test call")
    ap.add_argument("--to", help="phone number (any US format)")
    ap.add_argument("--name", default=DEFAULT_TEST_NAME)
    ap.add_argument("--zip", default=None)
    ap.add_argument("--dob", default=None)
    ap.add_argument("--pick", action="store_true")
    args = ap.parse_args()

    # Preflight
    missing = []
    if not ELEVENLABS_API_KEY:
        missing.append("ELEVENLABS_API_KEY")
    if not ELEVENLABS_AGENT_ID:
        missing.append("ELEVENLABS_AGENT_ID")
    if not ELEVENLABS_PHONE_NUMBER_ID:
        missing.append("ELEVENLABS_PHONE_NUMBER_ID")
    if missing:
        raise SystemExit(f"Missing: {', '.join(missing)} in .env.local")

    if args.to:
        phone = normalize(args.to)
        name = args.name
    elif args.pick:
        print("Pick a test number:")
        for k, (n, nm) in PRESET_NUMBERS.items():
            tag = "  <-- MAIN" if k == "1" else ""
            print(f"  [{k}] {n}  ({nm}){tag}")
        choice = input("> ").strip() or "1"
        phone, name = PRESET_NUMBERS.get(choice, (normalize(choice), args.name))
    else:
        phone, name = PRESET_NUMBERS["1"]
        print(f"[default] firing test call to {phone} ({name})")

    fire(phone, name, args.zip, args.dob)


if __name__ == "__main__":
    main()
