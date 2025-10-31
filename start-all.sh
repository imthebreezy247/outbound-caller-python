#!/bin/bash

# Start All Services
# This script starts:
# 1. Dashboard Backend (FastAPI)
# 2. Dashboard Frontend (React + Electron)
# 3. Outbound Caller Agent

echo "=========================================="
echo "Starting All Services"
echo "=========================================="

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "=========================================="
    echo "Stopping all services..."
    echo "=========================================="
    kill $(jobs -p) 2>/dev/null
    exit 0
}

trap cleanup EXIT INT TERM

# Start Dashboard Backend
echo "📡 Starting Dashboard Backend..."
./start-dashboard-backend.sh &
BACKEND_PID=$!
sleep 3

# Start Dashboard Frontend
echo "🖥️  Starting Dashboard Frontend..."
./start-dashboard-frontend.sh &
FRONTEND_PID=$!
sleep 5

# Start Agent
echo "🤖 Starting Outbound Caller Agent..."
./start-agent.sh &
AGENT_PID=$!

echo ""
echo "=========================================="
echo "✅ All Services Running!"
echo "=========================================="
echo ""
echo "📡 Dashboard Backend: http://localhost:8000"
echo "🖥️  Dashboard Frontend: http://localhost:3000"
echo "🤖 Agent Status: Running"
echo ""
echo "Press Ctrl+C to stop all services"
echo ""

# Wait for all background processes
wait
