#!/bin/bash
# One-shot: start agent if not running, then dispatch a call.
# Usage from WSL:  ./call.sh 9415180701
# Usage from Windows:  wsl ./call.sh 9415180701
set -e
cd "$(dirname "$0")"

NUMBER="${1:-9415180701}"
NAME="${2:-Chris}"
LOG=/tmp/emma-agent.log
PID=/tmp/emma-agent.pid

source venv-wsl/bin/activate

# Start agent in background if not already running
if [ -f "$PID" ] && kill -0 "$(cat $PID)" 2>/dev/null; then
  echo "[agent already running, pid $(cat $PID)]"
else
  echo "[starting agent...]"
  setsid nohup python3 agent.py start > "$LOG" 2>&1 < /dev/null &
  echo $! > "$PID"
  # Wait for "registered worker"
  for i in $(seq 1 60); do
    grep -q "registered worker" "$LOG" 2>/dev/null && { echo "[agent registered in ${i}s]"; break; }
    sleep 2
  done
fi

# Fire the call
python3 test_call.py --to "$NUMBER" --name "$NAME"
