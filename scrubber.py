"""
Phone number scrubbing pipeline: landline removal (Twilio Lookup), DNC check,
internal DNC list, and duplicate removal.

Usage:
  from scrubber import scrub_contacts, ScrubStats

  contacts, stats = await scrub_contacts(contacts_list)
  # contacts is the cleaned list; stats is a ScrubStats dataclass
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.local")

logger = logging.getLogger("scrubber")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
DNC_API_KEY = os.getenv("DNC_API_KEY", "")  # e.g. DNCscrub.com API key
DNC_API_URL = os.getenv("DNC_API_URL", "https://api.dncscrub.com/v1/check")
SCRUB_DB_PATH = Path("scrub.db")
LOOKUP_CONCURRENCY = int(os.getenv("LOOKUP_CONCURRENCY", "10"))
ENABLE_LANDLINE_CHECK = os.getenv("ENABLE_LANDLINE_CHECK", "true").lower() == "true"
ENABLE_DNC_CHECK = os.getenv("ENABLE_DNC_CHECK", "true").lower() == "true"

_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Internal DNC + duplicate tracking DB
# ---------------------------------------------------------------------------

def _init_scrub_db() -> None:
    with sqlite3.connect(SCRUB_DB_PATH) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS internal_dnc (
            phone TEXT PRIMARY KEY,
            reason TEXT,
            added_at REAL
        );
        CREATE TABLE IF NOT EXISTS phone_history (
            phone TEXT PRIMARY KEY,
            line_type TEXT,
            is_dnc INTEGER DEFAULT 0,
            checked_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_dnc_phone ON internal_dnc(phone);
        CREATE INDEX IF NOT EXISTS idx_history_phone ON phone_history(phone);
        """)

_init_scrub_db()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class ScrubStats:
    total_input: int = 0
    duplicates_removed: int = 0
    landlines_removed: int = 0
    dnc_removed: int = 0
    internal_dnc_removed: int = 0
    invalid_removed: int = 0
    total_output: int = 0
    lookup_errors: int = 0
    cached_lookups: int = 0
    elapsed_s: float = 0.0

    def summary(self) -> str:
        return (
            f"Scrub results: {self.total_input} in -> {self.total_output} out | "
            f"dupes={self.duplicates_removed} landline={self.landlines_removed} "
            f"dnc={self.dnc_removed} internal_dnc={self.internal_dnc_removed} "
            f"invalid={self.invalid_removed} errors={self.lookup_errors} "
            f"cached={self.cached_lookups} ({self.elapsed_s:.1f}s)"
        )


# ---------------------------------------------------------------------------
# Internal DNC management
# ---------------------------------------------------------------------------

def add_to_internal_dnc(phone: str, reason: str = "requested_removal") -> None:
    """Add a number to the internal DNC list (called when prospect says 'stop calling')."""
    with _LOCK, sqlite3.connect(SCRUB_DB_PATH) as c:
        c.execute(
            "INSERT OR REPLACE INTO internal_dnc(phone, reason, added_at) VALUES (?,?,?)",
            (phone, reason, time.time()),
        )


def remove_from_internal_dnc(phone: str) -> None:
    with _LOCK, sqlite3.connect(SCRUB_DB_PATH) as c:
        c.execute("DELETE FROM internal_dnc WHERE phone=?", (phone,))


def is_internal_dnc(phone: str) -> bool:
    with sqlite3.connect(SCRUB_DB_PATH) as c:
        row = c.execute("SELECT 1 FROM internal_dnc WHERE phone=?", (phone,)).fetchone()
        return row is not None


def list_internal_dnc(limit: int = 500) -> list[dict]:
    with sqlite3.connect(SCRUB_DB_PATH) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT * FROM internal_dnc ORDER BY added_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def internal_dnc_count() -> int:
    with sqlite3.connect(SCRUB_DB_PATH) as c:
        return c.execute("SELECT COUNT(*) FROM internal_dnc").fetchone()[0]


# ---------------------------------------------------------------------------
# Phone history cache (avoid re-lookups)
# ---------------------------------------------------------------------------

CACHE_TTL_HOURS = float(os.getenv("SCRUB_CACHE_TTL_HOURS", "168"))  # 7 days default


def _get_cached(phone: str) -> dict | None:
    cutoff = time.time() - CACHE_TTL_HOURS * 3600
    with sqlite3.connect(SCRUB_DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM phone_history WHERE phone=? AND checked_at>=?",
            (phone, cutoff),
        ).fetchone()
        return dict(row) if row else None


def _set_cached(phone: str, line_type: str, is_dnc: bool) -> None:
    with _LOCK, sqlite3.connect(SCRUB_DB_PATH) as c:
        c.execute(
            "INSERT OR REPLACE INTO phone_history(phone, line_type, is_dnc, checked_at) VALUES (?,?,?,?)",
            (phone, line_type, int(is_dnc), time.time()),
        )


# ---------------------------------------------------------------------------
# Twilio Lookup (landline detection)
# ---------------------------------------------------------------------------

