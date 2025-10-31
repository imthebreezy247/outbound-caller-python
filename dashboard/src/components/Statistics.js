import React from 'react';
import './Statistics.css';

function Statistics({ statistics }) {
  const stats = statistics || {
    total_calls: 0,
    active_calls: 0,
    successful_transfers: 0,
    average_duration: 0,
    success_rate: 0,
    total_duration: 0
  };

  const formatDuration = (seconds) => {
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    if (hours > 0) {
      return `${hours}h ${mins}m`;
    }
    if (mins > 0) {
      return `${mins}m ${secs}s`;
    }
    return `${secs}s`;
  };

  const statCards = [
    {
      label: 'Total Calls',
      value: stats.total_calls,
      icon: '📞',
      color: '#3b82f6',
      trend: '+12%'
    },
    {
      label: 'Success Rate',
      value: `${stats.success_rate.toFixed(1)}%`,
      icon: '✅',
      color: '#22c55e',
      trend: '+5%'
    },
    {
      label: 'Avg Duration',
      value: formatDuration(stats.average_duration),
      icon: '⏱️',
      color: '#f59e0b',
      trend: '-8%'
    },
    {
      label: 'Transfers',
      value: stats.successful_transfers,
      icon: '🔄',
      color: '#8b5cf6',
      trend: '+15%'
    }
  ];

  return (
    <div className="card">
      <div className="card-header">
        <h2 className="card-title">
          <span className="icon">📈</span>
          Performance Statistics
        </h2>
      </div>
      <div className="card-body">
        <div className="stats-grid">
          {statCards.map((stat, index) => (
            <div key={index} className="stat-card" style={{ borderColor: stat.color }}>
              <div className="stat-icon" style={{ color: stat.color }}>
                {stat.icon}
              </div>
              <div className="stat-content">
                <div className="stat-label">{stat.label}</div>
                <div className="stat-value">{stat.value}</div>
                {stat.trend && (
                  <div
                    className={`stat-trend ${stat.trend.startsWith('+') ? 'up' : 'down'}`}
                  >
                    {stat.trend.startsWith('+') ? '↗' : '↘'} {stat.trend}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="detailed-stats">
          <div className="detailed-stat">
            <div className="detailed-stat-header">
              <span className="detailed-stat-icon">🎯</span>
              <span className="detailed-stat-label">Active Calls</span>
            </div>
            <div className="detailed-stat-value">{stats.active_calls}</div>
          </div>

          <div className="detailed-stat">
            <div className="detailed-stat-header">
              <span className="detailed-stat-icon">⏰</span>
              <span className="detailed-stat-label">Total Talk Time</span>
            </div>
            <div className="detailed-stat-value">{formatDuration(stats.total_duration)}</div>
          </div>

          <div className="detailed-stat">
            <div className="detailed-stat-header">
              <span className="detailed-stat-icon">💰</span>
              <span className="detailed-stat-label">Est. Revenue</span>
            </div>
            <div className="detailed-stat-value">
              ${(stats.successful_transfers * 450).toLocaleString()}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Statistics;
