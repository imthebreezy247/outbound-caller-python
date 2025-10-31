import React, { useState } from 'react';
import './ConfigPanel.css';

function ConfigPanel() {
  const [config, setConfig] = useState({
    // Agent Settings
    agentName: 'John',
    agentVoice: 'echo',
    agentTemperature: 0.8,
    agentPersonality: 'energetic',

    // Human-like Behavior
    useFillers: true,
    fillerFrequency: 'medium',
    addPauses: true,
    pauseDuration: 'natural',
    varyTone: true,
    mirrorEnergy: true,

    // Response Settings
    maxResponseTime: 3,
    interruptionHandling: 'allow',
    contextMemory: true,
    emotionalAdaptation: true,

    // Call Settings
    autoAnswer: true,
    recordCalls: true,
    transcribeRealtime: true,
    sentimentAnalysis: true,
    objectionDetection: true,

    // Advanced
    debugMode: false,
    logLevel: 'info',
    webhookUrl: '',
    apiEndpoint: 'http://localhost:8000'
  });

  const handleChange = (key, value) => {
    setConfig(prev => ({ ...prev, [key]: value }));
  };

  const handleSave = () => {
    console.log('Saving configuration:', config);
    alert('Configuration saved successfully! (This is a demo - actual save coming soon)');
  };

  const handleReset = () => {
    if (window.confirm('Reset all settings to defaults?')) {
      // Reset logic here
      alert('Settings reset to defaults!');
    }
  };

  return (
    <div className="config-container">
      <div className="config-header">
        <h1 className="config-title">⚙️ Agent Configuration</h1>
        <div className="config-actions">
          <button className="btn btn-secondary" onClick={handleReset}>
            🔄 Reset
          </button>
          <button className="btn btn-success" onClick={handleSave}>
            💾 Save Changes
          </button>
        </div>
      </div>

      <div className="config-grid">
        {/* Agent Settings */}
        <div className="config-section">
          <div className="section-header">
            <span className="section-icon">🤖</span>
            <h2 className="section-title">Agent Settings</h2>
          </div>
          <div className="section-body">
            <div className="config-item">
              <label className="config-label">Agent Name</label>
              <input
                type="text"
                className="input"
                value={config.agentName}
                onChange={(e) => handleChange('agentName', e.target.value)}
              />
              <p className="config-hint">The name your agent uses during calls</p>
            </div>

            <div className="config-item">
              <label className="config-label">Voice Model</label>
              <select
                className="input"
                value={config.agentVoice}
                onChange={(e) => handleChange('agentVoice', e.target.value)}
              >
                <option value="echo">Echo (Male, Friendly)</option>
                <option value="alloy">Alloy (Neutral)</option>
                <option value="fable">Fable (British)</option>
                <option value="onyx">Onyx (Deep, Authoritative)</option>
                <option value="nova">Nova (Female, Warm)</option>
                <option value="shimmer">Shimmer (Female, Energetic)</option>
              </select>
            </div>

            <div className="config-item">
              <label className="config-label">
                Temperature: {config.agentTemperature}
              </label>
              <input
                type="range"
                className="slider"
                min="0"
                max="1"
                step="0.1"
                value={config.agentTemperature}
                onChange={(e) => handleChange('agentTemperature', parseFloat(e.target.value))}
              />
              <p className="config-hint">Higher = more creative, Lower = more focused</p>
            </div>

            <div className="config-item">
              <label className="config-label">Personality Style</label>
              <div className="radio-group">
                {['professional', 'friendly', 'energetic', 'persistent'].map(style => (
                  <label key={style} className="radio-label">
                    <input
                      type="radio"
                      name="personality"
                      value={style}
                      checked={config.agentPersonality === style}
                      onChange={(e) => handleChange('agentPersonality', e.target.value)}
                    />
                    <span className="radio-text">{style.charAt(0).toUpperCase() + style.slice(1)}</span>
                  </label>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Human-like Behavior */}
        <div className="config-section">
          <div className="section-header">
            <span className="section-icon">💭</span>
            <h2 className="section-title">Human-like Behavior</h2>
          </div>
          <div className="section-body">
            <div className="config-item">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  className="toggle"
                  checked={config.useFillers}
                  onChange={(e) => handleChange('useFillers', e.target.checked)}
                />
                <span className="toggle-slider"></span>
                <span>Use Natural Fillers</span>
              </label>
              <p className="config-hint">Add "um", "like", "you know" for realism</p>
            </div>

            <div className="config-item">
              <label className="config-label">Filler Frequency</label>
              <select
                className="input"
                value={config.fillerFrequency}
                onChange={(e) => handleChange('fillerFrequency', e.target.value)}
                disabled={!config.useFillers}
              >
                <option value="low">Low (Subtle)</option>
                <option value="medium">Medium (Natural)</option>
                <option value="high">High (Very casual)</option>
              </select>
            </div>

            <div className="config-item">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  className="toggle"
                  checked={config.addPauses}
                  onChange={(e) => handleChange('addPauses', e.target.checked)}
                />
                <span className="toggle-slider"></span>
                <span>Natural Pauses</span>
              </label>
              <p className="config-hint">Add breathing pauses between sentences</p>
            </div>

            <div className="config-item">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  className="toggle"
                  checked={config.varyTone}
                  onChange={(e) => handleChange('varyTone', e.target.checked)}
                />
                <span className="toggle-slider"></span>
                <span>Vary Tone & Emotion</span>
              </label>
              <p className="config-hint">Adjust tone based on conversation context</p>
            </div>

            <div className="config-item">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  className="toggle"
                  checked={config.mirrorEnergy}
                  onChange={(e) => handleChange('mirrorEnergy', e.target.checked)}
                />
                <span className="toggle-slider"></span>
                <span>Mirror Customer Energy</span>
              </label>
              <p className="config-hint">Match the customer's speaking energy</p>
            </div>
          </div>
        </div>

        {/* Call Settings */}
        <div className="config-section">
          <div className="section-header">
            <span className="section-icon">📞</span>
            <h2 className="section-title">Call Settings</h2>
          </div>
          <div className="section-body">
            <div className="config-item">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  className="toggle"
                  checked={config.autoAnswer}
                  onChange={(e) => handleChange('autoAnswer', e.target.checked)}
                />
                <span className="toggle-slider"></span>
                <span>Auto-answer Incoming</span>
              </label>
            </div>

            <div className="config-item">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  className="toggle"
                  checked={config.recordCalls}
                  onChange={(e) => handleChange('recordCalls', e.target.checked)}
                />
                <span className="toggle-slider"></span>
                <span>Record All Calls</span>
              </label>
            </div>

            <div className="config-item">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  className="toggle"
                  checked={config.transcribeRealtime}
                  onChange={(e) => handleChange('transcribeRealtime', e.target.checked)}
                />
                <span className="toggle-slider"></span>
                <span>Real-time Transcription</span>
              </label>
            </div>

            <div className="config-item">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  className="toggle"
                  checked={config.sentimentAnalysis}
                  onChange={(e) => handleChange('sentimentAnalysis', e.target.checked)}
                />
                <span className="toggle-slider"></span>
                <span>Sentiment Analysis</span>
              </label>
            </div>

            <div className="config-item">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  className="toggle"
                  checked={config.objectionDetection}
                  onChange={(e) => handleChange('objectionDetection', e.target.checked)}
                />
                <span className="toggle-slider"></span>
                <span>Objection Detection</span>
              </label>
            </div>
          </div>
        </div>

        {/* Advanced Settings */}
        <div className="config-section">
          <div className="section-header">
            <span className="section-icon">🔧</span>
            <h2 className="section-title">Advanced Settings</h2>
          </div>
          <div className="section-body">
            <div className="config-item">
              <label className="config-label">API Endpoint</label>
              <input
                type="url"
                className="input"
                value={config.apiEndpoint}
                onChange={(e) => handleChange('apiEndpoint', e.target.value)}
                placeholder="http://localhost:8000"
              />
            </div>

            <div className="config-item">
              <label className="config-label">Webhook URL (Optional)</label>
              <input
                type="url"
                className="input"
                value={config.webhookUrl}
                onChange={(e) => handleChange('webhookUrl', e.target.value)}
                placeholder="https://your-webhook.com/events"
              />
              <p className="config-hint">Receive call events and transcripts</p>
            </div>

            <div className="config-item">
              <label className="config-label">Log Level</label>
              <select
                className="input"
                value={config.logLevel}
                onChange={(e) => handleChange('logLevel', e.target.value)}
              >
                <option value="debug">Debug (Verbose)</option>
                <option value="info">Info (Normal)</option>
                <option value="warning">Warning (Important only)</option>
                <option value="error">Error (Errors only)</option>
              </select>
            </div>

            <div className="config-item">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  className="toggle"
                  checked={config.debugMode}
                  onChange={(e) => handleChange('debugMode', e.target.checked)}
                />
                <span className="toggle-slider"></span>
                <span>Debug Mode</span>
              </label>
              <p className="config-hint">Show detailed logs in console</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ConfigPanel;
