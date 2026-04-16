# Emma - Health Insurance Outbound Dialer

Female AI agent (Emma) calls leads from an Excel list, qualifies interest, collects
ZIP + DOB, and warm-transfers the live call to Chris's cell. Every call is transcribed
to SQLite, shown on a live dashboard, and fed back into a nightly learning job that
tunes the prompt automatically.

## Architecture

```
Excel -> dialer.py -> LiveKit agent dispatch -> Twilio SIP trunk -> Prospect phone
                                    |
                        Emma (agent.py, worker process)
                        Deepgram STT -> GPT-4o -> OpenAI TTS (gpt-4o-mini-tts, + ambience mix)
                                    |
                        TranscriptLogger (SQLite: calls.db)
                                    |
                        dashboard.py (FastAPI + SSE)   learnings.py (nightly)
```

## One-time setup

1. `pip install -r requirements.txt`
2. Copy `.env.example` -> `.env.local`, fill:
   - LiveKit creds (already set from your existing trunk)
   - `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`
   - `TRANSFER_TO_NUMBER` = your cell in E.164 (e.g. `+12025551234`)
3. Generate the placeholder ambience (or drop a real one at `assets/call_center_bg.wav`):
   ```
   python assets/generate_ambience.py
   ```
4. DC-number caller ID: verify your Twilio DC number is attached to the same SIP trunk
   referenced by `SIP_OUTBOUND_TRUNK_ID`. Prospects see that DC number; they never see
   your cell even after transfer.

## Running

Three processes (three terminals):

```
# 1. Agent worker (keeps running, waits for dispatches)
python agent.py dev

# 2. Dashboard UI at http://localhost:8080
uvicorn dashboard:app --host 0.0.0.0 --port 8080

# 3. Fire a batch from Excel (or use the dashboard's upload button)
python dialer.py leads.xlsx --concurrent 2
```

## Your current list: `alfano-chris-list-needs-scrub-04-13-26.xlsx`

- Sheet: `Sheet1` (auto-selected; "Claude Log" sheet ignored)
- Rows: 95,937 → **95,864 valid contacts** after phone normalization (73 rejected for invalid phone format)
- Top states: TX (19.4k), FL (13.3k), GA (6.4k), IL (5.5k), NC (4.7k)
- DOB present on 18,321 rows (Emma skips asking when available)
- **1,335 duplicate phone numbers** still present despite the scrub — add `--dedupe` if you want to drop them (or run a second pass through the same process that produced this file)
- **Scrub against the National DNC Registry** before dialing. 95k TCPA-unscrubbed dials is a ~$500/violation exposure.

Starter command (safe: 25 calls, 2 concurrent, dry-run first):
```
python dialer.py alfano-chris-list-needs-scrub-04-13-26.xlsx --limit 25 --concurrent 2 --dry-run
python dialer.py alfano-chris-list-needs-scrub-04-13-26.xlsx --limit 25 --concurrent 2
```

## Excel columns (case-insensitive)

| column       | required | notes                                    |
|--------------|----------|------------------------------------------|
| first_name   | yes      | Emma uses this in the opening line       |
| phone        | yes      | Any format, auto-normalized to E.164 US  |
| zip          | no       | If present, Emma skips asking            |
| dob          | no       | If present, Emma skips asking            |
| email        | no       | Logged to dashboard                      |
| state        | no       | Metadata only                            |

## Call flow

1. Ring -> on answer: "Hey {first_name}! How's it going today?"
2. Wait for reply, react warmly, pivot to pitch.
3. On interest: collect ZIP -> DOB -> transfer.
4. On 2 rejections / "don't call me" / hostile tone: auto-hangup (logged as `rejected`/`dnc`).
5. Voicemail detection: hangs up without leaving a message.

## Transfer mechanism (your cell question)

You asked if you can use your own cell phone. Yes - not as the caller ID, but as the
transfer destination. Flow:

- Outbound caller ID = your DC Twilio number (always)
- When Emma calls `transfer_call`, LiveKit issues a SIP REFER to Twilio
- Twilio bridges the in-progress call to `TRANSFER_TO_NUMBER` (your cell)
- The prospect's phone stays on the same call; you pick up on your cell
- Your cell number is never exposed to the prospect

## Learning loop

Nightly (or on-demand via dashboard's **Train on Past Calls** button):

```
python learnings.py
```

Reads the last 20 transferred + 20 rejected calls, asks GPT-4o to extract concrete
lessons, and writes `learnings.md`. That file is injected into Emma's system prompt
on the next worker restart. Kill and restart `agent.py dev` after each training pass.

Cron example: `0 3 * * * cd /path/to/project && python learnings.py && pkill -f "agent.py dev" && nohup python agent.py dev > agent.log 2>&1 &`

## Dashboard

- Live event feed (SSE): every ring/answer/ZIP/DOB/transfer/hangup
- Call list with outcome pills + transcript viewer
- 24h stats: total calls, conversion %, rejected, voicemails
- Upload Excel directly from UI to kick off a batch
- "Train on Past Calls" button runs learnings.py on demand

## Tuning Emma's voice

In `.env.local`:

- `OPENAI_TTS_VOICE` - female options: `shimmer` (soft/warm, default), `nova` (bright, young), `coral` (light, friendly), `sage` (mature/calm). Male: `alloy`, `ash`, `echo`, `onyx`, `fable`.
- `AMBIENCE_GAIN` - 0.08 (subtle) to 0.20 (clearly audible). Default 0.12.

If you want richer breathing/emotion, swap in ElevenLabs later:
```python
# requirements.txt: add livekit-agents[elevenlabs]
# agent.py:
from livekit.plugins import elevenlabs
tts=elevenlabs.TTS(voice_id="EXAVITQu4vr4xnSDxMaL", model="eleven_turbo_v2_5",
    voice_settings=elevenlabs.VoiceSettings(stability=0.45, similarity_boost=0.75, style=0.55, use_speaker_boost=True))
```

## Compliance (important)

- TCPA: don't dial numbers without prior consent; scrub against the DNC registry.
- Two-party recording: transcripts are stored. Disclose at call start if required by
  prospect's state (CA, FL, etc.). Add to opening line if needed.
- Emma currently claims to be human when asked. If you need disclosure add to the
  `instructions` block in `agent.py`.

## If this fails, check:

1. **`SIP 403/404` on outbound dial** - your Twilio trunk auth / outbound IP whitelist. Run `python fix_twilio_trunk.py`.
2. **Emma silent after pickup** - check worker logs for OpenAI TTS errors; verify `OPENAI_TTS_VOICE` is a valid voice name.
3. **Transfer fails silently** - `TRANSFER_TO_NUMBER` not in E.164 (+1... format) or Twilio trunk doesn't allow outbound to that number.
