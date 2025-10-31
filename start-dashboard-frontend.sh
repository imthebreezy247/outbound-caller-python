#!/bin/bash

# Start Dashboard Frontend (React + Electron)
# This script starts the React development server and Electron app

echo "=========================================="
echo "Starting Dashboard Frontend"
echo "=========================================="

cd dashboard || exit 1

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "📦 Installing npm dependencies..."
    npm install
fi

echo ""
echo "✅ Starting React development server on http://localhost:3000"
echo "✅ Launching Electron desktop app"
echo ""
echo "Press Ctrl+C to stop the frontend"
echo ""

# Start development server and Electron
npm run electron-dev
