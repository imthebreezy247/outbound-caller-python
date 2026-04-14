"""
Single-call test dialer. Calls YOUR number so you can pressure-test Emma
without waking up real leads at 10pm.

Usage:
  python test_call.py                         # interactive picker
  python test_call.py --to 9413230041         # call that number
  python test_call.py --to 9415180701 --name Chris
  python test_call.py --to 9413230041 --zip 34236 --dob 1990-05-15  # skip collection
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
PRESET_NUMBERS = {
    "1": ("+19413230041", "Chris"),
    "2": ("+19415180701", "Chris"),
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
    ap.add_argument("--to", help="phone number (any US format)")
    ap.add_argument("--name", default="Chris", help="first name Emma uses")
    ap.add_argument("--zip", dest="zip_code", default=None, help="pre-fill ZIP (skip asking)")
    ap.add_argument("--dob", default=None, help="pre-fill DOB (skip asking)")
    args = ap.parse_args()

    if args.to:
        phone = normalize(args.to)
        name = args.name
    else:
        print("Pick a test number:")
        for k, (n, nm) in PRESET_NUMBERS.items():
            print(f"  [{k}] {n}  ({nm})")
        choice = input("> ").strip()
        if choice in PRESET_NUMBERS:
            phone, name = PRESET_NUMBERS[choice]
        else:
            phone = normalize(choice)
            name = args.name

    if not os.getenv("SIP_OUTBOUND_TRUNK_ID"):
        raise SystemExit("SIP_OUTBOUND_TRUNK_ID missing from .env.local")
    if not os.getenv("TRANSFER_TO_NUMBER"):
        print("WARN: TRANSFER_TO_NUMBER not set - transfer step will fail (fine for opening-line test)")

    asyncio.run(fire(phone, name, args.zip_code, args.dob))


if __name__ == "__main__":
    main()
