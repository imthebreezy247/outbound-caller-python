# Emma - Outbound Health Insurance Agent

## 🛠️ ONE-TIME SETUP (WSL)

Do this once. Skip to "Run a Call" on every run after.

**1. Open Ubuntu / WSL** (Start menu → "Ubuntu") and `cd` to the project:

```bash
cd /mnt/d/Coding-projects/outbound-caller-python-main
```

**2. Install Python tooling** (skip if already installed):

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

**3. Create the Linux-native venv** (must be `venv-wsl/` — `call.sh` expects this name; the Windows-side `venv/` will not work in WSL):

```bash
python3 -m venv venv-wsl
source venv-wsl/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

**4. Confirm `.env.local` has** `LIVEKIT_*`, `SIP_OUTBOUND_TRUNK_ID`, `TRANSFER_TO_NUMBER`, `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`.

**5. Make `call.sh` executable** (once):

```bash
chmod +x call.sh
```

---

## 🚀 RUN A CALL

**From inside WSL** (project dir):

```bash
./call.sh 9415180701 Chris
```

**From Windows PowerShell** (note: must be `wsl.exe`, not just `wsl` — and run from PowerShell, NOT from inside a WSL shell):

```powershell
wsl.exe -d Ubuntu -- bash -lc "cd /mnt/d/Coding-projects/outbound-caller-python-main && ./call.sh 9415180701 Chris"
```

Format: `call.sh <phone-number> <first-name>`

The script activates the venv, starts the agent + dashboard if not already running, then dispatches the call.

---

## 👀 WATCH THE TRANSCRIPT LIVE — *ALWAYS THE LAST STEP*

After `./call.sh` returns, open a **second WSL terminal** and run:

```bash
tail -f /tmp/emma-agent.log
```

You'll see every transcript line, Emma's replies, tool calls, and errors stream in real time. Keep this open for the entire call.

Optional browser view: <http://localhost:8080> — same data with ZIP/DOB capture and call history.

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

4. Restart the agent, then watch the transcript:

   ```bash
   pkill -f "python3 agent.py"
   ./call.sh 9415180701
   tail -f /tmp/emma-agent.log    # always the last step
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