async def _twilio_lookup(session: aiohttp.ClientSession, phone: str) -> str | None:
    """Returns line type: 'mobile', 'landline', 'voip', 'unknown', or None on error."""
    if not TWILIO_SID or not TWILIO_TOKEN:
        return None
    url = f"https://lookups.twilio.com/v2/PhoneNumbers/{phone}?Fields=line_type_intelligence"
    try:
        async with session.get(
            url,
            auth=aiohttp.BasicAuth(TWILIO_SID, TWILIO_TOKEN),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                logger.warning(f"Twilio Lookup {phone}: HTTP {resp.status}")
                return None
            data = await resp.json()
            lti = data.get("line_type_intelligence", {})
            return lti.get("type", "unknown")
    except Exception as e:
        logger.warning(f"Twilio Lookup {phone}: {e}")
        return None


# ---------------------------------------------------------------------------
# DNC API check
# ---------------------------------------------------------------------------

async def _dnc_check(session: aiohttp.ClientSession, phone: str) -> bool | None:
    """Returns True if number is on the National DNC list. None on error."""
    if not DNC_API_KEY:
        return None
    # Generic DNC API integration — adjust URL/params for your specific provider
    # (DNCscrub.com, Gryphon, Contact Center Compliance, etc.)
    try:
        params = {"phone": phone, "api_key": DNC_API_KEY}
        async with session.get(
            DNC_API_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                logger.warning(f"DNC check {phone}: HTTP {resp.status}")
                return None
            data = await resp.json()
            # Adapt this to your DNC provider's response format
            return data.get("is_dnc", data.get("dnc", data.get("on_list", False)))
    except Exception as e:
        logger.warning(f"DNC check {phone}: {e}")
        return None


# ---------------------------------------------------------------------------
# Main scrubbing pipeline
# ---------------------------------------------------------------------------

async def scrub_contacts(
    contacts: list[dict],
    *,
    check_landline: bool | None = None,
    check_dnc: bool | None = None,
    check_internal_dnc: bool = True,
    remove_dupes: bool = True,
) -> tuple[list[dict], ScrubStats]:
    """
    Run the full scrubbing pipeline on a list of contacts.
    Each contact must have a 'phone_number' key in E.164 format.

    Returns (clean_contacts, stats).
    """
    start_time = time.time()
    stats = ScrubStats(total_input=len(contacts))

    if check_landline is None:
        check_landline = ENABLE_LANDLINE_CHECK
    if check_dnc is None:
        check_dnc = ENABLE_DNC_CHECK

    # Step 1: Remove duplicates (by phone_number)
    if remove_dupes:
        seen: set[str] = set()
        deduped: list[dict] = []
        for c in contacts:
            p = c.get("phone_number", "")
            if p in seen:
                stats.duplicates_removed += 1
                continue
            seen.add(p)
            deduped.append(c)
        contacts = deduped

    # Step 2: Remove internal DNC numbers
    if check_internal_dnc:
        clean: list[dict] = []
        for c in contacts:
            if is_internal_dnc(c["phone_number"]):
                stats.internal_dnc_removed += 1
                logger.info(f"internal DNC: {c['phone_number']}")
            else:
                clean.append(c)
        contacts = clean

    # Step 3: Landline check + DNC check (batched async)
    if (check_landline and TWILIO_SID) or (check_dnc and DNC_API_KEY):
        sem = asyncio.Semaphore(LOOKUP_CONCURRENCY)

        async def _check_one(session: aiohttp.ClientSession, contact: dict) -> dict | None:
            phone = contact["phone_number"]

            # Check cache first
            cached = _get_cached(phone)
            if cached is not None:
                stats.cached_lookups += 1
                if cached["line_type"] == "landline" and check_landline:
                    stats.landlines_removed += 1
                    return None
                if cached["is_dnc"] and check_dnc:
                    stats.dnc_removed += 1
                    return None
                return contact

            line_type = "unknown"
            on_dnc = False

            async with sem:
                # Twilio Lookup for line type
                if check_landline and TWILIO_SID:
                    lt = await _twilio_lookup(session, phone)
                    if lt is None:
                        stats.lookup_errors += 1
                    else:
                        line_type = lt

                # DNC API check
                if check_dnc and DNC_API_KEY:
                    dnc_result = await _dnc_check(session, phone)
                    if dnc_result is None:
                        stats.lookup_errors += 1
                    elif dnc_result:
                        on_dnc = True

            # Cache the result
            _set_cached(phone, line_type, on_dnc)

            # Filter
            if line_type == "landline" and check_landline:
                stats.landlines_removed += 1
                logger.info(f"landline removed: {phone}")
                return None
            if on_dnc and check_dnc:
                stats.dnc_removed += 1
                logger.info(f"DNC removed: {phone}")
                return None
            return contact

        async with aiohttp.ClientSession() as session:
            tasks = [_check_one(session, c) for c in contacts]
            results = await asyncio.gather(*tasks)
            contacts = [c for c in results if c is not None]

    stats.total_output = len(contacts)
    stats.elapsed_s = time.time() - start_time
    logger.info(stats.summary())
    return contacts, stats


# ---------------------------------------------------------------------------
# Bulk previously-dialed duplicate check
# ---------------------------------------------------------------------------

def get_previously_dialed_phones() -> set[str]:
    """Get all phone numbers that have been called before (from calls.db)."""
    calls_db = Path("calls.db")
    if not calls_db.exists():
        return set()
    with sqlite3.connect(calls_db) as c:
        rows = c.execute("SELECT DISTINCT phone FROM calls WHERE phone IS NOT NULL").fetchall()
        return {r[0] for r in rows}


async def scrub_contacts_full(
    contacts: list[dict],
    *,
    skip_previously_dialed: bool = False,
    **kwargs: Any,
) -> tuple[list[dict], ScrubStats]:
    """Extended scrub that also optionally removes previously-dialed numbers."""
    if skip_previously_dialed:
        dialed = get_previously_dialed_phones()
        before = len(contacts)
        contacts = [c for c in contacts if c["phone_number"] not in dialed]
        logger.info(f"previously dialed filter: {before} -> {len(contacts)} ({before - len(contacts)} removed)")

    return await scrub_contacts(contacts, **kwargs)
