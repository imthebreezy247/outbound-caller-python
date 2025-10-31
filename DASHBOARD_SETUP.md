# 🖥️ Dashboard Setup Guide

Complete guide to setting up and running the Outbound Caller Dashboard.

## 📋 Prerequisites

Before you begin, ensure you have:

- ✅ Python 3.9 or higher
- ✅ Node.js 16+ and npm
- ✅ Git (for cloning)
- ✅ LiveKit account and credentials
- ✅ OpenAI API key (for Realtime API)

## 🚀 Installation Steps

### 1. Install Python Dependencies

```bash
# Navigate to project root
cd outbound-caller-python

# Create virtual environment (if not already created)
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On Linux/Mac
# OR
.\venv\Scripts\activate  # On Windows

# Install all dependencies including dashboard
pip install -r requirements.txt
```

### 2. Install Node.js Dependencies

```bash
# Navigate to dashboard directory
cd dashboard

# Install npm packages
npm install

# This will install:
# - React 18
# - Electron
# - Socket.io client
# - Recharts (for visualizations)
# - Framer Motion (for animations)
# - And all other dependencies
```

### 3. Configure Environment Variables

Create or update `.env.local` in the project root:

```bash
# LiveKit Configuration (Required)
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_api_key_here
LIVEKIT_API_SECRET=your_api_secret_here

# OpenAI Configuration (Required)
OPENAI_API_KEY=your_openai_key_here

# SIP Configuration (Required for calling)
SIP_OUTBOUND_TRUNK_ID=your_trunk_id

# Phone Numbers
MAX_PHONE_NUMBER=+19412314887  # Transfer destination
TWILIO_TO_NUMBER=+19415180701  # Default test number

# Dashboard Configuration (Optional)
DASHBOARD_PORT=8000  # Backend API port
REACT_APP_API_URL=http://localhost:8000  # Backend URL
```

### 4. Verify Installation

Check that all components are ready:

```bash
# Check Python packages
pip list | grep -E 'livekit|fastapi|uvicorn'

# Check Node packages
cd dashboard
npm list react react-dom electron
```

## 🎯 Running the Dashboard

### Option 1: Start All Services at Once (Recommended)

```bash
# From project root
./start-all.sh
```

This will start:
1. 📡 Dashboard Backend (FastAPI) on port 8000
2. 🖥️ Dashboard Frontend (React) on port 3000
3. 🤖 Outbound Caller Agent

### Option 2: Start Services Individually

**Terminal 1 - Backend:**
```bash
./start-dashboard-backend.sh
# OR
python3 dashboard_backend.py
```

**Terminal 2 - Frontend:**
```bash
./start-dashboard-frontend.sh
# OR
cd dashboard && npm run electron-dev
```

**Terminal 3 - Agent:**
```bash
./start-agent.sh
# OR
python3 agent.py dev
```

### Option 3: Development Mode (React only, no Electron)

```bash
cd dashboard
npm start
# Opens http://localhost:3000 in your browser
```

## 🔍 Verifying Everything Works

### 1. Check Backend is Running

Open your browser or use curl:
```bash
curl http://localhost:8000
# Should return: {"status": "online", "service": "Outbound Caller Dashboard API"}
```

### 2. Check Frontend is Running

- Open http://localhost:3000 in your browser
- Should see the dashboard with "Outbound Caller" header
- Connection status indicator should show "Connected" (green)

### 3. Make a Test Call

1. In the dashboard, click **"Start New Call"**
2. Fill in:
   - Customer Name: "Test Customer"
   - Phone Number: "+19415180701" (or your test number)
   - Transfer To: "+19412314887" (or your transfer number)
3. Click **"Start Call"**
4. Watch the dashboard for real-time updates!

## 📊 Dashboard Features Overview

### Live Monitoring Tab
- **Active Calls** - See all ongoing calls
- **Live Transcription** - Real-time conversation text
- **Audio Visualization** - Waveforms and voice activity
- **Sentiment Analysis** - Emotional state tracking
- **Statistics** - Performance metrics
- **Control Panel** - Start/stop/transfer calls

### Call History Tab
- View all past calls
- Filter by outcome (transferred, rejected, failed)
- Search by name or number
- Click to view full details

### Configuration Tab
- Agent personality settings
- Voice model selection
- Human-like behavior toggles
- Call recording options
- Advanced settings

## 🎨 UI Components

### 1. Live Call Monitor
Shows all active calls with:
- Customer name and phone number
- Call status (dialing, ringing, connected, etc.)
- Call duration
- Message count
- Audio level meters

