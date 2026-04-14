"""
Nightly learning job: reads recent call transcripts, asks GPT to extract patterns
that worked (led to transfer) vs flopped (led to rejection), writes learnings.md.
This file is injected into Emma's system prompt on the next worker restart.

Run manually:  python learnings.py
Run nightly:   0 3 * * *  cd /path && python learnings.py
"""
from __future__ import annotations

import os
import sqlite3
import textwrap
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(dotenv_path=".env.local")

DB = Path("calls.db")
OUT = Path("learnings.md")
MAX_TRANSCRIPTS_PER_BUCKET = 20
MAX_CHARS_PER_TRANSCRIPT = 3500


def _fetch_transcripts(outcome: str, limit: int) -> list[str]:
    if not DB.exists():
        return []
    with sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        calls = c.execute(
            "SELECT id, first_name FROM calls WHERE outcome=? ORDER BY started_at DESC LIMIT ?",
            (outcome, limit),
        ).fetchall()
        transcripts = []
        for call in calls:
            turns = c.execute(
                "SELECT role, text FROM turns WHERE call_id=? ORDER BY ts ASC", (call["id"],)
            ).fetchall()
            if not turns:
                continue
            convo = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in turns)
            transcripts.append(convo[:MAX_CHARS_PER_TRANSCRIPT])
        return transcripts


def build_learnings() -> str:
    won = _fetch_transcripts("transferred", MAX_TRANSCRIPTS_PER_BUCKET)
    lost = _fetch_transcripts("rejected", MAX_TRANSCRIPTS_PER_BUCKET)
    if not won and not lost:
        return ""

    prompt = textwrap.dedent(f"""
    You are a sales coach analyzing outbound health-insurance cold calls by an agent named Emma.
    Goal: transfer prospects to a licensed agent (Chris) after collecting ZIP and DOB.

    Below are {len(won)} TRANSFERRED calls (wins) and {len(lost)} REJECTED calls (losses).
    Extract 5-10 concrete, actionable lessons Emma should apply on future calls. Focus on:
    - Opening moves that worked vs backfired
    - Rebuttals that got past the first objection
    - Tone/word choices correlated with success
    - Common failure patterns to avoid

    Format as terse bullet points, max 12 words each, no fluff. Start each bullet with a verb.

    === WINS ===
    {chr(10).join(f'--- WIN {i+1} ---' + chr(10) + t for i, t in enumerate(won))}

    === LOSSES ===
    {chr(10).join(f'--- LOSS {i+1} ---' + chr(10) + t for i, t in enumerate(lost))}
    """).strip()

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()


def main() -> None:
    lessons = build_learnings()
    if not lessons:
        print("no calls yet, skipping")
        return
    OUT.write_text(lessons, encoding="utf-8")
    print(f"wrote {len(lessons)} chars -> {OUT}")


if __name__ == "__main__":
    main()
