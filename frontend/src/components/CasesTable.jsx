import React, { useState } from 'react';
import { Search, Sparkles, CheckCircle, Clock, AlertTriangle, XCircle } from 'lucide-react';

export default function CasesTable({ cases, isLoading, onSelectCase }) {
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');

  const filteredCases = cases.filter((item) => {
    // Search match
    const searchLower = searchTerm.toLowerCase();
    const matchesSearch =
      !searchTerm ||
      item.id?.toLowerCase().includes(searchLower) ||
      item.payment_id?.toLowerCase().includes(searchLower) ||
      item.error_description?.toLowerCase().includes(searchLower) ||
      item.decision_action?.toLowerCase().includes(searchLower);

    // Status filter match
    if (!matchesSearch) return false;

    if (statusFilter === 'ALL') return true;
    if (statusFilter === 'RECOVERED') {
      return item.execution_status === 'recovered';
    }
    if (statusFilter === 'APPROVAL') {
      return item.requires_human_approval === 1 || item.execution_status === 'approval_required';
    }
    if (statusFilter === 'LINK_SENT') {
      return item.execution_status === 'executed';
    }
    if (statusFilter === 'EXHAUSTED') {
      return item.execution_status === 'exhausted';
    }
    if (statusFilter === 'AT_RISK') {
      return item.risk_status === 'at_risk' && item.execution_status !== 'executed' && item.execution_status !== 'recovered';
    }
    if (statusFilter === 'REJECTED') {
      return item.execution_status === 'rejected';
    }
    return true;
  });

  const getActionMerchantLabel = (action, category, execStatus) => {
    switch (action) {
      case 'SEND_PAYMENT_LINK':
        return 'Send Recovery Link';
      case 'WAIT':
        return 'Deferred Hold';
      case 'NO_ACTION':
        if (category === 'FRAUD_OR_SECURITY') return 'Recovery Blocked';
        if (execStatus === 'exhausted') return 'Recovery Stopped';
        return 'Recovery Blocked';
      case 'INVESTIGATE':
        return 'Investigation Needed';
      case 'SEND_INVOICE':
        return 'Advisory: Invoice';
      default:
        return action || 'Investigate';
    }
  };

  const getStatusBadge = (item) => {
    const execStatus = item.execution_status;
    const reqApproval = item.requires_human_approval === 1;

    if (execStatus === 'recovered') {
      return (
        <span className="badge badge-executed">
          <CheckCircle size={12} /> Recovered
        </span>
      );
    }
    if (execStatus === 'exhausted') {
      return (
        <span className="badge badge-rejected">
          <AlertTriangle size={12} /> Recovery Stopped
        </span>
      );
    }
    if (execStatus === 'executed') {
      const isPreserved = Boolean(
        item.original_payment_link_id &&
        item.original_payment_link_id === item.payment_link_id
      );
      return (
        <span className="badge badge-executed">
          <CheckCircle size={12} /> {isPreserved ? 'Link Preserved' : 'Link Sent'}
        </span>
      );
    }
    if (execStatus === 'rejected') {
      return (
        <span className="badge badge-rejected">
          <XCircle size={12} /> Rejected
        </span>
      );
    }
    if (reqApproval || execStatus === 'approval_required') {
      return (
        <span className="badge badge-approval">
          <Clock size={12} /> Awaiting Review
        </span>
      );
    }
    if (item.risk_status === 'at_risk') {
      return (
        <span className="badge badge-approval">
          <AlertTriangle size={12} /> At Risk
        </span>
      );
    }
    return <span className="badge badge-pending">Pending</span>;
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '-';
    try {
      const d = new Date(dateStr);
      return d.toLocaleDateString('en-IN', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return dateStr;
    }
  };

  return (
    <div className="section-card">
      <div className="section-header">
        <div>
          <h3>Revenue Risk & Recovery Cases</h3>
          <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            Showing {filteredCases.length} of {cases.length} cases
          </p>
        </div>

        <div className="table-controls">
          <div className="search-input-wrapper">
            <Search size={15} />
            <input
              type="text"
              placeholder="Search Case or Payment ID..."
              className="search-input"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>

          <div className="filter-pills">
            <button
              className={`filter-pill ${statusFilter === 'ALL' ? 'active' : ''}`}
              onClick={() => setStatusFilter('ALL')}
            >
              All
            </button>
            <button
              className={`filter-pill ${statusFilter === 'RECOVERED' ? 'active' : ''}`}
              onClick={() => setStatusFilter('RECOVERED')}
            >
              Recovered
            </button>
            <button
              className={`filter-pill ${statusFilter === 'APPROVAL' ? 'active' : ''}`}
              onClick={() => setStatusFilter('APPROVAL')}
            >
              Awaiting Review
            </button>
            <button
              className={`filter-pill ${statusFilter === 'LINK_SENT' ? 'active' : ''}`}
              onClick={() => setStatusFilter('LINK_SENT')}
            >
              Link Sent
            </button>
            <button
              className={`filter-pill ${statusFilter === 'EXHAUSTED' ? 'active' : ''}`}
              onClick={() => setStatusFilter('EXHAUSTED')}
            >
              Recovery Stopped
            </button>
            <button
              className={`filter-pill ${statusFilter === 'AT_RISK' ? 'active' : ''}`}
              onClick={() => setStatusFilter('AT_RISK')}
            >
              At Risk
            </button>
            <button
              className={`filter-pill ${statusFilter === 'REJECTED' ? 'active' : ''}`}
              onClick={() => setStatusFilter('REJECTED')}
            >
              Rejected
            </button>
          </div>
        </div>
      </div>

      <div className="data-table-container">
        {isLoading ? (
          <div className="loading-state">
            <div className="spinner"></div>
            <p>Loading recovery cases...</p>
          </div>
        ) : filteredCases.length === 0 ? (
          <div className="empty-state">
            <AlertTriangle size={32} />
            <p>No recovery cases match your search or filter.</p>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Amount</th>
                <th>Status</th>
                <th>AI Strategy</th>
                <th>Case Reference</th>
                <th>Payment ID</th>
                <th>Created</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredCases.map((item) => {
                const isDemo = (item.id || '').startsWith('case_demo_');
                return (
                  <tr key={item.id}>
                    <td className="amount-cell">
                      ₹{Number(item.amount || 0).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                    </td>
                    <td>{getStatusBadge(item)}</td>
                    <td>
                      {item.decision_action ? (
                        <div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <span className="badge badge-ai">
                              <Sparkles size={11} /> {getActionMerchantLabel(item.decision_action, item.failure_category, item.execution_status)}
                            </span>
                            {item.decision_confidence && (
                              <span style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                                {(item.decision_confidence * 100).toFixed(0)}%
                              </span>
                            )}
                          </div>
                          {item.failure_category_label && (
                            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '3px' }}>
                              {item.failure_category_label}
                            </div>
                          )}
                        </div>
                      ) : (
                        <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>Pending</span>
                      )}
                    </td>
                    <td className="case-id-cell">
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <span>{item.id}</span>
                        {isDemo ? (
                          <span style={{
                            fontSize: '0.66rem',
                            padding: '1px 5px',
                            borderRadius: '4px',
                            background: 'rgba(245, 158, 11, 0.15)',
                            color: '#f59e0b',
                            border: '1px solid rgba(245, 158, 11, 0.3)',
                            fontWeight: 600
                          }}>
                            Demo Scenario
                          </span>
                        ) : (
                          <span style={{
                            fontSize: '0.66rem',
                            padding: '1px 5px',
                            borderRadius: '4px',
                            background: 'rgba(16, 185, 129, 0.12)',
                            color: '#10b981',
                            border: '1px solid rgba(16, 185, 129, 0.3)',
                            fontWeight: 600
                          }}>
                            Test Mode
                          </span>
                        )}
                      </div>
                    </td>
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: '0.82rem' }}>
                      {item.payment_id || '-'}
                    </td>
                    <td style={{ fontSize: '0.8rem' }}>{formatDate(item.created_at)}</td>
                    <td>
                      <button
                        className="btn btn-outline btn-sm"
                        onClick={() => onSelectCase(item.id)}
                      >
                        View
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
