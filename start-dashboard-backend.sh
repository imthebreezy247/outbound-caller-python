#!/bin/bash

# Start Dashboard Backend Server
# This script starts the FastAPI WebSocket backend for the dashboard

echo "=========================================="
echo "Starting Dashboard Backend Server"
echo "=========================================="

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Please run setup first."
    exit 1
fi

# Activate virtual environment
source venv/bin/activate || . venv/Scripts/activate

# Check if .env.local exists
if [ ! -f ".env.local" ]; then
    echo "❌ .env.local not found. Please configure your environment variables."
    exit 1
fi

echo ""
echo "✅ Starting FastAPI backend on http://localhost:8000"
echo "✅ WebSocket endpoint: ws://localhost:8000/ws"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Start the backend server
python3 dashboard_backend.py
