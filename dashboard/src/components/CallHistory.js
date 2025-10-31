import React, { useState } from 'react';
import './CallHistory.css';

function CallHistory({ callHistory, onSelectCall }) {
  const [filter, setFilter] = useState('all'); // all, transferred, rejected, failed
  const [searchTerm, setSearchTerm] = useState('');

  const formatDuration = (seconds) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const formatDate = (timestamp) => {
    const date = new Date(timestamp * 1000);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const getOutcomeColor = (outcome) => {
    const colors = {
      transferred: '#22c55e',
      scheduled: '#3b82f6',
      rejected: '#ef4444',
      hung_up: '#6b7280',
      failed: '#ef4444'
    };
    return colors[outcome] || '#6b7280';
  };

  const getOutcomeIcon = (outcome) => {
    const icons = {
      transferred: '✅',
      scheduled: '📅',
      rejected: '❌',
      hung_up: '📞',
      failed: '⚠️'
    };
    return icons[outcome] || '📞';
  };

  const filteredCalls = callHistory.filter((call) => {
    // Filter by outcome
    if (filter !== 'all' && call.outcome !== filter) {
      return false;
    }

    // Filter by search term
    if (searchTerm) {
      const search = searchTerm.toLowerCase();
      return (
        call.customer_name?.toLowerCase().includes(search) ||
        call.phone_number?.includes(search)
      );
    }

    return true;
  });

  const stats = {
    total: callHistory.length,
    transferred: callHistory.filter(c => c.outcome === 'transferred').length,
    rejected: callHistory.filter(c => c.outcome === 'rejected').length,
    failed: callHistory.filter(c => c.outcome === 'failed').length
  };

  return (
    <div className="call-history-container">
      <div className="history-header">
        <h1 className="history-title">Call History</h1>
        <div className="history-stats">
          <div className="history-stat">
            <span className="stat-label">Total</span>
            <span className="stat-value">{stats.total}</span>
          </div>
          <div className="history-stat success">
            <span className="stat-label">Transferred</span>
            <span className="stat-value">{stats.transferred}</span>
          </div>
          <div className="history-stat failed">
            <span className="stat-label">Rejected</span>
            <span className="stat-value">{stats.rejected}</span>
          </div>
        </div>
      </div>

      <div className="history-filters">
        <div className="filter-group">
          <button
            className={`filter-btn ${filter === 'all' ? 'active' : ''}`}
            onClick={() => setFilter('all')}
          >
            All
          </button>
          <button
            className={`filter-btn ${filter === 'transferred' ? 'active' : ''}`}
            onClick={() => setFilter('transferred')}
          >
            ✅ Transferred
          </button>
          <button
            className={`filter-btn ${filter === 'rejected' ? 'active' : ''}`}
            onClick={() => setFilter('rejected')}
          >
            ❌ Rejected
          </button>
          <button
            className={`filter-btn ${filter === 'failed' ? 'active' : ''}`}
            onClick={() => setFilter('failed')}
          >
            ⚠️ Failed
          </button>
        </div>

        <div className="search-box">
          <span className="search-icon">🔍</span>
          <input
            type="text"
            className="search-input"
            placeholder="Search by name or number..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
      </div>

      <div className="history-list">
        {filteredCalls.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">📭</div>
            <h3>No calls found</h3>
            <p>
              {searchTerm
                ? 'Try adjusting your search or filters'
                : 'Start making calls to see history here'}
            </p>
          </div>
        ) : (
          filteredCalls.map((call) => (
            <div
              key={call.call_id}
              className="history-item"
              onClick={() => onSelectCall(call)}
            >
              <div className="history-item-left">
                <div
                  className="outcome-badge"
                  style={{ color: getOutcomeColor(call.outcome) }}
                >
                  {getOutcomeIcon(call.outcome)}
                </div>
                <div className="history-item-info">
                  <div className="history-customer">{call.customer_name}</div>
                  <div className="history-phone">{call.phone_number}</div>
                </div>
              </div>

              <div className="history-item-center">
                <div className="history-detail">
                  <span className="detail-label">Duration:</span>
                  <span className="detail-value">{formatDuration(call.duration)}</span>
                </div>
                <div className="history-detail">
                  <span className="detail-label">Messages:</span>
                  <span className="detail-value">{call.transcript?.length || 0}</span>
                </div>
                {call.objections_count > 0 && (
                  <div className="history-detail warning">
                    <span className="detail-label">Objections:</span>
                    <span className="detail-value">{call.objections_count}</span>
                  </div>
                )}
              </div>

              <div className="history-item-right">
                <div
                  className="outcome-tag"
                  style={{
                    background: `${getOutcomeColor(call.outcome)}22`,
                    color: getOutcomeColor(call.outcome)
                  }}
                >
                  {call.outcome?.replace('_', ' ').toUpperCase() || 'UNKNOWN'}
                </div>
                <div className="history-date">{formatDate(call.start_time)}</div>
                {call.recording_url && (
                  <button className="play-btn" title="Play recording">
                    ▶️
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default CallHistory;
