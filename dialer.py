"""
Excel dialer: read contacts from .xlsx, normalize phones, scrub, dispatch LiveKit jobs.

Usage:
  python dialer.py leads.xlsx                    # dial all rows (with scrubbing)
  python dialer.py leads.xlsx --limit 50         # first 50
  python dialer.py leads.xlsx --concurrent 3     # 3 calls in flight
  python dialer.py leads.xlsx --dry-run          # just print what it would do
  python dialer.py leads.xlsx --no-scrub         # skip scrubbing pipeline
  python dialer.py leads.xlsx --scrub-only       # scrub and report, don't dial

Expected columns (case-insensitive, any order):
  first_name (or name, fname)      REQUIRED
  phone (or phone_number, number)  REQUIRED
  zip (or zip_code, postal)        optional
  dob (or date_of_birth, birthday) optional
  email                            optional
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from livekit import api

load_dotenv(dotenv_path=".env.local")

AGENT_NAME = "emma-health"

COLUMN_ALIASES = {
    "first_name": {"first_name", "firstname", "fname", "name", "first"},
    "phone": {"phone", "phone_number", "number", "phonenumber", "mobile", "cell"},
    "zip": {"zip", "zip_code", "zipcode", "postal", "postal_code"},
    "dob": {"dob", "date_of_birth", "birthday", "birthdate", "dateofbirth"},
    "email": {"email", "email_address", "e_mail"},
    "state": {"state", "st"},
}


def _canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    lower = {c: c.lower().strip().replace(" ", "_") for c in df.columns}
    df = df.rename(columns=lower)
    mapping: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for col in df.columns:
            if col in aliases:
                mapping[col] = canonical
                break
    return df.rename(columns=mapping)


def normalize_phone(raw: str | int | float) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = re.sub(r"\D", "", str(raw))
    if not s:
        return None
    if len(s) == 10:
        return "+1" + s
    if len(s) == 11 and s.startswith("1"):
        return "+" + s
    if len(s) > 11 and str(raw).startswith("+"):
        return "+" + s
    return None


def _pick_sheet(path: Path) -> str:
    """Pick the best sheet: prefer 'Sheet1' / largest sheet with a phone-like column."""
    xl = pd.ExcelFile(path)
    candidates = []
    for name in xl.sheet_names:
        try:
            head = pd.read_excel(path, sheet_name=name, nrows=1)
            cols = {c.lower().strip().replace(" ", "_") for c in head.columns}
            has_phone = any(c in cols for c in COLUMN_ALIASES["phone"])
            n_rows = xl.book[name].max_row if hasattr(xl, "book") else len(pd.read_excel(path, sheet_name=name))
            candidates.append((has_phone, n_rows, name))
        except Exception:
            continue
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2] if candidates else xl.sheet_names[0]


def _clean_numeric_string(v: Any) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    return s or None


def load_contacts(path: Path) -> list[dict]:
    if path.suffix.lower() in (".xlsx", ".xls"):
        sheet = _pick_sheet(path)
        df = pd.read_excel(path, sheet_name=sheet, dtype={"ZIP": str, "zip": str, "Phone Number": str, "phone": str, "DOB": str, "dob": str}, engine="openpyxl")
        print(f"reading sheet '{sheet}' ({len(df)} rows)")
    else:
        df = pd.read_csv(path, dtype=str)
    df = _canonicalize_columns(df)
    if "phone" not in df.columns:
        raise ValueError(f"No phone column found. Columns: {list(df.columns)}")
    if "first_name" not in df.columns:
        df["first_name"] = "there"

    contacts = []
    for _, row in df.iterrows():
        phone = normalize_phone(row.get("phone"))
        if not phone:
            continue
        fn_raw = row.get("first_name")
        fn = "there" if (fn_raw is None or (isinstance(fn_raw, float) and pd.isna(fn_raw))) else str(fn_raw).strip().split()[0]
        fn = fn.title() if fn else "there"
        contact = {
            "first_name": fn,
            "phone_number": phone,
            "zip": (re.sub(r"\D", "", _clean_numeric_string(row.get("zip")) or "")[:5] or None) if "zip" in df.columns else None,
            "dob": _clean_numeric_string(row.get("dob")) if "dob" in df.columns else None,
            "email": str(row["email"]).strip().lower() if "email" in df.columns and pd.notna(row.get("email")) else None,
            "state": str(row["state"]).strip() if "state" in df.columns and pd.notna(row.get("state")) else None,
        }
        contacts.append(contact)
    return contacts


async def dispatch_call(lk: api.LiveKitAPI, contact: dict) -> str:
    call_id = f"call_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    metadata = {**contact, "call_id": call_id}
    room_name = f"emma-{call_id}"
    await lk.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=AGENT_NAME,
            room=room_name,
            metadata=json.dumps(metadata),
        )
    )
    return call_id


async def run(contacts: list[dict], concurrent: int, pause: float, dry_run: bool) -> None:
    if dry_run:
        for c in contacts:
            print(f"[DRY] would dial {c['phone_number']}  {c['first_name']}  zip={c.get('zip')}  dob={c.get('dob')}")
        return

    lk = api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"],
        api_key=os.environ["LIVEKIT_API_KEY"],
        api_secret=os.environ["LIVEKIT_API_SECRET"],
    )
    sem = asyncio.Semaphore(concurrent)

    async def _one(c: dict) -> None:
        async with sem:
            try:
                call_id = await dispatch_call(lk, c)
                print(f"dispatched {call_id} -> {c['phone_number']} ({c['first_name']})")
            except Exception as e:
                print(f"FAILED {c['phone_number']}: {e}")
            await asyncio.sleep(pause)

    await asyncio.gather(*[_one(c) for c in contacts])
    await lk.aclose()


async def scrub_and_run(contacts: list[dict], concurrent: int, pause: float, dry_run: bool, no_scrub: bool, scrub_only: bool) -> None:
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path, help=".xlsx or .csv contact list")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrent", type=int, default=2, help="concurrent calls (start low)")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between dispatches per worker")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-scrub", action="store_true", help="skip the scrubbing pipeline")
    ap.add_argument("--scrub-only", action="store_true", help="scrub and report, don't dial")
    args = ap.parse_args()

    contacts = load_contacts(args.file)
    if args.limit:
        contacts = contacts[: args.limit]
    print(f"loaded {len(contacts)} valid contacts from {args.file}")

    asyncio.run(scrub_and_run(contacts, args.concurrent, args.pause, args.dry_run, args.no_scrub, args.scrub_only))


if __name__ == "__main__":
    main()
