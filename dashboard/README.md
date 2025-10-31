# 📞 Outbound Caller Dashboard

A comprehensive, real-time monitoring dashboard for the AI Outbound Caller Agent. Built with React, Electron, and FastAPI WebSockets for seamless desktop experience and live call monitoring.

## ✨ Features

### 🔴 Live Call Monitoring
- **Real-time call status** - Track all active calls with live status updates
- **Multi-call view** - Monitor multiple simultaneous calls
- **Visual indicators** - Color-coded status badges and audio level meters
- **Call selection** - Click any call to view detailed information

### 💬 Live Transcription
- **Real-time transcription** - See conversation as it happens
- **Speaker identification** - Agent vs. Customer clearly labeled
- **Sentiment indicators** - Emoji-based sentiment for each message
- **Emotion detection** - Track emotional state during conversation
- **Confidence scores** - View transcription accuracy

### 🎵 Audio Visualization
- **Live waveforms** - Visual representation of audio activity
- **Voice activity detection** - See who's speaking in real-time
- **Volume meters** - Track audio levels for both parties
- **Noise monitoring** - Background noise level tracking
- **Connection quality** - Real-time quality metrics

### 📊 Sentiment Analysis
- **Overall sentiment** - Quick view of conversation mood
- **Breakdown chart** - Positive/Neutral/Negative percentages
- **Objection tracking** - Automatic objection detection and counting
- **Questions counter** - Track customer questions
- **Insight cards** - Key metrics at a glance

### 📈 Performance Statistics
- **Total calls** - Complete call volume tracking
- **Success rate** - Transfer success percentage
- **Average duration** - Mean call length statistics
- **Total transfers** - Successful transfer count
- **Revenue estimates** - Projected revenue from transfers
- **Trend indicators** - Up/down performance arrows

### 📋 Call History
- **Complete history** - All past calls in one place
- **Advanced filtering** - Filter by outcome (transferred, rejected, failed)
- **Search functionality** - Find calls by name or number
- **Detailed view** - Click any call to see full details
- **Recording playback** - Play back call recordings (when available)
- **Export capability** - Export transcripts and data

### 🎮 Control Panel
- **Start new calls** - Quick call initiation form
- **Active call controls** - Transfer, mute, hold, end call
- **Transfer management** - Manual transfer to human agents
- **Quick actions** - Load call lists, schedule calls
- **Import contacts** - Bulk contact import (coming soon)

### ⚙️ Configuration Panel
- **Agent settings** - Configure agent name, voice, personality
- **Human-like behavior** - Toggle natural fillers, pauses, tone variation
- **Voice model selection** - Choose from multiple voice options
- **Temperature control** - Adjust creativity vs. focus
- **Personality styles** - Professional, friendly, energetic, persistent
- **Call settings** - Recording, transcription, analysis toggles
- **Advanced options** - API endpoints, webhooks, debug mode

## 🚀 Quick Start

### Prerequisites
- Node.js 16+ and npm
- Python 3.9+
- Running outbound caller agent
- Dashboard backend server

### Installation

1. **Install dependencies:**
   ```bash
   cd dashboard
   npm install
   ```

2. **Install Python backend dependencies:**
   ```bash
   cd ..
   pip install -r requirements.txt
   ```

3. **Start the dashboard backend:**
   ```bash
   ./start-dashboard-backend.sh
   ```

4. **Start the dashboard frontend:**
   ```bash
   ./start-dashboard-frontend.sh
   ```

5. **Or start everything at once:**
   ```bash
   ./start-all.sh
   ```

### Manual Start

**Backend:**
```bash
python3 dashboard_backend.py
```

**Frontend (Development):**
```bash
cd dashboard
npm start
```

**Frontend (Electron):**
```bash
cd dashboard
npm run electron-dev
```

## 🏗️ Architecture

### Frontend (React + Electron)
- **React 18** - Modern UI framework
- **Electron** - Desktop application wrapper
- **Socket.io** - Real-time WebSocket communication
- **Recharts** - Data visualization
- **Framer Motion** - Smooth animations
- **CSS Modules** - Scoped styling

