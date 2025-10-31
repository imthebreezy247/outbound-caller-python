import React from 'react';
import './SentimentAnalysis.css';

function SentimentAnalysis({ call }) {
  if (!call) {
    return (
      <div className="card">
        <div className="card-header">
          <h2 className="card-title">
            <span className="icon">📊</span>
            Sentiment Analysis
          </h2>
        </div>
        <div className="card-body">
          <div className="empty-state">
            <div className="empty-icon">📈</div>
            <p>No sentiment data available</p>
          </div>
        </div>
      </div>
    );
  }

  const sentiment = call.sentiment_scores || { positive: 0, neutral: 100, negative: 0 };
  const total = sentiment.positive + sentiment.neutral + sentiment.negative;

  const positivePercent = (sentiment.positive / total) * 100;
  const neutralPercent = (sentiment.neutral / total) * 100;
  const negativePercent = (sentiment.negative / total) * 100;

  const getOverallSentiment = () => {
    if (positivePercent > 50) return { label: 'Positive', color: '#22c55e', emoji: '😊' };
    if (negativePercent > 50) return { label: 'Negative', color: '#ef4444', emoji: '😟' };
    return { label: 'Neutral', color: '#6b7280', emoji: '😐' };
  };

  const overall = getOverallSentiment();

  return (
    <div className="card">
      <div className="card-header">
        <h2 className="card-title">
          <span className="icon">📊</span>
          Sentiment Analysis
        </h2>
      </div>
      <div className="card-body">
        <div className="overall-sentiment">
          <div className="sentiment-emoji" style={{ color: overall.color }}>
            {overall.emoji}
          </div>
          <div className="sentiment-info">
            <div className="sentiment-label">Overall Sentiment</div>
            <div className="sentiment-value" style={{ color: overall.color }}>
              {overall.label}
            </div>
          </div>
        </div>

        <div className="sentiment-breakdown">
          <div className="sentiment-item positive">
            <div className="sentiment-header">
              <span className="sentiment-icon">😊</span>
              <span className="sentiment-name">Positive</span>
              <span className="sentiment-percent">{positivePercent.toFixed(1)}%</span>
            </div>
            <div className="sentiment-bar">
              <div
                className="sentiment-bar-fill"
                style={{ width: `${positivePercent}%`, background: '#22c55e' }}
              ></div>
            </div>
          </div>

          <div className="sentiment-item neutral">
            <div className="sentiment-header">
              <span className="sentiment-icon">😐</span>
              <span className="sentiment-name">Neutral</span>
              <span className="sentiment-percent">{neutralPercent.toFixed(1)}%</span>
            </div>
            <div className="sentiment-bar">
              <div
                className="sentiment-bar-fill"
                style={{ width: `${neutralPercent}%`, background: '#6b7280' }}
              ></div>
            </div>
          </div>

          <div className="sentiment-item negative">
            <div className="sentiment-header">
              <span className="sentiment-icon">😟</span>
              <span className="sentiment-name">Negative</span>
              <span className="sentiment-percent">{negativePercent.toFixed(1)}%</span>
            </div>
            <div className="sentiment-bar">
              <div
                className="sentiment-bar-fill"
                style={{ width: `${negativePercent}%`, background: '#ef4444' }}
              ></div>
            </div>
          </div>
        </div>

        <div className="sentiment-insights">
          <div className="insight-title">Key Insights</div>

          <div className="insight-item">
            <span className="insight-icon">💬</span>
            <div className="insight-content">
              <div className="insight-label">Messages Analyzed</div>
              <div className="insight-value">{call.transcript?.length || 0}</div>
            </div>
          </div>

          <div className="insight-item">
            <span className="insight-icon">⚠️</span>
            <div className="insight-content">
              <div className="insight-label">Objections Detected</div>
              <div className="insight-value">{call.objections_count || 0}</div>
            </div>
          </div>

          <div className="insight-item">
            <span className="insight-icon">❓</span>
            <div className="insight-content">
              <div className="insight-label">Questions Asked</div>
              <div className="insight-value">{call.questions_asked || 0}</div>
            </div>
          </div>

          {call.objections && call.objections.length > 0 && (
            <div className="objections-list">
              <div className="objections-title">Recent Objections:</div>
              {call.objections.slice(-3).map((objection, index) => (
                <div key={index} className="objection-tag">
                  {objection}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default SentimentAnalysis;
