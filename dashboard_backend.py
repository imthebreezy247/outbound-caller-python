"""
Real-time Dashboard Backend for Outbound Caller Agent

This FastAPI server provides WebSocket connections for real-time call monitoring,
analytics, transcription, and control features for the desktop dashboard.

Features:
- Real-time call status updates via WebSocket
- Live transcription streaming
- Audio level monitoring and visualization
- Sentiment analysis
- Call history and analytics
- Manual call controls (transfer, mute, hangup)
- Agent performance metrics
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncio
import json
import logging
from typing import Dict, List, Set, Any, Optional
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum
import uuid
from collections import defaultdict
import os
from dotenv import load_dotenv
from livekit import api, rtc

load_dotenv(dotenv_path=".env.local")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dashboard-backend")

# Initialize FastAPI app
app = FastAPI(title="Outbound Caller Dashboard API")

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# LiveKit API client
lk_api = api.LiveKitAPI(
    url=os.getenv("LIVEKIT_URL"),
    api_key=os.getenv("LIVEKIT_API_KEY"),
    api_secret=os.getenv("LIVEKIT_API_SECRET"),
)


class CallStatus(str, Enum):
    """Call status states"""
    IDLE = "idle"
    DIALING = "dialing"
    RINGING = "ringing"
    CONNECTED = "connected"
    TALKING = "talking"
    ON_HOLD = "on_hold"
    TRANSFERRING = "transferring"
    ENDED = "ended"
    FAILED = "failed"


class SpeakerRole(str, Enum):
    """Speaker identification"""
    AGENT = "agent"
    CUSTOMER = "customer"


@dataclass
class TranscriptMessage:
    """Individual transcript message"""
    id: str
    speaker: SpeakerRole
    text: str
    timestamp: float
    confidence: float
    sentiment: str  # positive, neutral, negative
    emotion: Optional[str] = None  # happy, frustrated, confused, etc.


@dataclass
class AudioMetrics:
    """Real-time audio metrics"""
    timestamp: float
    agent_volume: float
    customer_volume: float
    agent_speaking: bool
    customer_speaking: bool
    background_noise_level: float


@dataclass
class CallData:
    """Complete call information"""
    call_id: str
    phone_number: str
    customer_name: str
    status: CallStatus
    start_time: float
    end_time: Optional[float]
    duration: float
    transcript: List[TranscriptMessage]
    audio_metrics: List[AudioMetrics]
    sentiment_scores: Dict[str, float]  # positive, neutral, negative percentages
    objections_count: int
    objections: List[str]
    questions_asked: int
    transfer_to: Optional[str]
    outcome: Optional[str]  # transferred, hung_up, scheduled, rejected
    recording_url: Optional[str]
    room_name: Optional[str]


class ConnectionManager:
    """Manages WebSocket connections for real-time updates"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.call_data: Dict[str, CallData] = {}
        self.call_history: List[CallData] = []

    async def connect(self, websocket: WebSocket):
        """Accept and register new WebSocket connection"""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"New dashboard connection. Total: {len(self.active_connections)}")

        # Send current call state to newly connected client
        await self.send_initial_state(websocket)

    def disconnect(self, websocket: WebSocket):
        """Remove WebSocket connection"""
        self.active_connections.discard(websocket)
        logger.info(f"Dashboard disconnected. Total: {len(self.active_connections)}")

    async def send_initial_state(self, websocket: WebSocket):
        """Send current state to newly connected client"""
        initial_data = {
            "type": "initial_state",
            "active_calls": {
                call_id: asdict(call) for call_id, call in self.call_data.items()
            },
            "call_history": [asdict(call) for call in self.call_history[-50:]],  # Last 50 calls
            "stats": self.get_statistics()
        }
        await websocket.send_json(initial_data)

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast message to all connected clients"""
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to client: {e}")
                disconnected.add(connection)

        # Clean up disconnected clients
        self.active_connections -= disconnected

    async def update_call_status(self, call_id: str, status: CallStatus, data: Optional[Dict] = None):
        """Update call status and broadcast to all clients"""
        if call_id in self.call_data:
            call = self.call_data[call_id]
            call.status = status

            if data:
                for key, value in data.items():
                    if hasattr(call, key):
                        setattr(call, key, value)

            # Calculate duration
            call.duration = (
                call.end_time if call.end_time else datetime.now().timestamp()
            ) - call.start_time

            await self.broadcast({
                "type": "call_status_update",
                "call_id": call_id,
                "status": status.value,
                "data": asdict(call)
            })

    async def add_transcript(self, call_id: str, message: TranscriptMessage):
        """Add transcript message and broadcast"""
        if call_id in self.call_data:
            call = self.call_data[call_id]
            call.transcript.append(message)

            await self.broadcast({
                "type": "transcript_update",
                "call_id": call_id,
                "message": asdict(message)
            })

    async def update_audio_metrics(self, call_id: str, metrics: AudioMetrics):
        """Update audio metrics and broadcast"""
        if call_id in self.call_data:
            call = self.call_data[call_id]
            call.audio_metrics.append(metrics)

            # Keep only last 100 metrics to prevent memory issues
            if len(call.audio_metrics) > 100:
                call.audio_metrics = call.audio_metrics[-100:]

            await self.broadcast({
                "type": "audio_metrics",
                "call_id": call_id,
                "metrics": asdict(metrics)
            })

    def get_statistics(self) -> Dict[str, Any]:
        """Calculate dashboard statistics"""
        total_calls = len(self.call_history)
        if total_calls == 0:
            return {
                "total_calls": 0,
                "active_calls": 0,
                "successful_transfers": 0,
                "average_duration": 0,
                "success_rate": 0,
            }

        successful = sum(1 for call in self.call_history if call.outcome == "transferred")
        total_duration = sum(call.duration for call in self.call_history)

        return {
            "total_calls": total_calls,
            "active_calls": len(self.call_data),
            "successful_transfers": successful,
            "average_duration": total_duration / total_calls if total_calls > 0 else 0,
            "success_rate": (successful / total_calls * 100) if total_calls > 0 else 0,
            "total_duration": total_duration,
        }


# Global connection manager
manager = ConnectionManager()


@app.get("/")
async def root():
    """API health check"""
    return {"status": "online", "service": "Outbound Caller Dashboard API"}


@app.get("/api/stats")
async def get_statistics():
    """Get dashboard statistics"""
    return manager.get_statistics()


@app.get("/api/calls")
async def get_calls(limit: int = 50):
    """Get recent call history"""
    return {
        "calls": [asdict(call) for call in manager.call_history[-limit:]],
        "total": len(manager.call_history)
    }


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str):
    """Get specific call details"""
    if call_id in manager.call_data:
        return asdict(manager.call_data[call_id])

    # Check history
    for call in manager.call_history:
        if call.call_id == call_id:
            return asdict(call)

    raise HTTPException(status_code=404, detail="Call not found")


@app.post("/api/calls/start")
async def start_call(phone_number: str, customer_name: str, transfer_to: str):
    """Start a new outbound call"""
    call_id = str(uuid.uuid4())

    call_data = CallData(
        call_id=call_id,
        phone_number=phone_number,
        customer_name=customer_name,
        status=CallStatus.DIALING,
        start_time=datetime.now().timestamp(),
        end_time=None,
        duration=0,
        transcript=[],
        audio_metrics=[],
        sentiment_scores={"positive": 0, "neutral": 100, "negative": 0},
        objections_count=0,
        objections=[],
        questions_asked=0,
        transfer_to=transfer_to,
        outcome=None,
        recording_url=None,
        room_name=f"outbound-call-{call_id}"
    )

    manager.call_data[call_id] = call_data

    # Dispatch the call via LiveKit (same as make_call.py)
    metadata = json.dumps({
        "phone_number": phone_number,
        "transfer_to": transfer_to,
        "call_id": call_id,
    })

    try:
        dispatch = await lk_api.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="outbound-caller",
                room=call_data.room_name,
                metadata=metadata,
            )
        )

        await manager.broadcast({
            "type": "call_started",
            "call_id": call_id,
            "data": asdict(call_data)
        })

        return {"success": True, "call_id": call_id, "dispatch_id": dispatch.id}

    except Exception as e:
        logger.error(f"Error starting call: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/calls/{call_id}/transfer")
async def transfer_call(call_id: str, transfer_to: str):
    """Transfer an active call"""
    if call_id not in manager.call_data:
        raise HTTPException(status_code=404, detail="Call not found")

    call = manager.call_data[call_id]

    try:
        # Use LiveKit SIP transfer API
        await lk_api.sip.transfer_sip_participant(
            api.TransferSIPParticipantRequest(
                room_name=call.room_name,
                participant_identity=call.phone_number,
                transfer_to=f"tel:{transfer_to}",
            )
        )

        await manager.update_call_status(call_id, CallStatus.TRANSFERRING, {
            "transfer_to": transfer_to
        })

        return {"success": True}

    except Exception as e:
        logger.error(f"Error transferring call: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/calls/{call_id}/end")
async def end_call(call_id: str):
    """End an active call"""
    if call_id not in manager.call_data:
        raise HTTPException(status_code=404, detail="Call not found")

    call = manager.call_data[call_id]

    try:
        # Delete the LiveKit room to end the call
        await lk_api.room.delete_room(
            api.DeleteRoomRequest(room=call.room_name)
        )

        call.end_time = datetime.now().timestamp()
        await manager.update_call_status(call_id, CallStatus.ENDED)

        # Move to history
        manager.call_history.append(call)
        del manager.call_data[call_id]

        return {"success": True}

    except Exception as e:
        logger.error(f"Error ending call: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time dashboard updates"""
    await manager.connect(websocket)

    try:
        while True:
            # Receive messages from client
            data = await websocket.receive_json()

            # Handle different message types
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

            elif data.get("type") == "subscribe":
                # Client wants to subscribe to specific call updates
                pass

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


# Simulated real-time updates (for testing)
async def simulate_call_updates():
    """Background task to simulate call updates for testing"""
    while True:
        await asyncio.sleep(2)

        # Update audio metrics for active calls
        for call_id, call in manager.call_data.items():
            if call.status in [CallStatus.CONNECTED, CallStatus.TALKING]:
                import random
                metrics = AudioMetrics(
                    timestamp=datetime.now().timestamp(),
                    agent_volume=random.uniform(0.3, 0.8),
                    customer_volume=random.uniform(0.2, 0.7),
                    agent_speaking=random.choice([True, False]),
                    customer_speaking=random.choice([True, False]),
                    background_noise_level=random.uniform(0.1, 0.3)
                )
                await manager.update_audio_metrics(call_id, metrics)


# Start background tasks
@app.on_event("startup")
async def startup_event():
    """Start background tasks on server startup"""
    # asyncio.create_task(simulate_call_updates())  # Uncomment for testing
    logger.info("Dashboard backend started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown"""
    await lk_api.aclose()
    logger.info("Dashboard backend shut down")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