### Backend (FastAPI)
- **FastAPI** - Modern Python web framework
- **WebSockets** - Real-time bidirectional communication
- **LiveKit API** - Call control and management
- **Async/await** - Non-blocking operations
- **CORS enabled** - Cross-origin support

### Data Flow
```
LiveKit Agent → Dashboard Backend → WebSocket → Dashboard Frontend
     ↓                ↓                              ↓
  Call Data    Real-time Events         Live UI Updates
```

## 📡 API Endpoints

### HTTP Endpoints

**GET /**
- Health check
- Returns: `{"status": "online"}`

**GET /api/stats**
- Get dashboard statistics
- Returns: Statistics object

**GET /api/calls**
- Get call history
- Query params: `limit` (default: 50)
- Returns: List of calls

**GET /api/calls/{call_id}**
- Get specific call details
- Returns: Call object

**POST /api/calls/start**
- Start a new outbound call
- Body: `{ "phone_number", "customer_name", "transfer_to" }`
- Returns: `{ "success", "call_id", "dispatch_id" }`

**POST /api/calls/{call_id}/transfer**
- Transfer an active call
- Body: `{ "transfer_to" }`
- Returns: `{ "success" }`

**POST /api/calls/{call_id}/end**
- End an active call
- Returns: `{ "success" }`

### WebSocket Endpoint

**WS /ws**
- Real-time updates
- Events:
  - `initial_state` - Current state on connect
  - `call_started` - New call initiated
  - `call_status_update` - Call status changed
  - `transcript_update` - New transcript message
  - `audio_metrics` - Audio level updates

## 🎨 Customization

### Theming
Edit `src/App.css` and component CSS files to customize colors, spacing, and layout.

### Adding Features
1. Create new component in `src/components/`
2. Import and use in `src/App.js`
3. Add backend endpoint if needed in `dashboard_backend.py`

### Voice Models
Available OpenAI voices:
- `echo` - Male, friendly (default)
- `alloy` - Neutral
- `fable` - British accent
- `onyx` - Deep, authoritative
- `nova` - Female, warm
- `shimmer` - Female, energetic

## 🐛 Troubleshooting

**Dashboard won't connect:**
- Ensure backend is running on port 8000
- Check CORS settings in `dashboard_backend.py`
- Verify WebSocket URL in `src/App.js`

**No calls showing:**
- Start the outbound caller agent first
- Make a test call using control panel
- Check browser console for errors

**Audio visualization not working:**
- Ensure calls are sending audio metrics
- Check browser console for canvas errors
- Verify call object has `audio_metrics` array

**Electron app won't start:**
- Run `npm install` to ensure all dependencies
- Check if port 3000 is available
- Try starting React dev server separately first

## 📝 Environment Variables

Create `.env.local` in project root:

```bash
# LiveKit Configuration
LIVEKIT_URL=wss://your-livekit-url.com
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret

# SIP Configuration
SIP_OUTBOUND_TRUNK_ID=your_trunk_id

# Phone Numbers
MAX_PHONE_NUMBER=+19412314887
TWILIO_TO_NUMBER=+19415180701

# Dashboard (Optional)
REACT_APP_API_URL=http://localhost:8000
```

## 🔒 Security Notes

- Dashboard backend runs on localhost by default
- CORS is enabled for development (restrict in production)
- No authentication implemented (add for production use)
- WebSocket connections are unencrypted locally

## 📦 Building for Production

**Build React app:**
```bash
cd dashboard
npm run build
```

**Package Electron app:**
```bash
cd dashboard
npm run package
```

## 🤝 Contributing

To add new features:
1. Create feature branch
2. Add component or backend endpoint
3. Test thoroughly
4. Submit pull request

## 📄 License

See main project LICENSE file.

## 🆘 Support

For issues or questions:
- Check existing GitHub issues
- Create new issue with details
- Include browser/OS information
- Attach relevant logs

---

**Built with ❤️ for AI-powered sales automation**
