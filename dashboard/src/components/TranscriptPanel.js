import React, { useEffect, useRef } from 'react';
import './TranscriptPanel.css';

function TranscriptPanel({ call }) {
  const transcriptEndRef = useRef(null);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [call?.transcript]);

  const formatTime = (timestamp) => {
    const date = new Date(timestamp * 1000);
    return date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });
  };

  const getSentimentColor = (sentiment) => {
    const colors = {
      positive: '#22c55e',
      neutral: '#6b7280',
      negative: '#ef4444'
    };
    return colors[sentiment] || '#6b7280';
  };

  const getSentimentEmoji = (sentiment) => {
    const emojis = {
      positive: '😊',
      neutral: '😐',
      negative: '😟'
    };
    return emojis[sentiment] || '😐';
  };

  const getEmotionEmoji = (emotion) => {
    const emojis = {
      happy: '😄',
      frustrated: '😤',
      confused: '😕',
      excited: '🤩',
      calm: '😌',
      angry: '😠',
      interested: '🤔'
    };
    return emojis[emotion] || '';
  };

  if (!call) {
    return (
      <div className="card">
        <div className="card-header">
          <h2 className="card-title">
            <span className="icon">💬</span>
            Live Transcript
          </h2>
        </div>
        <div className="card-body">
          <div className="empty-state">
            <div className="empty-icon">📝</div>
            <p>Select a call to view transcript</p>
          </div>
        </div>
      </div>
    );
  }

  const transcript = call.transcript || [];

  return (
    <div className="card">
      <div className="card-header">
        <h2 className="card-title">
          <span className="icon">💬</span>
          Live Transcript
          {transcript.length > 0 && (
            <span className="count-badge">{transcript.length}</span>
          )}
        </h2>
        <div className="transcript-controls">
          <button className="btn-icon" title="Export transcript">
            📥
          </button>
          <button className="btn-icon" title="Clear transcript">
            🗑️
          </button>
        </div>
      </div>
      <div className="card-body">
        {transcript.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">⏳</div>
            <p>Waiting for conversation to start...</p>
          </div>
        ) : (
          <div className="transcript-container">
            {transcript.map((message, index) => (
              <div
                key={message.id || index}
                className={`transcript-message ${message.speaker}`}
              >
                <div className="message-header">
                  <div className="speaker-info">
                    <span className="speaker-avatar">
                      {message.speaker === 'agent' ? '🤖' : '👤'}
                    </span>
                    <span className="speaker-name">
                      {message.speaker === 'agent' ? 'John (Agent)' : call.customer_name}
                    </span>
                    {message.emotion && (
                      <span className="emotion-indicator" title={message.emotion}>
                        {getEmotionEmoji(message.emotion)}
                      </span>
                    )}
                  </div>
                  <div className="message-meta">
                    <span
                      className="sentiment-badge"
                      style={{ color: getSentimentColor(message.sentiment) }}
                      title={`Sentiment: ${message.sentiment}`}
                    >
                      {getSentimentEmoji(message.sentiment)}
                    </span>
                    <span className="timestamp">{formatTime(message.timestamp)}</span>
                  </div>
                </div>
                <div className="message-content">
                  <p>{message.text}</p>
                  {message.confidence && (
                    <div className="confidence-bar">
                      <div
                        className="confidence-fill"
                        style={{ width: `${message.confidence * 100}%` }}
                      ></div>
                    </div>
                  )}
                </div>
              </div>
            ))}
            <div ref={transcriptEndRef} />
          </div>
        )}
      </div>
    </div>
  );
}

export default TranscriptPanel;