### 2. Transcript Panel
Displays conversation in real-time:
- Speaker identification (Agent vs Customer)
- Sentiment indicators (😊 😐 😟)
- Emotion detection
- Timestamps
- Confidence scores

### 3. Audio Visualization
Shows audio activity:
- Live waveforms for both speakers
- Voice activity indicators
- Volume meters
- Background noise levels
- Connection quality

### 4. Sentiment Analysis
Tracks conversation mood:
- Overall sentiment gauge
- Positive/Neutral/Negative breakdown
- Objection counter
- Question tracker
- Key insights

### 5. Statistics Dashboard
Performance metrics:
- Total calls made
- Success rate percentage
- Average call duration
- Total transfers
- Revenue estimates

## 🛠️ Troubleshooting

### Backend Won't Start

**Issue:** `ModuleNotFoundError: No module named 'fastapi'`
```bash
# Solution:
pip install fastapi uvicorn websockets python-socketio
```

**Issue:** Port 8000 already in use
```bash
# Solution: Kill process using port 8000
lsof -ti:8000 | xargs kill -9
# OR change port in dashboard_backend.py
```

### Frontend Won't Start

**Issue:** `npm: command not found`
```bash
# Solution: Install Node.js
# Visit https://nodejs.org/ and install LTS version
```

**Issue:** `Cannot find module 'react'`
```bash
# Solution:
cd dashboard
rm -rf node_modules package-lock.json
npm install
```

### Dashboard Shows "Disconnected"

1. **Check backend is running:**
   ```bash
   curl http://localhost:8000
   ```

2. **Check WebSocket connection:**
   - Open browser console (F12)
   - Look for WebSocket connection errors
   - Verify URL is correct in `src/App.js`

3. **Check CORS settings:**
   - Backend allows all origins by default
   - If restricted, add frontend URL to `allow_origins`

### No Calls Appearing

1. **Ensure agent is running:**
   ```bash
   ps aux | grep agent.py
   ```

2. **Check agent logs:**
   - Look for connection errors
   - Verify LiveKit credentials

3. **Make a test call:**
   - Use the Control Panel to start a call
   - Watch backend logs for errors

### Electron App Won't Launch

**Issue:** Blank screen
```bash
# Solution:
cd dashboard
npm run start  # Start React dev server first
# Wait for it to start, then in another terminal:
npm run electron
```

**Issue:** `electron: command not found`
```bash
# Solution:
cd dashboard
npm install electron --save-dev
```

## 🔧 Advanced Configuration

### Change Backend Port

Edit `dashboard_backend.py`:
```python
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)  # Change port here
```

Update frontend to match in `src/App.js`:
```javascript
const API_BASE_URL = 'http://localhost:8080';
```

### Enable HTTPS

For production, use a reverse proxy like nginx or run uvicorn with SSL:
```bash
uvicorn dashboard_backend:app --host 0.0.0.0 --port 8000 \
  --ssl-keyfile /path/to/key.pem \
  --ssl-certfile /path/to/cert.pem
```

### Add Authentication

Add middleware to `dashboard_backend.py`:
```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer

security = HTTPBearer()

async def verify_token(credentials = Depends(security)):
    if credentials.credentials != "your-secret-token":
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials

# Add to endpoints:
@app.get("/api/stats", dependencies=[Depends(verify_token)])
async def get_statistics():
    # ...
```

## 📱 Mobile Access

While primarily designed for desktop, you can access via mobile browser:

1. Find your computer's local IP:
   ```bash
   ifconfig | grep inet  # Mac/Linux
   ipconfig  # Windows
   ```

2. Start backend with `--host 0.0.0.0`:
   ```bash
   python3 dashboard_backend.py
   ```

3. Start React without Electron:
   ```bash
   cd dashboard
   BROWSER=none npm start
   ```

4. Access from mobile browser:
   ```
   http://YOUR_LOCAL_IP:3000
   ```

## 🚢 Production Deployment

### Build React App
```bash
cd dashboard
npm run build
```

### Serve with Backend
```bash
# Update dashboard_backend.py to serve static files
from fastapi.staticfiles import StaticFiles

app.mount("/", StaticFiles(directory="dashboard/build", html=True), name="static")
```

### Run with Production Server
```bash
gunicorn dashboard_backend:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

## 📞 Support

If you encounter issues:

1. Check this troubleshooting guide
2. Review error logs in terminal
3. Check browser console (F12)
4. Verify all environment variables are set
5. Ensure all dependencies are installed

For additional help, create an issue on GitHub with:
- Error messages
- Steps to reproduce
- Environment details (OS, Python version, Node version)

---

**Enjoy your new AI-powered calling dashboard! 🎉**
