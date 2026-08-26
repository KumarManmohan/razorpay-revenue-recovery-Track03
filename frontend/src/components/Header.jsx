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
