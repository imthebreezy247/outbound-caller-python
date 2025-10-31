import React from 'react';
import './LiveCallMonitor.css';

function LiveCallMonitor({ activeCalls, selectedCall, onSelectCall }) {
  const formatDuration = (seconds) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const getStatusColor = (status) => {
    const colors = {
      idle: '#6b7280',
      dialing: '#3b82f6',
      ringing: '#fb923c',
      connected: '#22c55e',
      talking: '#22c55e',
      on_hold: '#f59e0b',
      transferring: '#8b5cf6',
      ended: '#6b7280',
      failed: '#ef4444'
    };
    return colors[status] || '#6b7280';
  };

  if (activeCalls.length === 0) {
    return (
      <div className="card">
        <div className="card-header">
          <h2 className="card-title">
            <span className="icon">📞</span>
            Live Calls
          </h2>
        </div>
        <div className="card-body">
          <div className="empty-state">
            <div className="empty-icon">💤</div>
            <h3>No Active Calls</h3>
            <p>Start a new call using the control panel to begin monitoring</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-header">
        <h2 className="card-title">
          <span className="icon">📞</span>
          Live Calls
          <span className="count-badge">{activeCalls.length}</span>
        </h2>
      </div>
      <div className="card-body">
        <div className="calls-grid">
          {activeCalls.map((call) => (
            <div
              key={call.call_id}
              className={`call-card ${selectedCall === call.call_id ? 'selected' : ''}`}
              onClick={() => onSelectCall(call.call_id)}
              style={{ borderColor: getStatusColor(call.status) }}
            >
              <div className="call-card-header">
                <div className="call-info">
                  <div className="customer-name">{call.customer_name}</div>
                  <div className="phone-number">{call.phone_number}</div>
                </div>
                <div
                  className="status-indicator"
                  style={{ background: getStatusColor(call.status) }}
                >
                  <div className="status-pulse"></div>
                </div>
              </div>

              <div className="call-card-body">
                <div className="status-row">
                  <span className="status-label">Status:</span>
                  <span
                    className="status-value"
                    style={{ color: getStatusColor(call.status) }}
                  >
                    {call.status.replace('_', ' ').toUpperCase()}
                  </span>
                </div>

                <div className="status-row">
                  <span className="status-label">Duration:</span>
                  <span className="status-value">
                    {formatDuration(call.duration)}
                  </span>
                </div>

                <div className="status-row">
                  <span className="status-label">Messages:</span>
                  <span className="status-value">
                    {call.transcript?.length || 0}
                  </span>
                </div>

                {call.objections_count > 0 && (
                  <div className="status-row warning">
                    <span className="status-label">⚠️ Objections:</span>
                    <span className="status-value">{call.objections_count}</span>
                  </div>
                )}
              </div>

              <div className="call-card-footer">
                <div className="audio-bars">
                  <div className="audio-bar">
                    <div className="audio-label">Agent</div>
                    <div className="audio-bar-container">
                      <div
                        className="audio-bar-fill agent"
                        style={{
                          width: `${(call.audio_metrics?.[call.audio_metrics.length - 1]?.agent_volume || 0) * 100}%`
                        }}
                      ></div>
                    </div>
                  </div>
                  <div className="audio-bar">
                    <div className="audio-label">Customer</div>
                    <div className="audio-bar-container">
                      <div
                        className="audio-bar-fill customer"
                        style={{
                          width: `${(call.audio_metrics?.[call.audio_metrics.length - 1]?.customer_volume || 0) * 100}%`
                        }}
                      ></div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default LiveCallMonitor;
