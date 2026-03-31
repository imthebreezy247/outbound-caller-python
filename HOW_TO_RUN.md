# HOW TO RUN - AI Outbound Caller (Health Insurance Sales Agent)

Guide to run the AI-powered outbound calling system using LiveKit + OpenAI Realtime API.

---

## Prerequisites

- Python 3.11+ installed in WSL2
- WSL2 installed and working (`wsl` from Windows terminal)
- LiveKit account with SIP trunk configured
- Twilio account with a phone number
- `.env.local` file with all credentials filled in (copy from `.env.example`)

---

## Quick Start - 2 Steps

1. **Start the AI Agent** - Runs the worker that handles calls
2. **Dispatch a Call** - Tell the agent to dial a phone number

---

## Step 1: Start the AI Agent (WSL2 Required)

Open a WSL2 terminal and run:

```bash
cd /mnt/d/Coding-projects/outbound-caller-python-main
source venv-wsl/bin/activate
python3 agent.py start
```

You will see initialization logs like:

```
{"message": "initializing process", "level": "INFO", ...}
{"message": "process initialized", "level": "INFO", ...}
```

Wait for the `"registered worker"` message - that means the agent is ready.

Keep this terminal open.

---

## Step 2: Make a Phone Call

### Option A: LiveKit Dashboard (Easiest)

1. Go to <https://cloud.livekit.io/>
2. Select your project
3. Go to **Agents** > find **outbound-caller** > click **Dispatch**
4. Enter metadata:

   ```json
   {"phone_number": "+1XXXXXXXXXX", "transfer_to": "+1YYYYYYYYYY"}
   ```

   - `phone_number` = the number to call
   - `transfer_to` = number to transfer to when prospect agrees (Steeve's number)
5. Click **Dispatch**

### Option B: LiveKit CLI (from WSL2)

```bash
lk dispatch create \
  --new-room \
  --agent-name outbound-caller \
  --metadata '{"phone_number": "+1XXXXXXXXXX", "transfer_to": "+1YYYYYYYYYY"}'
```

The phone will ring. When answered, the AI agent "John" will start the health insurance sales pitch.

---

## What the AI Agent Does

The agent plays "John", a health insurance sales agent from Tampa, FL:

- Greets the prospect and asks how they're doing
- Pitches a free health insurance quote (20-40% savings)
- Handles objections persistently (multiple rebuttals per objection)
- Asks for the prospect's age to qualify them
- When they agree, transfers the call to "David" (the specialist) via `transfer_to` number
- Detects voicemail and hangs up automatically
- Uses OpenAI Realtime API with the "ash" voice

---

## Configuration

### .env.local (Required)

Copy `.env.example` to `.env.local` and fill in:

```bash
# LiveKit (from https://cloud.livekit.io/ > Settings)
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret

# OpenAI (from https://platform.openai.com/api-keys)
OPENAI_API_KEY=sk-proj-...

# SIP Trunk (from LiveKit dashboard after trunk setup)
SIP_OUTBOUND_TRUNK_ID=ST_...

# Optional: Only needed if switching to Claude pipelined approach
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=...
```

### Switching to Claude Sonnet 4 (Pipelined Approach)

In `agent.py`, comment out the OpenAI Realtime session (~line 471) and uncomment the Claude session (~line 480). The pipelined approach uses Deepgram STT + Claude LLM + Cartesia TTS. This gives better reasoning but is slower than Realtime API.

---

## Troubleshooting

### Agent won't start / TimeoutError

- **Cause:** LiveKit agents require Unix IPC - doesn't work on Windows/Git Bash
- **Fix:** Must run in WSL2. Open `wsl` terminal first, then run from there

### "registered worker" never appears

- Check `.env.local` has valid `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- Check your LiveKit project is active at <https://cloud.livekit.io/>

### Phone doesn't ring after dispatch

- Check `SIP_OUTBOUND_TRUNK_ID` is correct in `.env.local`
- Check SIP trunk is active in LiveKit dashboard
- Verify phone number is E.164 format (`+1XXXXXXXXXX`)
- Check Twilio has credits and the phone number is active

### Call connects but no audio / agent doesn't speak

- Check `OPENAI_API_KEY` is valid and has credits
- Look at WSL2 terminal logs for errors

### `lk` command not found

- Install LiveKit CLI: `curl -sSL https://get.livekit.io/cli | bash`
- Or just use the dashboard instead

---

## Stopping

- Press `Ctrl+C` in the WSL2 terminal to stop the agent
- Active calls will end when the agent stops
