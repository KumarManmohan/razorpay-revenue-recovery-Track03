import React, { useState } from 'react';
import {
  X,
  Sparkles,
  ExternalLink,
  CheckCircle2,
  XCircle,
  Send,
  Copy,
  Check,
  History,
  ShieldAlert,
  UserCheck,
  AlertTriangle,
  ArrowRight,
  ShieldCheck,
  PauseCircle,
  FileSearch,
  Filter,
  Code2,
  Info
} from 'lucide-react';

export default function CaseDetailModal({
  caseData,
  auditTrail,
  attempts = [],
  onClose,
  onApprove,
  onReject,
  onNotify,
  isActionLoading
}) {
  const [copied, setCopied] = useState(false);
  const [copiedJson, setCopiedJson] = useState(false);
  const [confirmApprove, setConfirmApprove] = useState(false);
  const [confirmReject, setConfirmReject] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [approvalNotes, setApprovalNotes] = useState('');
  const [notificationRecipient, setNotificationRecipient] = useState(
    caseData?.customer_id || 'customer@example.com'
  );

  if (!caseData) return null;

  const handleCopyLink = () => {
    if (caseData.payment_link_url) {
      navigator.clipboard.writeText(caseData.payment_link_url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleCopyJson = () => {
    navigator.clipboard.writeText(JSON.stringify(caseData, null, 2));
    setCopiedJson(true);
    setTimeout(() => setCopiedJson(false), 2000);
  };

  const isExecuted = caseData.execution_status === 'executed';
  const isRejected = caseData.execution_status === 'rejected';
  const isRecovered = caseData.execution_status === 'recovered';
  const isCurrentlyExhausted = caseData.execution_status === 'exhausted';
  const wasExhausted = Boolean(
    caseData.failure_category === 'RECOVERY_EXHAUSTED' ||
    (caseData.decision_reason || '').includes('retry limit exhausted') ||
    (caseData.cancelled_payment_links && caseData.cancelled_payment_links.includes(caseData.payment_link_id)) ||
    (Array.isArray(auditTrail) && auditTrail.some((ev) => ev.event_type === 'RECOVERY_EXHAUSTED'))
  );
  const isPostExhaustionRecovered = isRecovered && wasExhausted;
  const isExhausted = isCurrentlyExhausted || (wasExhausted && !isRecovered);
  const isFraud = caseData.failure_category === 'FRAUD_OR_SECURITY';
  const isWait = caseData.decision_action === 'WAIT';
  const isInvestigate = caseData.decision_action === 'INVESTIGATE';
  const isInvoice = caseData.decision_action === 'SEND_INVOICE';
  const requiresApproval = (caseData.requires_human_approval === 1 || caseData.execution_status === 'approval_required') && !isRecovered && !isRejected && !isExhausted && !isPostExhaustionRecovered;
  const isDemo = (caseData.id || '').startsWith('case_demo_');

  const formatTimelineDate = (dateStr) => {
    if (!dateStr) return '';
    try {
      const d = new Date(dateStr);
      return d.toLocaleDateString('en-IN', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    } catch {
      return dateStr;
    }
  };

  const formatCurrency = (val) => {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 2,
    }).format(val || 0);
  };

  // Merchant-friendly failure descriptions
  const getFriendlyFailureDescription = () => {
    if (caseData.error_description) return caseData.error_description;
    switch (caseData.failure_category) {
      case 'BANK_DECLINED':
        return 'The issuing bank declined the customer payment instrument.';
      case 'INSUFFICIENT_FUNDS':
        return 'Payment failed due to insufficient funds in customer account.';
      case 'CARD_LIMIT_EXCEEDED':
        return 'Card spending or single-transaction limit exceeded.';
      case 'CARD_EXPIRED':
        return 'Customer payment card has expired or reached validity end.';
      case 'AUTHENTICATION_REQUIRED':
        return '3DS customer two-factor authorization was not completed.';
      case 'INVALID_CARD_DETAILS':
        return 'Card number, expiry, or CVV security code was invalid.';
      case 'GATEWAY_ERROR':
        return 'Temporary banking gateway network timeout encountered.';
      case 'FRAUD_OR_SECURITY':
        return 'Security restriction flagged on transaction to prevent unauthorized charge.';
      case 'UNKNOWN_FAILURE':
      default:
        return 'Payment failed due to an unclassified network or gateway error.';
    }
  };

  // Merchant action label
  const getActionMerchantLabel = (action) => {
    switch (action) {
      case 'SEND_PAYMENT_LINK':
        return 'Send Recovery Link';
      case 'WAIT':
        return 'Deferred Hold';
      case 'NO_ACTION':
        return 'Recovery Blocked';
      case 'INVESTIGATE':
        return 'Investigation Needed';
      case 'SEND_INVOICE':
        return 'Advisory: Invoice';
      default:
        return action || 'Investigate';
    }
  };

  // Merchant decision source label
  const getSourceMerchantLabel = (source) => {
    if (source === 'deterministic_fallback') return 'Rule-Based Decision';
    if (source === 'llm' || source === 'gemini') return 'AI-Assisted Decision';
    return source || 'Deterministic Engine';
  };

  // Build dynamic lifecycle steps (strictly reflects actual events)
  const lifecycleSteps = [
    {
      title: 'Payment Failed',
      desc: formatCurrency(caseData.amount),
      status: 'completed',
      icon: AlertTriangle,
    },
    {
      title: 'Failure Analyzed',
      desc: caseData.failure_category_label || caseData.failure_category || 'Classified',
      status: 'completed',
      icon: Filter,
    },
    {
      title: 'AI Strategy',
      desc: getActionMerchantLabel(caseData.decision_action),
      status: 'completed',
      icon: Sparkles,
    },
    {
      title: 'Policy Authority',
      desc: requiresApproval
        ? 'Awaiting Review'
        : isExhausted || isPostExhaustionRecovered
          ? 'Automation Stopped'
          : isFraud
            ? 'Recovery Blocked'
            : 'Recovery Permitted',
      status: requiresApproval ? 'active' : isFraud || isExhausted || isPostExhaustionRecovered ? 'blocked' : 'completed',
      icon: ShieldCheck,
    },
  ];

  const linkAuditEvents = Array.isArray(auditTrail)
    ? auditTrail.filter((ev) =>
        ['PAYMENT_PATH_PRESERVED', 'PAYMENT_LINK_CREATED', 'RECOVERY_LINK_CREATED'].includes(ev.event_type)
      )
    : [];
  const latestLinkEvent = linkAuditEvents.length > 0 ? linkAuditEvents[linkAuditEvents.length - 1] : null;
  const isLinkPreserved = latestLinkEvent
    ? latestLinkEvent.event_type === 'PAYMENT_PATH_PRESERVED'
    : Boolean(caseData.original_payment_link_id && caseData.original_payment_link_id === caseData.payment_link_id);

  const hasActiveOrRecoveredLink =
    !isExhausted &&
    !isPostExhaustionRecovered &&
    !isRejected &&
    !isFraud &&
    (
      isExecuted ||
      (isRecovered && !isPostExhaustionRecovered) ||
      Boolean(caseData.payment_link_url)
    );

  if (hasActiveOrRecoveredLink) {
    if (isLinkPreserved) {
      lifecycleSteps.push({
        title: 'Recovery Link Preserved',
        desc: 'Existing Razorpay Test Link',
        status: 'completed',
        icon: Send,
      });
    } else {
      lifecycleSteps.push({
        title: 'Link Issued',
        desc: caseData.payment_link_id ? 'Razorpay Test Link' : 'Active Link',
        status: 'completed',
        icon: Send,
      });
    }
  }

  if (isRecovered) {
    lifecycleSteps.push({
      title: 'Reconciled',
      desc: isPostExhaustionRecovered
        ? `${formatCurrency(caseData.recovered_amount || caseData.amount)} Captured (Post-Exhaustion)`
        : `${formatCurrency(caseData.recovered_amount || caseData.amount)} Captured`,
      status: 'completed',
      icon: CheckCircle2,
    });
  }

  // Next step description
  const getNextStepText = () => {
    if (isRecovered) {
      return {
        title: 'Payment Captured & Reconciled',
        desc: isPostExhaustionRecovered
          ? 'Revenue has been fully recovered and verified via webhook capture following automated retry exhaustion. No further outreach is required.'
          : 'Revenue has been fully recovered and verified via webhook capture. No further automated recovery is required.',
        icon: CheckCircle2,
        color: '#059669',
      };
    }
    if (requiresApproval) {
      return {
        title: 'Merchant Authorization Required',
        desc: 'Merchant approval is required before the recovery link can be issued to the customer.',
        icon: UserCheck,
        color: '#d97706',
      };
    }
    if (isExhausted) {
      return {
        title: 'Manual Merchant Action Required',
        desc: 'Automated recovery has stopped after retry limits. Manual merchant handling is required.',
        icon: AlertTriangle,
        color: '#dc2626',
      };
    }
    if (isFraud) {
      return {
        title: 'Automated Recovery Blocked',
        desc: 'Automated recovery remains blocked for security and compliance reasons.',
        icon: ShieldAlert,
        color: '#dc2626',
      };
    }
    if (isRejected) {
      return {
        title: 'Recovery Rejected by Merchant',
        desc: 'Merchant declined automated recovery outreach for this transaction.',
        icon: XCircle,
        color: '#dc2626',
      };
    }
    if (hasActiveOrRecoveredLink) {
      return {
        title: 'Waiting for Customer Payment',
        desc: 'Payment link has been created and dispatched. Waiting for the customer to complete payment.',
        icon: Send,
        color: '#0284c7',
      };
    }
    if (isWait) {
      return {
        title: 'Deferred Hold in Progress',
        desc: 'Waiting for a subsequent payment event or merchant intervention before proceeding.',
        icon: PauseCircle,
        color: '#0284c7',
      };
    }
    return {
      title: 'Investigation in Progress',
      desc: 'Failure cause is being investigated. Manual review is recommended before outreach.',
      icon: FileSearch,
      color: '#475569',
    };
  };

  const nextStep = getNextStepText();
  const NextStepIcon = nextStep.icon;

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true" aria-label="Case Detail Modal">
      <div className="modal-container" onClick={(e) => e.stopPropagation()}>
        {/* Modal Header */}
        <div className="modal-header">
          <div className="modal-header-left">
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div className="badge badge-ai">
                <Sparkles size={13} aria-hidden="true" /> Case Governance
              </div>
              {isDemo ? (
                <span style={{
                  fontSize: '0.72rem',
                  padding: '2px 8px',
                  borderRadius: '4px',
                  background: 'rgba(217, 119, 6, 0.1)',
                  color: '#b45309',
                  border: '1px solid rgba(217, 119, 6, 0.25)',
                  fontWeight: 600
                }}>
                  Demo Scenario
                </span>
              ) : (
                <span style={{
                  fontSize: '0.72rem',
                  padding: '2px 8px',
                  borderRadius: '4px',
                  background: 'rgba(5, 150, 105, 0.08)',
                  color: '#047857',
                  border: '1px solid rgba(5, 150, 105, 0.25)',
                  fontWeight: 600
                }}>
                  Razorpay Test Mode (Live)
                </span>
              )}
            </div>
            <span style={{ fontSize: '0.78rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
              {caseData.id}
            </span>
          </div>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close modal">
            <X size={18} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="modal-body">
          {/* Question 1: What Happened? (Hero Headline) */}
          <div className="modal-hero">
            <div className="modal-hero-top">
              <div className="modal-hero-title">
                {formatCurrency(caseData.amount)} Payment {isRecovered ? 'Recovered' : 'Failed'}
              </div>
              <span className="badge badge-approval">
                {caseData.failure_category_label || caseData.failure_category || 'Failed'}
              </span>
            </div>
            <div className="modal-hero-desc">
              {getFriendlyFailureDescription()}
            </div>
            <div className="modal-hero-meta">
              <span>Payment: <code>{caseData.payment_id || 'N/A'}</code></span>
              {caseData.order_id && <span>Order: <code>{caseData.order_id}</code></span>}
              <span>Type: {caseData.is_recurring_revenue ? 'Subscription' : 'One-Time Order'}</span>
              {caseData.created_at && <span>Failed at: {formatTimelineDate(caseData.created_at)}</span>}
            </div>
          </div>

          {/* Operational State Banner */}
          {isRecovered ? (
            <div className="modal-state-banner recovered">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <CheckCircle2 size={24} color="#059669" aria-hidden="true" />
                <div>
                  <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#047857' }}>
                    Recovered &amp; Reconciled
                  </div>
                  <div style={{ fontSize: '0.78rem', color: '#065f46' }}>
                    Captured Payment ID: <code>{caseData.recovered_payment_id || 'N/A'}</code>
                    {caseData.recovered_at && ` • ${formatTimelineDate(caseData.recovered_at)}`}
                    {isPostExhaustionRecovered && ' • Post-Exhaustion Payment'}
                  </div>
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <span style={{ fontSize: '0.70rem', color: '#047857', textTransform: 'uppercase', fontWeight: 700 }}>Recovered</span>
                <div style={{ fontSize: '1.20rem', fontWeight: 800, color: '#059669', fontFamily: 'var(--font-mono)' }}>
                  {formatCurrency(caseData.recovered_amount || caseData.amount)}
                </div>
              </div>
            </div>
          ) : requiresApproval ? (
            <div className="modal-state-banner approval">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <UserCheck size={24} color="#d97706" aria-hidden="true" />
                <div>
                  <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#92400e' }}>
                    Awaiting Review
                  </div>
                  <div style={{ fontSize: '0.78rem', color: '#78350f' }}>
                    {caseData.amount >= 50000
                      ? 'High-value transaction (≥ ₹50,000) gated for merchant authorization.'
                      : 'Policy guardrail requires merchant review before issuing a payment link.'}
                  </div>
                </div>
              </div>
            </div>
          ) : isExhausted ? (
            <div className="modal-state-banner exhausted">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <AlertTriangle size={24} color="#dc2626" aria-hidden="true" />
                <div>
                  <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#991b1b' }}>
                    Recovery Stopped
                  </div>
                  <div style={{ fontSize: '0.78rem', color: '#7f1d1d' }}>
                    Automated recovery halted after retry limits. Manual merchant handling required.
                  </div>
                </div>
              </div>
            </div>
          ) : isFraud ? (
            <div className="modal-state-banner blocked">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <ShieldAlert size={24} color="#dc2626" aria-hidden="true" />
                <div>
                  <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#991b1b' }}>
                    Recovery Blocked
                  </div>
                  <div style={{ fontSize: '0.78rem', color: '#7f1d1d' }}>
                    Security or compliance restriction prevented automated recovery outreach.
                  </div>
                </div>
              </div>
            </div>
          ) : isWait ? (
            <div className="modal-state-banner hold">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <PauseCircle size={24} color="#0284c7" aria-hidden="true" />
                <div>
                  <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#075985' }}>
                    Deferred Hold
                  </div>
                  <div style={{ fontSize: '0.78rem', color: '#0369a1' }}>
                    Recovery paused pending a subsequent payment event or merchant intervention.
                  </div>
                </div>
              </div>
            </div>
          ) : isExecuted || caseData.payment_link_url ? (
            <div className="modal-state-banner executed">
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <Send size={24} color="#0284c7" aria-hidden="true" />
                <div>
                  <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#075985' }}>
                    {isLinkPreserved ? 'Link Preserved' : 'Link Sent'}
                  </div>
                  <div style={{ fontSize: '0.78rem', color: '#0369a1' }}>
                    {isLinkPreserved
                      ? 'Existing active recovery payment link preserved. Waiting for customer payment.'
                      : 'Recovery payment link created and active. Waiting for customer payment.'}
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {/* Lifecycle Stepper */}
          <div className="lifecycle-stepper-box">
            <div className="lifecycle-stepper-title">
              Payment Recovery Lifecycle
            </div>
            <div className="lifecycle-stepper-items">
              {lifecycleSteps.map((step, idx) => {
                const StepIcon = step.icon;
                return (
                  <React.Fragment key={idx}>
                    <div className={`stepper-step ${step.status}`}>
                      <StepIcon size={14} aria-hidden="true" />
                      <div className="stepper-step-content">
                        <span className="stepper-step-title">{step.title}</span>
                        <span className="stepper-step-desc">{step.desc}</span>
                      </div>
                    </div>
                    {idx < lifecycleSteps.length - 1 && (
                      <ArrowRight size={13} color="var(--text-muted)" style={{ flexShrink: 0 }} aria-hidden="true" />
                    )}
                  </React.Fragment>
                );
              })}
            </div>
          </div>

          {/* Three-Layer Governance: AI Advisor vs Policy Authority vs Execution Status */}
          <div className="governance-container">
            <div className="governance-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Sparkles size={16} color="var(--primary)" aria-hidden="true" />
                <span style={{ fontSize: '0.88rem', fontWeight: 700 }}>Three-Layer Governance Flow</span>
              </div>
              <span className="badge badge-ai">
                {getSourceMerchantLabel(caseData.decision_source)}
              </span>
            </div>

            <div className="governance-grid">
              {/* 1. AI Advisor (Gemini) */}
              <div className="governance-card ai">
                <span className="governance-card-label">1. AI Advisor (Gemini)</span>
                <span className="governance-card-value" style={{ color: '#6d28d9' }}>
                  {getActionMerchantLabel(caseData.decision_action)}
                </span>
                <span className="governance-card-sub">
                  Confidence: {caseData.decision_confidence ? `${Math.round(caseData.decision_confidence * 100)}%` : 'N/A'}
                </span>
              </div>

              {/* 2. Policy Authority (Python) */}
              <div className="governance-card policy">
                <span className="governance-card-label">2. Policy Authority (Python)</span>
                <span className="governance-card-value" style={{ color: requiresApproval ? '#d97706' : isExhausted || isPostExhaustionRecovered || isFraud ? '#dc2626' : '#059669' }}>
                  {requiresApproval
                    ? 'Human Approval Required'
                    : isExhausted || isPostExhaustionRecovered
                      ? 'Automation Stopped'
                      : isFraud
                        ? 'Recovery Blocked'
                        : 'Recovery Permitted'}
                </span>
                <span className="governance-card-sub">
                  {caseData.amount >= 50000
                    ? 'Policy: ≥ ₹50,000 Threshold'
                    : isExhausted || isPostExhaustionRecovered
                      ? 'Policy: Max Retries Exceeded'
                      : isFraud
                        ? 'Policy: Compliance Guardrail'
                        : 'Policy: Server Safety Rules Passed'}
                </span>
              </div>

              {/* 3. Execution Status */}
              <div className="governance-card execution">
                <span className="governance-card-label">3. Execution Status</span>
                <span className="governance-card-value" style={{ color: isRecovered ? '#059669' : isExecuted ? '#0284c7' : isRejected || isExhausted ? '#dc2626' : '#d97706' }}>
                  {isRecovered
                    ? isPostExhaustionRecovered
                      ? 'Recovered Post-Exhaustion'
                      : 'Recovered & Reconciled'
                    : isExecuted
                      ? isLinkPreserved ? 'Link Preserved' : 'Link Sent'
                      : isRejected
                        ? 'Rejected'
                        : isExhausted
                          ? 'Recovery Stopped'
                          : isWait
                            ? 'Deferred Hold'
                            : 'Awaiting Review'}
                </span>
                <span className="governance-card-sub">
                  Channel: {caseData.payment_link_url ? (isPostExhaustionRecovered ? 'Reconciled (Link Cancelled)' : 'Razorpay Payment Link') : isRecovered ? 'Reconciled' : 'None'}
                </span>
              </div>
            </div>

            <div className="governance-rationale">
              <strong>Contextual Rationale:</strong> {caseData.decision_reason || 'Standard recovery policy applied based on failure classification.'}
            </div>
          </div>

          {/* Active Recovery Payment Link Box (Only display verified URL) */}
          {caseData.payment_link_url ? (
            <div className="payment-link-box" style={isPostExhaustionRecovered ? { borderColor: '#e2e8f0', background: '#f8fafc' } : {}}>
              <div className="payment-link-details">
                <span style={{ fontSize: '0.74rem', color: isPostExhaustionRecovered ? '#64748b' : '#0284c7', fontWeight: 700, textTransform: 'uppercase' }}>
                  {isPostExhaustionRecovered ? 'Historical Payment Link (Cancelled on Exhaustion)' : 'Recovery Payment Link (Razorpay Test Mode)'}
                </span>
                <a
                  href={caseData.payment_link_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="url"
                  style={isPostExhaustionRecovered ? { color: '#64748b' } : {}}
                  title="Open official Razorpay payment link"
                >
                  {caseData.payment_link_url}
                </a>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                  <span>Link ID: <code style={{ color: isPostExhaustionRecovered ? '#64748b' : '#0284c7' }}>{caseData.payment_link_id}</code></span>
                  {caseData.original_payment_link_id && caseData.original_payment_link_id !== caseData.payment_link_id && (
                    <span style={{ color: '#d97706' }}>
                      Pre-existing Link ID: <code>{caseData.original_payment_link_id}</code>
                    </span>
                  )}
                  {caseData.cancelled_payment_links && (
                    <span style={{ color: '#dc2626' }}>
                      Cancelled Open Links: <code>{caseData.cancelled_payment_links}</code>
                    </span>
                  )}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
                <button className="btn btn-outline btn-sm" onClick={handleCopyLink} aria-label="Copy recovery payment link">
                  {copied ? <Check size={14} color="var(--success)" /> : <Copy size={14} />}
                  <span>{copied ? 'Copied' : 'Copy'}</span>
                </button>
                <a
                  href={caseData.payment_link_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-outline btn-sm"
                  aria-label="Open recovery payment link in new window"
                >
                  <ExternalLink size={14} />
                  <span>Open Link</span>
                </a>
              </div>
            </div>
          ) : caseData.payment_link_id ? (
            <div className="payment-link-box" style={{ borderColor: '#fde68a', background: '#fffbeb' }}>
              <div className="payment-link-details">
                <span style={{ fontSize: '0.74rem', color: '#d97706', fontWeight: 700, textTransform: 'uppercase' }}>
                  Payment Link ID Registered
                </span>
                <span style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
                  Payment link URL unavailable — fetch from Razorpay dashboard.
                </span>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '2px' }}>
                  Link ID: <code style={{ color: '#d97706' }}>{caseData.payment_link_id}</code>
                </div>
              </div>
            </div>
          ) : null}

          {/* Operational Next Step Callout */}
          <div className="next-step-box">
            <div className="next-step-icon">
              <NextStepIcon size={16} color={nextStep.color} aria-hidden="true" />
            </div>
            <div>
              <div className="next-step-title">{nextStep.title}</div>
              <div className="next-step-desc">{nextStep.desc}</div>
            </div>
          </div>

          {/* Payment Attempt Ledger (if attempts exist) */}
          {attempts && attempts.length > 0 && (
            <div style={{
              background: '#ffffff',
              border: '1px solid var(--border-color)',
              borderRadius: 'var(--radius-md)',
              padding: '14px 18px',
              boxShadow: 'var(--shadow-sm)'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                <div style={{ fontSize: '0.74rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  Payment Attempt Ledger ({attempts.length} Attempt{attempts.length > 1 ? 's' : ''})
                </div>
                {caseData.order_id && (
                  <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    Order: {caseData.order_id}
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {attempts.map((att, idx) => (
                  <div key={att.id || idx} style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '8px 12px',
                    borderRadius: '6px',
                    background: '#f8fafc',
                    border: '1px solid var(--border-color)',
                    fontSize: '0.78rem',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{
                        fontWeight: 700,
                        color: att.status === 'captured' ? '#059669' : '#dc2626',
                        fontFamily: 'var(--font-mono)'
                      }}>
                        Attempt #{idx + 1}
                      </span>
                      <code style={{ color: '#0284c7' }}>{att.payment_id || 'N/A'}</code>
                      {att.error_description && (
                        <span style={{ color: 'var(--text-secondary)', fontSize: '0.74rem' }}>
                          • {att.error_description}
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{
                        padding: '2px 8px',
                        borderRadius: '4px',
                        fontSize: '0.70rem',
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        background: att.status === 'captured'
                          ? '#ecfdf5'
                          : att.status === 'captured_duplicate'
                            ? '#fffbeb'
                            : '#fef2f2',
                        color: att.status === 'captured'
                          ? '#047857'
                          : att.status === 'captured_duplicate'
                            ? '#92400e'
                            : '#991b1b',
                        border: `1px solid ${att.status === 'captured'
                            ? '#a7f3d0'
                            : att.status === 'captured_duplicate'
                              ? '#fde68a'
                              : '#fecaca'
                          }`,
                      }}>
                        {att.status === 'captured_duplicate' ? 'Duplicate / Overpayment' : att.status}
                      </span>
                      <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
                        {formatTimelineDate(att.created_at)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Progressive Technical Disclosure (Expandable) */}
          <details className="tech-details-wrapper">
            <summary className="tech-details-summary">
              <Code2 size={15} aria-hidden="true" />
              <span>Technical Evidence &amp; Diagnostics</span>
            </summary>
            <div className="tech-details-content">
              <div className="tech-grid">
                <div className="tech-item">
                  <span className="tech-item-label">Case Identifier</span>
                  <span className="tech-item-value">{caseData.id}</span>
                </div>
                <div className="tech-item">
                  <span className="tech-item-label">Payment Reference</span>
                  <span className="tech-item-value">{caseData.payment_id || 'N/A'}</span>
                </div>
                <div className="tech-item">
                  <span className="tech-item-label">Order Reference</span>
                  <span className="tech-item-value">{caseData.order_id || 'N/A'}</span>
                </div>
                <div className="tech-item">
                  <span className="tech-item-label">Recovery Order</span>
                  <span className="tech-item-value">{caseData.recovery_order_id || 'N/A'}</span>
                </div>
                <div className="tech-item">
                  <span className="tech-item-label">Payment Link ID</span>
                  <span className="tech-item-value">{caseData.payment_link_id || 'N/A'}</span>
                </div>
                <div className="tech-item">
                  <span className="tech-item-label">Decision Source</span>
                  <span className="tech-item-value">{getSourceMerchantLabel(caseData.decision_source)} ({caseData.decision_source || 'engine'})</span>
                </div>
                <div className="tech-item">
                  <span className="tech-item-label">Failure Code</span>
                  <span className="tech-item-value">{caseData.failure_code || caseData.failure_category || 'N/A'}</span>
                </div>
                <div className="tech-item">
                  <span className="tech-item-label">Execution Status (Raw)</span>
                  <span className="tech-item-value">{caseData.execution_status}</span>
                </div>
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '6px' }}>
                <button className="btn btn-outline btn-sm" onClick={handleCopyJson} aria-label="Copy raw case JSON payload">
                  {copiedJson ? <Check size={13} color="var(--success)" /> : <Copy size={13} />}
                  <span>{copiedJson ? 'Copied JSON' : 'Copy Case JSON'}</span>
                </button>
              </div>
            </div>
          </details>

          {/* Chronological Audit Trail Timeline */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
              <History size={16} color="var(--primary)" aria-hidden="true" />
              <h4 style={{ fontSize: '0.90rem' }}>Chronological Audit Trail (SQLite Ledger)</h4>
            </div>

            <div className="audit-timeline">
              {auditTrail && auditTrail.length > 0 ? (
                auditTrail.map((event) => (
                  <div key={event.id} className="timeline-item">
                    <div className="timeline-dot">
                      <History size={14} aria-hidden="true" />
                    </div>
                    <div className="timeline-content">
                      <div className="timeline-header">
                        <span className="timeline-type">{event.event_type}</span>
                        <span className="timeline-time">{formatTimelineDate(event.created_at)}</span>
                      </div>
                      <p className="timeline-message">{event.message}</p>
                      {event.metadata && (
                        <div className="timeline-meta">
                          {typeof event.metadata === 'string' ? event.metadata : JSON.stringify(event.metadata)}
                        </div>
                      )}
                    </div>
                  </div>
                ))
              ) : (
                <div style={{ fontSize: '0.80rem', color: 'var(--text-muted)', padding: '12px 0' }}>
                  No audit events recorded for this case yet.
                </div>
              )}
            </div>
          </div>

          {/* Test Customer Notification Section */}
          <div style={{
            background: '#ffffff',
            border: '1px solid var(--border-color)',
            borderRadius: 'var(--radius-md)',
            padding: '16px',
            boxShadow: 'var(--shadow-sm)'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
              <Send size={16} color="#0284c7" aria-hidden="true" />
              <h4 style={{ fontSize: '0.88rem' }}>Customer Notification (Mock / Test Mode)</h4>
            </div>
            <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '12px' }}>
              Dispatches test recovery instructions to customer email. Safeguarded with anti-spam rate limiting.
            </p>
            <div style={{ display: 'flex', gap: '8px' }}>
              <input
                type="email"
                placeholder="customer@example.com"
                className="search-input"
                style={{ flex: 1 }}
                value={notificationRecipient}
                onChange={(e) => setNotificationRecipient(e.target.value)}
                aria-label="Customer email recipient"
              />
              <button
                className="btn btn-outline btn-sm"
                onClick={() => onNotify(caseData.id, notificationRecipient)}
                disabled={isActionLoading}
              >
                <Send size={13} />
                <span>Send Test Notification</span>
              </button>
            </div>
          </div>
        </div>

        {/* Modal Footer / Actions */}
        <div className="modal-footer">
          <button className="btn btn-outline" onClick={onClose}>
            Close
          </button>

          {/* Human Review Controls */}
          {requiresApproval && !isExecuted && !isRejected && (
            <div style={{ display: 'flex', gap: '8px' }}>
              {!confirmApprove && !confirmReject ? (
                <>
                  <button
                    className="btn btn-danger btn-sm"
                    onClick={() => setConfirmReject(true)}
                    disabled={isActionLoading}
                  >
                    <XCircle size={14} />
                    <span>Reject</span>
                  </button>
                  <button
                    className="btn btn-success btn-sm"
                    onClick={() => setConfirmApprove(true)}
                    disabled={isActionLoading}
                  >
                    <CheckCircle2 size={14} />
                    <span>Approve &amp; Issue Link</span>
                  </button>
                </>
              ) : confirmApprove ? (
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <input
                    type="text"
                    placeholder="Approval note (optional)"
                    className="search-input"
                    style={{ fontSize: '0.78rem', padding: '4px 8px' }}
                    value={approvalNotes}
                    onChange={(e) => setApprovalNotes(e.target.value)}
                  />
                  <button
                    className="btn btn-success btn-sm"
                    onClick={() => {
                      onApprove(caseData.id, approvalNotes);
                      setConfirmApprove(false);
                    }}
                    disabled={isActionLoading}
                  >
                    Confirm &amp; Generate Link
                  </button>
                  <button
                    className="btn btn-outline btn-sm"
                    onClick={() => setConfirmApprove(false)}
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <input
                    type="text"
                    placeholder="Rejection reason"
                    className="search-input"
                    style={{ fontSize: '0.78rem', padding: '4px 8px' }}
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                  />
                  <button
                    className="btn btn-danger btn-sm"
                    onClick={() => {
                      onReject(caseData.id, rejectReason);
                      setConfirmReject(false);
                    }}
                    disabled={isActionLoading}
                  >
                    Confirm Reject
                  </button>
                  <button
                    className="btn btn-outline btn-sm"
                    onClick={() => setConfirmReject(false)}
                  >
                    Cancel
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
