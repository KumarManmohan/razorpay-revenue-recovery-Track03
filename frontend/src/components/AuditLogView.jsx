import React, { useState, useEffect } from 'react';
import {
  History,
  Search,
  Filter,
  ExternalLink,
  Clock,
  ShieldAlert,
  CheckCircle2,
  Sparkles,
  Send,
  FileText,
  AlertTriangle,
  RefreshCw
} from 'lucide-react';
import { api } from '../api';

export default function AuditLogView({ onSelectCase }) {
  const [auditEvents, setAuditEvents] = useState([]);
  const [validCaseIds, setValidCaseIds] = useState(new Set());
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [typeFilter, setTypeFilter] = useState('ALL');

  const fetchAuditEvents = async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const [auditRes, casesRes] = await Promise.all([
        api.getAuditEvents(150),
        api.getCases(200).catch(() => ({ cases: [] })),
      ]);
      setAuditEvents(Array.isArray(auditRes?.events) ? auditRes.events : []);
      const casesList = Array.isArray(casesRes?.cases) ? casesRes.cases : [];
      const ids = new Set(casesList.map((c) => c.case_id || c.id).filter(Boolean));
      setValidCaseIds(ids);
    } catch (err) {
      console.error('Failed to load audit events:', err);
      setLoadError(err.message || 'Unable to load audit events. Check that the backend is running.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchAuditEvents();
  }, []);

  const getEventMerchantLabel = (eventType) => {
    const evType = (eventType || '').toUpperCase();
    switch (evType) {
      case 'PAYMENT_FAILED':
      case 'PAYMENT_ATTEMPT_FAILED':
        return 'Payment Failed';
      case 'WEBHOOK_RECEIVED':
        return 'Webhook Received';
      case 'REVENUE_RISK_ANALYZED':
      case 'RISK_ANALYZED':
        return 'Risk Analyzed';
      case 'AI_DECISION_RECOMMENDED':
      case 'RECOVERY_DECIDED':
        return 'Recovery Decided';
      case 'HUMAN_APPROVAL_REQUIRED':
        return 'Approval Required';
      case 'HUMAN_APPROVAL_GRANTED':
        return 'Approval Granted';
      case 'HUMAN_APPROVAL_REJECTED':
        return 'Approval Rejected';
      case 'PAYMENT_LINK_CREATED':
      case 'RECOVERY_LINK_CREATED':
        return 'Recovery Link Created';
      case 'RECOVERY_PAYMENT_DETECTED':
      case 'RECOVERY_CASE_RECONCILED':
      case 'REVENUE_RECOVERED':
        return 'Payment Reconciled';
      case 'PAYMENT_LINK_CANCELLED':
      case 'PAYMENT_LINK_CANCELLED_AFTER_RECOVERY':
        return 'Sibling Link Cancelled';
      case 'PAYMENT_LINK_CANCELLATION_SKIPPED':
        return 'Link Cancellation Skipped';
      case 'RECOVERY_EXHAUSTED':
        return 'Recovery Stopped (Max Retries)';
      case 'RECOVERY_BLOCKED':
        return 'Recovery Blocked (Safety)';
      case 'DUPLICATE_PAYMENT_DETECTED':
        return 'Duplicate Payment Detected';
      case 'NOTIFICATION_SENT':
      case 'CUSTOMER_NOTIFICATION_SENT':
        return 'Notification Sent';
      case 'NOTIFICATION_BLOCKED_DUPLICATE':
      case 'CUSTOMER_NOTIFICATION_BLOCKED':
        return 'Notification Blocked';
      case 'PAYMENT_PATH_PRESERVED':
        return 'Payment Path Preserved';
      case 'PAYMENT_ATTEMPTS_CONSOLIDATED':
        return 'Attempts Consolidated';
      case 'TEST_EVENT':
        return 'Diagnostic Test Event';
      default:
        return eventType ? eventType.replace(/_/g, ' ') : 'Unknown Event';
    }
  };

  const getEventBadgeClass = (eventType) => {
    const evType = (eventType || '').toUpperCase();
    switch (evType) {
      case 'WEBHOOK_RECEIVED':
      case 'PAYMENT_FAILED':
      case 'PAYMENT_ATTEMPT_FAILED':
        return 'badge-failed';
      case 'REVENUE_RISK_ANALYZED':
      case 'RISK_ANALYZED':
        return 'badge-pending';
      case 'AI_DECISION_RECOMMENDED':
      case 'RECOVERY_DECIDED':
        return 'badge-ai';
      case 'HUMAN_APPROVAL_REQUIRED':
        return 'badge-approval';
      case 'HUMAN_APPROVAL_GRANTED':
      case 'RECOVERY_LINK_CREATED':
      case 'PAYMENT_LINK_CREATED':
      case 'RECOVERY_PAYMENT_DETECTED':
      case 'RECOVERY_CASE_RECONCILED':
      case 'REVENUE_RECOVERED':
        return 'badge-executed';
      case 'HUMAN_APPROVAL_REJECTED':
      case 'RECOVERY_BLOCKED':
      case 'RECOVERY_EXHAUSTED':
        return 'badge-rejected';
      case 'DUPLICATE_PAYMENT_DETECTED':
        return 'badge-approval';
      case 'PAYMENT_LINK_CANCELLED':
      case 'PAYMENT_LINK_CANCELLED_AFTER_RECOVERY':
      case 'CUSTOMER_NOTIFICATION_SENT':
      case 'CUSTOMER_NOTIFICATION_BLOCKED':
      case 'PAYMENT_PATH_PRESERVED':
      case 'PAYMENT_ATTEMPTS_CONSOLIDATED':
      case 'TEST_EVENT':
      default:
        return 'badge-pending';
    }
  };

  const getEventIcon = (eventType) => {
    const evType = (eventType || '').toUpperCase();
    switch (evType) {
      case 'WEBHOOK_RECEIVED':
      case 'PAYMENT_FAILED':
        return <ShieldAlert size={14} color="#f43f5e" />;
      case 'REVENUE_RISK_ANALYZED':
      case 'RISK_ANALYZED':
        return <Filter size={14} color="#f59e0b" />;
      case 'AI_DECISION_RECOMMENDED':
      case 'RECOVERY_DECIDED':
        return <Sparkles size={14} color="#38bdf8" />;
      case 'RECOVERY_LINK_CREATED':
      case 'PAYMENT_LINK_CREATED':
        return <Send size={14} color="#10b981" />;
      case 'REVENUE_RECOVERED':
      case 'RECOVERY_CASE_RECONCILED':
        return <CheckCircle2 size={14} color="#10b981" />;
      case 'DUPLICATE_PAYMENT_DETECTED':
        return <AlertTriangle size={14} color="#f59e0b" />;
      case 'RECOVERY_EXHAUSTED':
        return <ShieldAlert size={14} color="#ef4444" />;
      default:
        return <FileText size={14} color="#94a3b8" />;
    }
  };

  const formatDate = (isoString) => {
    if (!isoString) return '—';
    try {
      const d = new Date(isoString);
      if (isNaN(d.getTime())) return String(isoString);
      return d.toLocaleDateString('en-IN', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true,
      });
    } catch {
      return String(isoString);
    }
  };

  const filteredEvents = auditEvents.filter((event) => {
    if (!event) return false;
    const term = (searchTerm || '').toLowerCase();
    const caseId = (event.case_id || '').toLowerCase();
    const msg = (event.message || '').toLowerCase();
    const evType = (event.event_type || '').toUpperCase();
    const label = getEventMerchantLabel(event.event_type).toLowerCase();

    const matchesSearch =
      !term ||
      caseId.includes(term) ||
      msg.includes(term) ||
      evType.toLowerCase().includes(term) ||
      label.includes(term);

    if (!matchesSearch) return false;

    if (typeFilter === 'ALL') return true;
    if (typeFilter === 'WEBHOOKS') return evType.includes('WEBHOOK') || evType.includes('PAYMENT_FAILED');
    if (typeFilter === 'DECISIONS') return evType.includes('DECIS') || evType.includes('RISK');
    if (typeFilter === 'APPROVALS') return evType.includes('APPROVAL');
    if (typeFilter === 'LINKS') return evType.includes('LINK');
    if (typeFilter === 'RECONCILED') return evType.includes('RECOVER') || evType.includes('RECONCIL');
    return true;
  });

  return (
    <div className="section-card">
      <div className="section-header">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <History size={18} color="#38bdf8" />
            <h3 style={{ margin: 0 }}>Global Immutable Audit Trail</h3>
          </div>
          <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '4px' }}>
            Append-only SQLite event ledger recording all gateway webhooks, risk analyses, AI decisions, approvals, and reconciliations (showing latest {filteredEvents.length} events)
          </p>
        </div>

        <div className="table-controls">
          <div className="search-input-wrapper">
            <Search size={15} />
            <input
              type="text"
              placeholder="Search Event, Case ID, or Message..."
              className="search-input"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>

          <div className="filter-pills">
            <button
              className={`filter-pill ${typeFilter === 'ALL' ? 'active' : ''}`}
              onClick={() => setTypeFilter('ALL')}
            >
              All Events
            </button>
            <button
              className={`filter-pill ${typeFilter === 'WEBHOOKS' ? 'active' : ''}`}
              onClick={() => setTypeFilter('WEBHOOKS')}
            >
              Webhooks
            </button>
            <button
              className={`filter-pill ${typeFilter === 'DECISIONS' ? 'active' : ''}`}
              onClick={() => setTypeFilter('DECISIONS')}
            >
              AI Decisions
            </button>
            <button
              className={`filter-pill ${typeFilter === 'APPROVALS' ? 'active' : ''}`}
              onClick={() => setTypeFilter('APPROVALS')}
            >
              Human Approvals
            </button>
            <button
              className={`filter-pill ${typeFilter === 'LINKS' ? 'active' : ''}`}
              onClick={() => setTypeFilter('LINKS')}
            >
              Payment Links
            </button>
            <button
              className={`filter-pill ${typeFilter === 'RECONCILED' ? 'active' : ''}`}
              onClick={() => setTypeFilter('RECONCILED')}
            >
              Reconciled
            </button>
          </div>

          <button
            className="btn btn-outline btn-sm"
            onClick={fetchAuditEvents}
            disabled={isLoading}
            title="Refresh Audit Logs"
          >
            <RefreshCw size={13} className={isLoading ? 'spinner' : ''} />
            <span>{isLoading ? 'Loading...' : 'Refresh'}</span>
          </button>
        </div>
      </div>

      <div className="data-table-container">
        <table className="data-table">
          <thead>
            <tr>
              <th>Event ID & Timestamp</th>
              <th>Case Reference</th>
              <th>Event Type</th>
              <th>Audit Narrative</th>
              <th>Metadata Context</th>
              <th style={{ textAlign: 'right' }}>Action</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan="6" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>
                  <RefreshCw size={24} className="spinner" style={{ margin: '0 auto 12px' }} />
                  <div>Loading audit logs from SQLite ledger...</div>
                </td>
              </tr>
            ) : loadError ? (
              <tr>
                <td colSpan="6" style={{ textAlign: 'center', padding: '40px', color: '#f43f5e' }}>
                  <AlertTriangle size={24} style={{ margin: '0 auto 12px', color: '#f43f5e' }} />
                  <div style={{ fontWeight: 600, marginBottom: '6px' }}>Unable to load audit events.</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '14px' }}>
                    {loadError}
                  </div>
                  <button className="btn btn-outline btn-sm" onClick={fetchAuditEvents} style={{ margin: '0 auto' }}>
                    <RefreshCw size={13} />
                    <span>Retry Connection</span>
                  </button>
                </td>
              </tr>
            ) : auditEvents.length === 0 ? (
              <tr>
                <td colSpan="6" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>
                  No audit events recorded yet.
                </td>
              </tr>
            ) : filteredEvents.length === 0 ? (
              <tr>
                <td colSpan="6" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>
                  No audit events found matching your criteria.
                </td>
              </tr>
            ) : (
              filteredEvents.map((event) => {
                const eventId = event?.id != null ? event.id : '—';
                const caseId = event?.case_id || '—';
                const hasValidCase = Boolean(event?.case_id && validCaseIds.has(event.case_id));
                const eventType = event?.event_type || 'UNKNOWN_EVENT';
                const message = event?.message || 'No narrative recorded.';

                return (
                  <tr key={eventId !== '—' ? eventId : `${caseId}-${Math.random()}`}>
                    <td>
                      <div style={{ fontWeight: 600, fontFamily: 'var(--font-mono)', fontSize: '0.8rem', color: 'var(--text-primary)' }}>
                        #{eventId}
                      </div>
                      <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px', marginTop: '2px' }}>
                        <Clock size={11} />
                        <span>{formatDate(event?.created_at)}</span>
                      </div>
                    </td>

                    <td>
                      {hasValidCase ? (
                        <button
                          className="btn btn-ghost btn-sm"
                          style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem', padding: '4px 8px', color: '#38bdf8' }}
                          onClick={() => onSelectCase && onSelectCase(event.case_id)}
                          title="Open Case Details"
                        >
                          {caseId}
                        </button>
                      ) : (
                        <span
                          style={{
                            fontFamily: 'var(--font-mono)',
                            fontSize: '0.78rem',
                            padding: '4px 8px',
                            color: 'var(--text-muted)',
                            display: 'inline-block'
                          }}
                          title="System or test event without active case record"
                        >
                          {caseId}
                        </span>
                      )}
                    </td>

                    <td>
                      <span 
                        className={`badge ${getEventBadgeClass(eventType)}`} 
                        style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}
                        title={`Technical Event: ${eventType}`}
                      >
                        {getEventIcon(eventType)}
                        <span>{getEventMerchantLabel(eventType)}</span>
                      </span>
                    </td>

                    <td style={{ maxWidth: '320px' }}>
                      <div style={{ fontSize: '0.84rem', color: 'var(--text-primary)', lineHeight: 1.4 }}>
                        {message}
                      </div>
                    </td>

                    <td style={{ maxWidth: '200px' }}>
                      {event?.metadata ? (
                        <pre style={{
                          margin: 0,
                          fontSize: '0.7rem',
                          fontFamily: 'var(--font-mono)',
                          background: 'var(--bg-surface)',
                          border: '1px solid var(--border-color)',
                          padding: '4px 8px',
                          borderRadius: '4px',
                          overflowX: 'auto',
                          color: 'var(--text-secondary)'
                        }}>
                          {typeof event.metadata === 'object'
                            ? JSON.stringify(event.metadata, null, 1)
                            : String(event.metadata)}
                        </pre>
                      ) : (
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>—</span>
                      )}
                    </td>

                    <td style={{ textAlign: 'right' }}>
                      {hasValidCase ? (
                        <button
                          className="btn btn-outline btn-sm"
                          style={{ padding: '4px 8px', fontSize: '0.75rem' }}
                          onClick={() => onSelectCase && onSelectCase(event.case_id)}
                        >
                          <ExternalLink size={12} />
                          <span>View Case</span>
                        </button>
                      ) : (
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', paddingRight: '8px' }}>
                          —
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
