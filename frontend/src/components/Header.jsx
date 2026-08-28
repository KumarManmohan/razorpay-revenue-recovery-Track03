import React from 'react';
import { RefreshCw, CheckCircle, AlertCircle, ShieldCheck } from 'lucide-react';

export default function Header({ onRefresh, isRefreshing, healthStatus, testRazorpayStatus }) {
  return (
    <header className="top-header">
      <div className="header-title">
        <h1>Autonomous Revenue Recovery Agent</h1>
        <p>Real-time payment risk detection, bounded AI decisions, and human-in-the-loop recovery</p>
      </div>

      <div className="header-actions">
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          fontSize: '0.76rem',
          color: 'var(--text-secondary)',
          background: 'var(--card-bg-light)',
          padding: '6px 12px',
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border-color)'
        }}>
          <ShieldCheck size={15} color="var(--success)" />
          <span>Data Source: <strong>Razorpay Test Mode • SQLite Recovery Ledger</strong></span>
          <span style={{ color: 'var(--border-color)', margin: '0 2px' }}>•</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', color: 'var(--text-secondary)' }}>
            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--success)', display: 'inline-block' }}></span>
            Auto-refresh (5s)
          </span>
        </div>

        <button

          className="btn btn-outline btn-sm"
          onClick={onRefresh}
          disabled={isRefreshing}
          title="Refresh statistics and cases"
        >
          <RefreshCw size={14} className={isRefreshing ? 'spinner' : ''} />
          <span>{isRefreshing ? 'Refreshing...' : 'Refresh Data'}</span>
        </button>
      </div>
    </header>
  );
}
