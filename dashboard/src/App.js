import React, { useState, useEffect, useCallback } from 'react';
import './App.css';
import io from 'socket.io-client';
import axios from 'axios';

// Import components
import Header from './components/Header';
import LiveCallMonitor from './components/LiveCallMonitor';
import TranscriptPanel from './components/TranscriptPanel';
import AudioVisualization from './components/AudioVisualization';
import SentimentAnalysis from './components/SentimentAnalysis';
import CallHistory from './components/CallHistory';
import Statistics from './components/Statistics';
import ControlPanel from './components/ControlPanel';
import ConfigPanel from './components/ConfigPanel';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

function App() {
  const [socket, setSocket] = useState(null);
  const [connected, setConnected] = useState(false);
  const [activeCalls, setActiveCalls] = useState({});
  const [callHistory, setCallHistory] = useState([]);
  const [statistics, setStatistics] = useState(null);
  const [selectedCall, setSelectedCall] = useState(null);
  const [activeTab, setActiveTab] = useState('live'); // live, history, config

  // Initialize WebSocket connection
  useEffect(() => {
    const ws = io(API_BASE_URL, {
      transports: ['websocket'],
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
    });

    ws.on('connect', () => {
      console.log('WebSocket connected');
      setConnected(true);
    });

    ws.on('disconnect', () => {
      console.log('WebSocket disconnected');
      setConnected(false);
    });

    ws.on('initial_state', (data) => {
      console.log('Received initial state:', data);
      setActiveCalls(data.active_calls || {});
      setCallHistory(data.call_history || []);
      setStatistics(data.stats);
    });

    ws.on('call_started', (data) => {
      console.log('Call started:', data);
      setActiveCalls(prev => ({
        ...prev,
        [data.call_id]: data.data
      }));
    });

    ws.on('call_status_update', (data) => {
      console.log('Call status update:', data);
      setActiveCalls(prev => ({
        ...prev,
        [data.call_id]: data.data
      }));
    });

    ws.on('transcript_update', (data) => {
      console.log('Transcript update:', data);
      setActiveCalls(prev => {
        const call = prev[data.call_id];
        if (call) {
          return {
            ...prev,
            [data.call_id]: {
              ...call,
              transcript: [...call.transcript, data.message]
            }
          };
        }
        return prev;
      });
    });

    ws.on('audio_metrics', (data) => {
      setActiveCalls(prev => {
        const call = prev[data.call_id];
        if (call) {
          return {
            ...prev,
            [data.call_id]: {
              ...call,
              audio_metrics: [...(call.audio_metrics || []).slice(-99), data.metrics]
            }
          };
        }
        return prev;
      });
    });

    setSocket(ws);

    return () => {
      ws.close();
    };
  }, []);

  // Fetch statistics periodically
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const response = await axios.get(`${API_BASE_URL}/api/stats`);
        setStatistics(response.data);
      } catch (error) {
        console.error('Error fetching stats:', error);
      }
    };

    fetchStats();
    const interval = setInterval(fetchStats, 10000); // Every 10 seconds

    return () => clearInterval(interval);
  }, []);

  const handleStartCall = useCallback(async (phoneNumber, customerName, transferTo) => {
    try {
      const response = await axios.post(`${API_BASE_URL}/api/calls/start`, {
        phone_number: phoneNumber,
        customer_name: customerName,
        transfer_to: transferTo
      });
      console.log('Call started:', response.data);
    } catch (error) {
      console.error('Error starting call:', error);
      alert('Failed to start call: ' + error.message);
    }
  }, []);

  const handleEndCall = useCallback(async (callId) => {
    try {
      await axios.post(`${API_BASE_URL}/api/calls/${callId}/end`);
      console.log('Call ended:', callId);
    } catch (error) {
      console.error('Error ending call:', error);
    }
  }, []);

  const handleTransferCall = useCallback(async (callId, transferTo) => {
    try {
      await axios.post(`${API_BASE_URL}/api/calls/${callId}/transfer`, {
        transfer_to: transferTo
      });
      console.log('Call transferred:', callId);
    } catch (error) {
      console.error('Error transferring call:', error);
    }
  }, []);

  const activeCallsList = Object.values(activeCalls);
  const currentCall = selectedCall
    ? activeCalls[selectedCall] || callHistory.find(c => c.call_id === selectedCall)
    : activeCallsList[0];

  return (
    <div className="App">
      <Header
        connected={connected}
        activeCallsCount={activeCallsList.length}
        onTabChange={setActiveTab}
        activeTab={activeTab}
      />

      {activeTab === 'live' && (
        <div className="dashboard-container">
          {/* Top Row: Live Call Monitor and Controls */}
          <div className="dashboard-row">
            <div className="dashboard-col-wide">
              <LiveCallMonitor
                activeCalls={activeCallsList}
                selectedCall={selectedCall}
                onSelectCall={setSelectedCall}
              />
            </div>
            <div className="dashboard-col">
              <ControlPanel
                currentCall={currentCall}
                onStartCall={handleStartCall}
                onEndCall={handleEndCall}
                onTransferCall={handleTransferCall}
              />
            </div>
          </div>

          {/* Second Row: Statistics and Sentiment */}
          <div className="dashboard-row">
            <div className="dashboard-col">
              <Statistics statistics={statistics} />
            </div>
            <div className="dashboard-col">
              <SentimentAnalysis call={currentCall} />
            </div>
          </div>

          {/* Third Row: Transcript and Audio Visualization */}
          <div className="dashboard-row">
            <div className="dashboard-col">
              <TranscriptPanel call={currentCall} />
            </div>
            <div className="dashboard-col">
              <AudioVisualization call={currentCall} />
            </div>
          </div>
        </div>
      )}

      {activeTab === 'history' && (
        <div className="dashboard-container">
          <CallHistory
            callHistory={callHistory}
            onSelectCall={(call) => {
              setSelectedCall(call.call_id);
              setActiveTab('live');
            }}
          />
        </div>
      )}

      {activeTab === 'config' && (
        <div className="dashboard-container">
          <ConfigPanel />
        </div>
      )}
    </div>
  );
}

export default App;
