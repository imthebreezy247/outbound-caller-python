import React, { useState } from 'react';
import './ControlPanel.css';

function ControlPanel({ currentCall, onStartCall, onEndCall, onTransferCall }) {
  const [showNewCallForm, setShowNewCallForm] = useState(false);
  const [phoneNumber, setPhoneNumber] = useState('+1');
  const [customerName, setCustomerName] = useState('');
  const [transferTo, setTransferTo] = useState('');

  const handleStartCall = (e) => {
    e.preventDefault();
    if (phoneNumber && customerName && transferTo) {
      onStartCall(phoneNumber, customerName, transferTo);
      setShowNewCallForm(false);
      setPhoneNumber('+1');
      setCustomerName('');
      setTransferTo('');
    }
  };

  const handleTransfer = () => {
    const number = prompt('Enter transfer number (e.g., +19412314887):');
    if (number && currentCall) {
      onTransferCall(currentCall.call_id, number);
    }
  };

  return (
    <div className="card">
      <div className="card-header">
        <h2 className="card-title">
          <span className="icon">🎮</span>
          Control Panel
        </h2>
      </div>
      <div className="card-body control-panel">
        {!showNewCallForm ? (
          <button
            className="btn btn-primary btn-large"
            onClick={() => setShowNewCallForm(true)}
          >
            <span className="btn-icon">📞</span>
            Start New Call
          </button>
        ) : (
          <form onSubmit={handleStartCall} className="new-call-form">
            <div className="form-title">
              <span>📞</span>
              New Outbound Call
            </div>

            <div className="input-group">
              <label className="input-label">Customer Name</label>
              <input
                type="text"
                className="input"
                value={customerName}
                onChange={(e) => setCustomerName(e.target.value)}
                placeholder="e.g., Jayden Smith"
                required
              />
            </div>

            <div className="input-group">
              <label className="input-label">Phone Number</label>
              <input
                type="tel"
                className="input"
                value={phoneNumber}
                onChange={(e) => setPhoneNumber(e.target.value)}
                placeholder="+19415180701"
                required
              />
            </div>

            <div className="input-group">
              <label className="input-label">Transfer To (Max)</label>
              <input
                type="tel"
                className="input"
                value={transferTo}
                onChange={(e) => setTransferTo(e.target.value)}
                placeholder="+19412314887"
                required
              />
            </div>

            <div className="form-actions">
              <button type="submit" className="btn btn-success">
                <span className="btn-icon">✓</span>
                Start Call
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setShowNewCallForm(false)}
              >
                Cancel
              </button>
            </div>
          </form>
        )}

        {currentCall && (
          <div className="call-controls">
            <div className="controls-title">Active Call Controls</div>

            <div className="current-call-info">
              <div className="call-info-row">
                <span className="info-label">Customer:</span>
                <span className="info-value">{currentCall.customer_name}</span>
              </div>
              <div className="call-info-row">
                <span className="info-label">Number:</span>
                <span className="info-value">{currentCall.phone_number}</span>
              </div>
              <div className="call-info-row">
                <span className="info-label">Status:</span>
                <span className="info-value status-badge">{currentCall.status}</span>
              </div>
            </div>

            <div className="control-buttons">
              <button
                className="btn btn-warning btn-control"
                onClick={handleTransfer}
                disabled={currentCall.status === 'ended'}
              >
                <span className="btn-icon">🔄</span>
                Transfer
              </button>

              <button
                className="btn btn-secondary btn-control"
                disabled
                title="Mute coming soon"
              >
                <span className="btn-icon">🔇</span>
                Mute
              </button>

              <button
                className="btn btn-secondary btn-control"
                disabled
                title="Hold coming soon"
              >
                <span className="btn-icon">⏸️</span>
                Hold
              </button>

              <button
                className="btn btn-danger btn-control"
                onClick={() => onEndCall(currentCall.call_id)}
                disabled={currentCall.status === 'ended'}
              >
                <span className="btn-icon">❌</span>
                End Call
              </button>
            </div>
          </div>
        )}

        <div className="quick-actions">
          <div className="quick-actions-title">Quick Actions</div>

          <button className="btn btn-secondary btn-small" disabled>
            <span className="btn-icon">📋</span>
            Load Call List
          </button>

          <button className="btn btn-secondary btn-small" disabled>
            <span className="btn-icon">⏰</span>
            Schedule Calls
          </button>

          <button className="btn btn-secondary btn-small" disabled>
            <span className="btn-icon">📥</span>
            Import Contacts
          </button>
        </div>
      </div>
    </div>
  );
}

export default ControlPanel;
