# Emma - Outbound Health Insurance Agent

## 🚀 HOW TO RUN - ONE COMMAND

**From Windows PowerShell or any terminal:**

```bash
wsl /mnt/d/Coding-projects/outbound-caller-python-main/call.sh 9415180701 Chris
```

**From inside WSL (already in project dir):**

```bash
./call.sh 9415180701 Chris
```

Format: `call.sh <phone-number> <first-name>`

That's it. The script starts the agent, starts the dashboard, and dispatches the call.

---

## 👀 HOW TO WATCH THE CALL LIVE

You have **two options** — use either or both:

### Option A: Browser Dashboard (recommended)

After running `./call.sh`, open in your browser:

**→ <http://localhost:8080>**

You'll see live transcripts, captured ZIP/DOB, call status, and history.

### Option B: Terminal Stream

Open a **second WSL terminal** and run:

```bash
tail -f /tmp/emma-agent.log
```

You'll see every line the agent logs — user transcripts, Emma's responses, tool calls, errors. Great for debugging.

---

## 🛑 HOW TO STOP THE AGENT

```bash
pkill -f "python3 agent.py"
pkill -f "uvicorn dashboard:app"
```

Or just close the WSL window — the processes die with it.

---

## 🎤 CHANGING EMMA'S VOICE

Current setup: **ElevenLabs Jessica** (young, expressive, American female).

To swap voices:

1. Browse <https://elevenlabs.io/app/voice-library> and click any voice to preview
2. Copy its Voice ID (looks like `cgSgspJ2msm6clMCkdW9`)
3. Paste into `.env.local`:

   ```bash
   ELEVENLABS_VOICE_ID=<your-chosen-id>
   ```

4. Restart the agent:

   ```bash
   pkill -f "python3 agent.py"
   ./call.sh 9415180701
   ```

**IMPORTANT:** You want the **Voice Library**, NOT ElevenLabs "Agents." The Agents product is a competing all-in-one product we don't use. Just browse voices and grab IDs.

---

## 📝 CHANGING WHAT EMMA SAYS

Her full script is in [agent.py](agent.py) lines 112-191 — the big multiline `instructions=f"""..."""` block inside `EmmaAgent.__init__`. Edit that text, restart the agent, done.

---

## 🔧 TROUBLESHOOTING

### Phone doesn't ring after dispatch

- Check `/tmp/emma-agent.log` for errors
- Verify `SIP_OUTBOUND_TRUNK_ID` in `.env.local` is correct
- Make sure the number is US 10-digit format

### Dashboard won't load at localhost:8080

- Check `/tmp/emma-dashboard.log` for startup errors
- Make sure nothing else is on port 8080 (`lsof -i :8080`)

### "registered worker" never appears

- `.env.local` missing credentials — check LiveKit keys
- Network/firewall blocking websocket connection to LiveKit Cloud

### Emma sounds robotic

- You're probably still on Deepgram TTS
- Swap to ElevenLabs: set `ELEVENLABS_API_KEY` in `.env.local` (see above section)
- Restart agent

---

## 📁 KEY FILES

| File                                             | Purpose                              |
| ------------------------------------------------ | ------------------------------------ |
| [call.sh](call.sh)                               | One-command launcher                 |
| [agent.py](agent.py)                             | Emma's brain + prompt (line 112)     |
| [dashboard.py](dashboard.py)                     | Browser UI at localhost:8080         |
| [test_call.py](test_call.py)                     | Dispatches calls to the agent        |
| [.env.local](.env.local)                         | Your API keys + config               |
| [transcript_logger.py](transcript_logger.py)     | Writes calls to calls.db             |
| [scrubber.py](scrubber.py)                       | Landline/DNC checks before calling   |
