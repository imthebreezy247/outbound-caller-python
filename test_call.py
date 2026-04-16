"""
Single-call test dialer. Calls YOUR number so you can pressure-test Emma
without waking up real leads at 10pm.

Usage:
  python test_call.py                         # MAIN test call -> 941-949-7026 (Chris)
  python test_call.py --pick                  # interactive picker (2/3 = backup numbers)
  python test_call.py --to 9413230041         # ad-hoc: call that number
  python test_call.py --to 9419497026 --name Chris
  python test_call.py --to 9419497026 --zip 34236 --dob 1990-05-15  # skip collection
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
import uuid

from dotenv import load_dotenv
from livekit import api

load_dotenv(dotenv_path=".env.local")

AGENT_NAME = "emma-health"
DEFAULT_TEST_NUMBER = "+19419497026"
DEFAULT_TEST_NAME = "Chris"
PRESET_NUMBERS = {
    "1": (DEFAULT_TEST_NUMBER, DEFAULT_TEST_NAME),  # MAIN test line
    "2": ("+19413230041", "Chris"),
    "3": ("+19415180701", "Chris"),
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


async def fire(phone: str, first_name: str, zip_code: str | None, dob: str | None) -> None:
    lk = api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"],
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    )
    call_id = f"test_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    metadata = {
        "phone_number": phone,
        "first_name": first_name,
        "zip": zip_code,
        "dob": dob,
        "call_id": call_id,
        "test_call": True,
    }
    room = f"emma-test-{call_id}"
    await lk.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=AGENT_NAME,
            room=room,
            metadata=json.dumps(metadata),
        )
    )
    print(f"[OK] dispatched {call_id}")
    print(f"     -> calling {phone} as {first_name}")
    print(f"     -> room {room}")
    print(f"     -> watch it live at http://localhost:8080")
    await lk.aclose()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", help="phone number (any US format). Omit to fire the MAIN test call.")
    ap.add_argument("--name", default=DEFAULT_TEST_NAME, help="first name Emma uses")
    ap.add_argument("--zip", dest="zip_code", default=None, help="pre-fill ZIP (skip asking)")
    ap.add_argument("--dob", default=None, help="pre-fill DOB (skip asking)")
    ap.add_argument("--pick", action="store_true", help="force interactive picker instead of main default")
    args = ap.parse_args()

    if args.to:
        phone = normalize(args.to)
        name = args.name
    elif args.pick:
        print("Pick a test number:")
        for k, (n, nm) in PRESET_NUMBERS.items():
            tag = "  <-- MAIN" if k == "1" else ""
            print(f"  [{k}] {n}  ({nm}){tag}")
        choice = input("> ").strip() or "1"
        if choice in PRESET_NUMBERS:
            phone, name = PRESET_NUMBERS[choice]
        else:
            phone = normalize(choice)
            name = args.name
    else:
        phone, name = PRESET_NUMBERS["1"]
        print(f"[default] firing MAIN test call to {phone} ({name})")
        print("          (use --pick to choose a different preset, or --to <number> for ad-hoc)")

    if not os.getenv("SIP_OUTBOUND_TRUNK_ID"):
        raise SystemExit("SIP_OUTBOUND_TRUNK_ID missing from .env.local")
    if not os.getenv("TRANSFER_TO_NUMBER"):
        print("WARN: TRANSFER_TO_NUMBER not set - transfer step will fail (fine for opening-line test)")

    asyncio.run(fire(phone, name, args.zip_code, args.dob))


if __name__ == "__main__":
    main()
