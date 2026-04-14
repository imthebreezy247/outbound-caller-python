"""
TranscriptLogger - per-call SQLite persistence with streaming turn appends.
Schema:
  calls(id, phone, first_name, started_at, ended_at, outcome, zip, dob, duration_s)
  turns(id, call_id, role, text, ts)
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DB_PATH = Path("calls.db")
_LOCK = threading.Lock()


def _init() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS calls (
          id TEXT PRIMARY KEY,
          phone TEXT,
          first_name TEXT,
          started_at REAL,
          ended_at REAL,
          outcome TEXT,
          zip TEXT,
          dob TEXT,
          duration_s REAL,
          meta TEXT
        );
        CREATE TABLE IF NOT EXISTS turns (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          call_id TEXT,
          role TEXT,
          text TEXT,
          ts REAL,
          FOREIGN KEY (call_id) REFERENCES calls(id)
        );
        CREATE INDEX IF NOT EXISTS idx_turns_call ON turns(call_id);
        CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_calls_outcome ON calls(outcome);
        """)


_init()


class TranscriptLogger:
    def __init__(self, call_id: str, phone: str = "", first_name: str = "", meta: dict | None = None):
        self.call_id = call_id
        self.started = time.time()
        with _LOCK, sqlite3.connect(DB_PATH) as c:
            c.execute(
                "INSERT OR REPLACE INTO calls(id, phone, first_name, started_at, outcome, meta) VALUES (?,?,?,?,?,?)",
                (call_id, phone, first_name, self.started, "in_progress", json.dumps(meta or {})),
            )

    def log_turn(self, role: str, text: str) -> None:
        if not text:
            return
        with _LOCK, sqlite3.connect(DB_PATH) as c:
            c.execute(
                "INSERT INTO turns(call_id, role, text, ts) VALUES (?,?,?,?)",
                (self.call_id, role, text, time.time()),
            )

    def set_field(self, field: str, value: Any) -> None:
        if field not in ("zip", "dob", "first_name", "phone", "outcome"):
            return
        with _LOCK, sqlite3.connect(DB_PATH) as c:
            c.execute(f"UPDATE calls SET {field}=? WHERE id=?", (value, self.call_id))

    def finalize(self, outcome: str, zip_code: str | None = None, dob: str | None = None) -> None:
        ended = time.time()
        with _LOCK, sqlite3.connect(DB_PATH) as c:
            c.execute(
                "UPDATE calls SET ended_at=?, outcome=?, duration_s=?, zip=COALESCE(?, zip), dob=COALESCE(?, dob) WHERE id=?",
                (ended, outcome, ended - self.started, zip_code, dob, self.call_id),
            )


# ---- Read helpers for dashboard ----

def list_calls(limit: int = 200) -> list[dict]:
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_call(call_id: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
        if not row:
            return None
        turns = c.execute(
            "SELECT role, text, ts FROM turns WHERE call_id=? ORDER BY ts ASC", (call_id,)
        ).fetchall()
        d = dict(row)
        d["turns"] = [dict(t) for t in turns]
        return d


def stats(since_hours: float = 24) -> dict:
    cutoff = time.time() - since_hours * 3600
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            "SELECT outcome, COUNT(*) FROM calls WHERE started_at >= ? GROUP BY outcome", (cutoff,)
        ).fetchall()
        total = sum(n for _, n in rows)
        by = {o or "unknown": n for o, n in rows}
        transferred = by.get("transferred", 0)
        return {
            "window_hours": since_hours,
            "total": total,
            "transferred": transferred,
            "rejected": by.get("rejected", 0),
            "voicemail": by.get("voicemail", 0),
            "dnc": by.get("dnc", 0),
            "conversion_pct": round(100 * transferred / total, 1) if total else 0,
            "by_outcome": by,
        }
