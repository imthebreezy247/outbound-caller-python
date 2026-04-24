#!/bin/bash
# One-shot: start agent + dashboard if not running, then dispatch a call.
# Usage from WSL:       ./call.sh 9415180701 [Name]
# Usage from Windows:   wsl ./call.sh 9415180701 [Name]
set -e
cd "$(dirname "$0")"

NUMBER="${1:-9415180701}"
NAME="${2:-Chris}"

AGENT_LOG=/tmp/emma-agent.log
DASH_LOG=/tmp/emma-dashboard.log
AGENT_PID=/tmp/emma-agent.pid
DASH_PID=/tmp/emma-dashboard.pid

source venv-wsl/bin/activate

# ---- Start agent if not already running ----
if [ -f "$AGENT_PID" ] && kill -0 "$(cat $AGENT_PID)" 2>/dev/null; then
  echo "[agent already running, pid $(cat $AGENT_PID)]"
else
  echo "[starting agent...]"
  setsid nohup python3 agent.py start > "$AGENT_LOG" 2>&1 < /dev/null &
  echo $! > "$AGENT_PID"
  for i in $(seq 1 60); do
    grep -q "registered worker" "$AGENT_LOG" 2>/dev/null && { echo "[agent registered in ${i}s]"; break; }
    sleep 2
  done
fi

# ---- Start dashboard if not already running ----
if [ -f "$DASH_PID" ] && kill -0 "$(cat $DASH_PID)" 2>/dev/null; then
  echo "[dashboard already running, pid $(cat $DASH_PID)] -> http://localhost:8080"
else
  echo "[starting dashboard -> http://localhost:8080]"
  setsid nohup python3 -m uvicorn dashboard:app --host 0.0.0.0 --port 8080 > "$DASH_LOG" 2>&1 < /dev/null &
  echo $! > "$DASH_PID"
  sleep 2
fi

echo ""
echo "=========================================="
echo "  WATCH THE CALL LIVE:"
echo "    Browser:  http://localhost:8080"
echo "    Terminal: tail -f $AGENT_LOG"
echo "=========================================="
echo ""

# ---- Fire the call ----
python3 test_call.py --to "$NUMBER" --name "$NAME"
