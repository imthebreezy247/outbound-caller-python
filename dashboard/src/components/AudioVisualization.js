import React, { useEffect, useRef, useState } from 'react';
import './AudioVisualization.css';

function AudioVisualization({ call }) {
  const canvasRef = useRef(null);
  const [voiceActivity, setVoiceActivity] = useState({ agent: false, customer: false });

  useEffect(() => {
    if (!call || !canvasRef.current) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    const metrics = call.audio_metrics || [];

    // Set canvas size
    canvas.width = canvas.offsetWidth;
    canvas.height = canvas.offsetHeight;

    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (metrics.length === 0) return;

    const width = canvas.width;
    const height = canvas.height;
    const barWidth = width / Math.min(metrics.length, 50);
    const recentMetrics = metrics.slice(-50);

    // Update voice activity
    const latest = metrics[metrics.length - 1];
    if (latest) {
      setVoiceActivity({
        agent: latest.agent_speaking,
        customer: latest.customer_speaking
      });
    }

    // Draw agent waveform (top half)
    ctx.fillStyle = '#3b82f6';
    recentMetrics.forEach((metric, index) => {
      const x = index * barWidth;
      const barHeight = (metric.agent_volume * height) / 2;
      const y = height / 2 - barHeight;

      ctx.globalAlpha = metric.agent_speaking ? 1 : 0.3;
      ctx.fillRect(x, y, barWidth - 2, barHeight);
    });

    // Draw center line
    ctx.strokeStyle = '#374151';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, height / 2);
    ctx.lineTo(width, height / 2);
    ctx.stroke();

    // Draw customer waveform (bottom half)
    ctx.fillStyle = '#22c55e';
    recentMetrics.forEach((metric, index) => {
      const x = index * barWidth;
      const barHeight = (metric.customer_volume * height) / 2;
      const y = height / 2;

      ctx.globalAlpha = metric.customer_speaking ? 1 : 0.3;
      ctx.fillRect(x, y, barWidth - 2, barHeight);
    });

    ctx.globalAlpha = 1;

  }, [call]);

  const getAudioLevel = (metrics, speaker) => {
    if (!metrics || metrics.length === 0) return 0;
    const latest = metrics[metrics.length - 1];
    return speaker === 'agent' ? latest?.agent_volume || 0 : latest?.customer_volume || 0;
  };

  const formatVolume = (volume) => {
    return `${Math.round(volume * 100)}%`;
  };

  if (!call) {
    return (
      <div className="card">
        <div className="card-header">
          <h2 className="card-title">
            <span className="icon">🎵</span>
            Audio Visualization
          </h2>
        </div>
        <div className="card-body">
          <div className="empty-state">
            <div className="empty-icon">🔇</div>
            <p>No audio data available</p>
          </div>
        </div>
      </div>
    );
  }

  const agentLevel = getAudioLevel(call.audio_metrics, 'agent');
  const customerLevel = getAudioLevel(call.audio_metrics, 'customer');
  const latestMetrics = call.audio_metrics?.[call.audio_metrics.length - 1];
  const noiseLevel = latestMetrics?.background_noise_level || 0;

  return (
    <div className="card">
      <div className="card-header">
        <h2 className="card-title">
          <span className="icon">🎵</span>
          Audio Visualization
        </h2>
        <div className="audio-controls">
          <button className="btn-icon" title="Mute/Unmute">
            🔊
          </button>
          <button className="btn-icon" title="Record">
            ⏺️
          </button>
        </div>
      </div>
      <div className="card-body">
        <div className="voice-activity">
          <div className={`voice-indicator ${voiceActivity.agent ? 'active' : ''}`}>
            <div className="voice-avatar">🤖</div>
            <div className="voice-info">
              <div className="voice-name">Agent</div>
              <div className="voice-level">{formatVolume(agentLevel)}</div>
            </div>
            {voiceActivity.agent && (
              <div className="speaking-indicator">
                <span className="speaking-dot"></span>
                Speaking
              </div>
            )}
          </div>

          <div className={`voice-indicator ${voiceActivity.customer ? 'active' : ''}`}>
            <div className="voice-avatar">👤</div>
            <div className="voice-info">
              <div className="voice-name">Customer</div>
              <div className="voice-level">{formatVolume(customerLevel)}</div>
            </div>
            {voiceActivity.customer && (
              <div className="speaking-indicator">
                <span className="speaking-dot"></span>
                Speaking
              </div>
            )}
          </div>
        </div>

        <div className="waveform-container">
          <canvas ref={canvasRef} className="waveform-canvas"></canvas>
        </div>

        <div className="audio-stats">
          <div className="audio-stat">
            <span className="stat-label">Background Noise</span>
            <div className="stat-bar">
              <div
                className="stat-bar-fill noise"
                style={{ width: `${noiseLevel * 100}%` }}
              ></div>
            </div>
            <span className="stat-value">{formatVolume(noiseLevel)}</span>
          </div>

          <div className="audio-stat">
            <span className="stat-label">Connection Quality</span>
            <div className="stat-bar">
              <div
                className="stat-bar-fill quality"
                style={{ width: '95%' }}
              ></div>
            </div>
            <span className="stat-value">95%</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default AudioVisualization;
