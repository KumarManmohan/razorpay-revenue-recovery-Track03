import React from 'react';
import { AlertTriangle, CheckCircle2, UserCheck, TrendingUp } from 'lucide-react';

export default function StatsOverview({ stats, isLoading }) {
  const formatCurrency = (val) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(val || 0);
  };

  const formatCompactINR = (amount) => {
    if (!amount || amount <= 0) return null;
    if (amount >= 100000) {
      const lakhs = amount / 100000;
      return `₹${lakhs % 1 === 0 ? lakhs : lakhs.toFixed(2).replace(/\.?0+$/, '')}L`;
    }
    if (amount >= 1000) {
      const thousands = amount / 1000;
      return `₹${thousands % 1 === 0 ? thousands : thousands.toFixed(1).replace(/\.?0+$/, '')}K`;
    }
    return `₹${Math.round(amount)}`;
  };

  const renderRiskProvenanceChips = () => {
    const p = stats?.risk_provenance;
    if (!p) return null;
    const chips = [];
    if (p.demo > 0) {
      chips.push(
        <span key="demo" className="kpi-chip kpi-chip--demo">
          <span className="chip-label">Demo</span>
          <span className="chip-value">{formatCompactINR(p.demo)}</span>
        </span>
      );
    }
    if (p.internal_test > 0) {
      chips.push(
        <span key="internal_test" className="kpi-chip kpi-chip--internal">
          <span className="chip-label">Internal Test</span>
          <span className="chip-value">{formatCompactINR(p.internal_test)}</span>
        </span>
      );
    }
    if (p.razorpay_test > 0) {
      chips.push(
        <span key="razorpay_test" className="kpi-chip kpi-chip--razorpay">
          <span className="chip-label">Razorpay Test</span>
          <span className="chip-value">{formatCompactINR(p.razorpay_test)}</span>
        </span>
      );
    }
    if (chips.length === 0) return null;
    return (
      <div className="kpi-provenance-container">
        <span className="kpi-provenance-header">Source breakdown</span>
        <div className="kpi-provenance-chips">{chips}</div>
      </div>
    );
  };

  const renderRecoveredProvenanceChips = () => {
    const p = stats?.recovered_provenance;
    const totalRec = stats?.recovered_revenue || 0;
    if (!p) return null;
    const chips = [];
    if (p.razorpay_test > 0) {
      const pct = totalRec > 0 ? ` · ${((p.razorpay_test / totalRec) * 100).toFixed(1)}%` : '';
      chips.push(
        <span key="razorpay_test" className="kpi-chip kpi-chip--razorpay kpi-chip--featured">
          <span className="chip-label">Razorpay Test</span>
          <span className="chip-value">{formatCompactINR(p.razorpay_test)}{pct}</span>
        </span>
      );
    }
    if (p.demo > 0) {
      chips.push(
        <span key="demo" className="kpi-chip kpi-chip--demo">
          <span className="chip-label">Demo</span>
          <span className="chip-value">{formatCompactINR(p.demo)}</span>
        </span>
      );
    }
    if (p.internal_test > 0) {
      chips.push(
        <span key="internal_test" className="kpi-chip kpi-chip--internal">
          <span className="chip-label">Internal Test</span>
          <span className="chip-value">{formatCompactINR(p.internal_test)}</span>
        </span>
      );
    }
    if (chips.length === 0) return null;
    return (
      <div className="kpi-provenance-container">
        <span className="kpi-provenance-header">Source breakdown</span>
        <div className="kpi-provenance-chips">{chips}</div>
      </div>
    );
  };

  const pendingCount = stats?.pending_approvals || 0;
  const hasPending = pendingCount > 0;

  return (
    <div className="kpi-grid" role="region" aria-label="Key performance indicators">
      {/* 1. Revenue Currently at Risk */}
      <div className="kpi-card risk" aria-label="Revenue currently at risk">
        <div className="kpi-top">
          <span className="kpi-label">Revenue Currently at Risk</span>
          <div className="kpi-icon" aria-hidden="true">
            <AlertTriangle size={20} />
          </div>
        </div>
        <div className="kpi-value">
          {isLoading ? '...' : formatCurrency(stats?.total_revenue_at_risk)}
        </div>
        <div className="kpi-subtext">
          <span>Unresolved failed payment obligations</span>
          {!isLoading && renderRiskProvenanceChips()}
        </div>
      </div>

      {/* 2. Recovered Revenue */}
      <div className="kpi-card recovered" aria-label="Recovered revenue">
        <div className="kpi-top">
          <span className="kpi-label">Recovered Revenue</span>
          <div className="kpi-icon" aria-hidden="true">
            <CheckCircle2 size={20} />
          </div>
        </div>
        <div className="kpi-value kpi-value--prominent">
          {isLoading ? '...' : formatCurrency(stats?.recovered_revenue)}
        </div>
        <div className="kpi-subtext">
          <span>Confirmed captured and reconciled payments</span>
          {!isLoading && renderRecoveredProvenanceChips()}
        </div>
      </div>

      {/* 3. Action Needed / No Action Needed */}
      <div
        className={`kpi-card pending ${hasPending ? 'pending--active' : 'pending--calm'}`}
        aria-label={hasPending ? `${pendingCount} cases awaiting review` : 'No action needed'}
      >
        <div className="kpi-top">
          <span className="kpi-label">
            {hasPending ? 'Action Needed' : 'No Action Needed'}
          </span>
          <div className="kpi-icon" aria-hidden="true">
            <UserCheck size={20} />
          </div>
        </div>
        <div className="kpi-value">
          {isLoading ? '...' : pendingCount}
        </div>
        <div className="kpi-subtext">
          <span>
            {hasPending
              ? 'Cases awaiting merchant review'
              : 'No recovery cases currently awaiting review'}
          </span>
        </div>
      </div>

      {/* 4. Recovery Rate */}
      <div className="kpi-card rate" aria-label="Recovery rate">
        <div className="kpi-top">
          <span className="kpi-label">Recovery Rate</span>
          <div className="kpi-icon" aria-hidden="true">
            <TrendingUp size={20} />
          </div>
        </div>
        <div className="kpi-value">
          {isLoading ? '...' : `${(stats?.recovery_rate_percentage || 0).toFixed(1)}%`}
        </div>
        <div className="kpi-subtext">
          <span>Recovered vs. processed exposure</span>
        </div>
      </div>
    </div>
  );
}
