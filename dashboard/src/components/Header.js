import React from 'react';
import './Header.css';

function Header({ connected, activeCallsCount, onTabChange, activeTab }) {
  return (
    <header className="header">
      <div className="header-content">
        <div className="header-left">
          <div className="logo">
            <div className="logo-icon">📞</div>
            <div>
              <h1 className="logo-text">Outbound Caller</h1>
              <p className="logo-subtitle">AI Agent Dashboard</p>
            </div>
          </div>
        </div>

        <div className="header-center">
          <nav className="nav-tabs">
            <button
              className={`nav-tab ${activeTab === 'live' ? 'active' : ''}`}
              onClick={() => onTabChange('live')}
            >
              <span className="nav-tab-icon">🔴</span>
              Live Monitoring
            </button>
            <button
              className={`nav-tab ${activeTab === 'history' ? 'active' : ''}`}
              onClick={() => onTabChange('history')}
            >
              <span className="nav-tab-icon">📋</span>
              Call History
            </button>
            <button
              className={`nav-tab ${activeTab === 'config' ? 'active' : ''}`}
              onClick={() => onTabChange('config')}
            >
              <span className="nav-tab-icon">⚙️</span>
              Configuration
            </button>
          </nav>
        </div>

        <div className="header-right">
          <div className="status-container">
            <div className={`connection-status ${connected ? 'connected' : 'disconnected'}`}>
              <div className="status-dot"></div>
              <span>{connected ? 'Connected' : 'Disconnected'}</span>
            </div>

            {activeCallsCount > 0 && (
              <div className="active-calls-badge">
                <span className="badge-icon">📞</span>
                <span>{activeCallsCount} Active</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}

export default Header;
