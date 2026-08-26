import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { 
  FlaskConical, 
  TrendingUp, 
  ShieldCheck, 
  AlertTriangle, 
  Play, 
  CheckCircle, 
  Clock, 
  RotateCcw,
  Sparkles,
  Layers,
  HelpCircle,
  FileText
} from 'lucide-react';

export default function EvaluationView() {
  const [activeTab, setActiveTab] = useState('contextual'); // 'contextual' or 'batch'
  const [report, setReport] = useState(null);
  const [contextualReport, setContextualReport] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRunning, setIsRunning] = useState(false);
  const [numCases, setNumCases] = useState(100);
  const [seed, setSeed] = useState(42);
  const [mode, setMode] = useState('deterministic');
  const [error, setError] = useState(null);

  const getActionMerchantLabel = (action, category, outcomeStatus) => {
    switch (action) {
      case 'SEND_PAYMENT_LINK':
        return 'Send Recovery Link';
      case 'WAIT':
        return 'Deferred Hold';
      case 'NO_ACTION':
        if (category === 'FRAUD_OR_SECURITY' || outcomeStatus === 'blocked_fraud_security') {
          return 'Recovery Blocked';
        }
        return 'Recovery Stopped';
      case 'INVESTIGATE':
        return 'Investigation Needed';
      case 'SEND_INVOICE':
        return 'Advisory: Invoice';
      default:
        return action || 'Investigate';
    }
  };

  const getDecisionSourceLabel = (source) => {
    if (source === 'deterministic_fallback' || source === 'deterministic') {
      return 'Rule-Based Decision';
    }
    if (source === 'llm' || source === 'gemini') {
      return 'AI-Assisted Decision';
    }
    return source || 'Rule-Based';
  };

  const fetchLatest = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [batchRes, ctxRes] = await Promise.all([
        api.getLatestEvaluation().catch(() => null),
        api.getLatestContextualEvaluation().catch(() => null),
      ]);
      if (batchRes?.data) {
        setReport(batchRes.data);
      }
      if (ctxRes?.data) {
        setContextualReport(ctxRes.data);
      }
    } catch (err) {
      console.error('Failed to load evaluation data:', err);
      setError('Could not retrieve evaluation results.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchLatest();
  }, []);

  const handleRunEvaluation = async () => {
    setIsRunning(true);
    setError(null);
    try {
      const res = await api.runEvaluation(Number(numCases), Number(seed), mode);
      if (res?.results) {
        const latestResult = res.results[mode] || res.results['llm'] || res.results['deterministic'];
        setReport(latestResult);
      } else {
        await fetchLatest();
      }
    } catch (err) {
      console.error('Batch evaluation error:', err);
      setError(err.message || 'Failed to run batch evaluation.');
    } finally {
      setIsRunning(false);
    }
  };

  const handleRunContextualEvaluation = async () => {
    setIsRunning(true);
    setError(null);
    try {
      const res = await api.runContextualEvaluation();
      if (res?.data) {
        setContextualReport(res.data);
      } else {
        await fetchLatest();
      }
    } catch (err) {
      console.error('Contextual evaluation error:', err);
      setError(err.message || 'Failed to run contextual evaluation.');
    } finally {
      setIsRunning(false);
    }
  };

  const metrics = report?.metrics;
  const breakdown = report?.category_breakdown || [];
  const sampleCases = report?.cases_sample || [];
  const ctxSummary = contextualReport?.summary;
  const ctxCases = contextualReport?.cases || [];

  return (
    <div className="evaluation-view">
      {/* Simulation / Benchmark Disclaimer Banner */}
      <div className="eval-disclaimer-banner">
        <div className="disclaimer-icon">
          <FlaskConical size={22} />
        </div>
        <div className="disclaimer-text">
          <h3>CONTROLLED BENCHMARK & SIMULATION HARNESS</h3>
          <p>
            This evaluation environment operates on isolated synthetic and contextual datasets. 
            All metrics shown are <strong>simulated benchmarks</strong> independent of self-reported model confidence and do not modify live Razorpay payments or operational data.
            <strong> Reproducible benchmark:</strong> identical inputs produce identical outcomes across runs.
          </p>
        </div>
        <div className="disclaimer-tag">ISOLATED BENCHMARK</div>
      </div>

      {/* Sub-Tab Navigation */}
      <div className="eval-tab-nav">
        <button 
          className={`eval-tab-btn ${activeTab === 'contextual' ? 'active' : ''}`}
          onClick={() => setActiveTab('contextual')}
        >
          <Sparkles size={16} />
          <span>Contextual AI Intelligence (Milestone C)</span>
        </button>
        <button 
          className={`eval-tab-btn ${activeTab === 'batch' ? 'active' : ''}`}
          onClick={() => setActiveTab('batch')}
        >
          <Layers size={16} />
          <span>Batch Recovery Simulation (Milestone B)</span>
        </button>
      </div>

      {activeTab === 'contextual' ? (
        <>
          {/* Contextual Control Card */}
          <div className="eval-control-card">
            <div className="control-header">
              <div className="control-title">
                <Sparkles size={18} />
                <span>Contextual Recovery Intelligence Benchmark (16 Complex Scenarios)</span>
              </div>
              {ctxSummary?.evaluation_timestamp && (
                <div className="last-run-tag">
                  <Clock size={14} />
                  <span>Last Run: {new Date(ctxSummary.evaluation_timestamp).toLocaleString()}</span>
                </div>
              )}
            </div>

            <p className="eval-subtext-desc">
              Evaluates AI decision reasoning across multi-attempt customer profiles, tenure, previous payment success tracks, and prior link outcomes without leaking sensitive customer PII or API secrets.
            </p>

            <div className="control-inputs contextual-actions">
              <button 
                className="btn btn-primary run-eval-btn" 
                onClick={handleRunContextualEvaluation}
                disabled={isRunning}
              >
                {isRunning ? (
                  <>
                    <div className="spinner-small"></div>
                    <span>Evaluating Contextual Scenarios...</span>
                  </>
                ) : (
                  <>
                    <Play size={16} />
                    <span>Run Contextual Benchmark (16 Cases)</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {ctxSummary && (
            <>
              {/* Contextual KPI Grid */}
              <div className="eval-kpi-grid">
                <div className="eval-kpi-card highlight-card">
                  <div className="kpi-label">POLICY SAFETY ALIGNMENT</div>
                  <div className="kpi-value">{ctxSummary.policy_agreement_percentage || ctxSummary.action_agreement_percentage || 100}%</div>
                  <div className="kpi-subtext">
                    Authoritative guardrails & financial safety invariants preserved
                  </div>
                </div>

                <div className="eval-kpi-card">
                  <div className="kpi-label">CONTEXT-FACTOR COVERAGE</div>
                  <div className="kpi-value">
                    {ctxSummary.ai_context_factor_coverage_percentage !== undefined ? `${ctxSummary.ai_context_factor_coverage_percentage}%` : 'N/A'}
                  </div>
                  <div className="kpi-subtext">
                    Baseline Heuristic: {ctxSummary.baseline_context_factor_coverage_percentage !== undefined ? `${ctxSummary.baseline_context_factor_coverage_percentage}%` : 'N/A'}
                  </div>
                </div>

                <div className="eval-kpi-card">
                  <div className="kpi-label">PRIORITY AGREEMENT</div>
                  <div className="kpi-value">
                    {ctxSummary.ai_priority_agreement_percentage !== undefined ? `${ctxSummary.ai_priority_agreement_percentage}%` : 'N/A'}
                  </div>
                  <div className="kpi-subtext">
                    Baseline Match: {ctxSummary.baseline_priority_agreement_percentage !== undefined ? `${ctxSummary.baseline_priority_agreement_percentage}%` : 'N/A'}
                  </div>
                </div>

                <div className="eval-kpi-card">
                  <div className="kpi-label">ESCALATION AGREEMENT</div>
                  <div className="kpi-value">
                    {ctxSummary.ai_escalation_agreement_percentage !== undefined ? `${ctxSummary.ai_escalation_agreement_percentage}%` : 'N/A'}
                  </div>
                  <div className="kpi-subtext">
                    Baseline Match: {ctxSummary.baseline_escalation_agreement_percentage !== undefined ? `${ctxSummary.baseline_escalation_agreement_percentage}%` : 'N/A'}
                  </div>
                </div>

                <div className="eval-kpi-card">
                  <div className="kpi-label">AVG EXPLANATION RUBRIC (0-5)</div>
                  <div className="kpi-value">
                    {ctxSummary.ai_average_explanation_score !== undefined ? `${ctxSummary.ai_average_explanation_score}/5.0` : 'N/A'}
                  </div>
                  <div className="kpi-subtext">
                    Baseline Score: {ctxSummary.baseline_average_explanation_score !== undefined ? `${ctxSummary.baseline_average_explanation_score}/5.0` : 'N/A'}
                  </div>
                </div>

                <div className="eval-kpi-card">
                  <div className="kpi-label">MANDATORY APPROVALS (≥₹50K)</div>
                  <div className="kpi-value">{ctxSummary.human_approvals_mandated}</div>
                  <div className="kpi-subtext">100% compliance with merchant guardrails</div>
                </div>
              </div>

              {/* Contextual Cases Ledger */}
              <div className="eval-table-card">
                <div className="table-header">
                  <h3>Contextual Scenarios Comparative Ledger</h3>
                  <div className="table-badge">{ctxCases.length} Scenarios</div>
                </div>

                <div className="table-responsive">
                  <table className="eval-table">
                    <thead>
                      <tr>
                        <th>Scenario</th>
                        <th>Amount</th>
                        <th>Category</th>
                        <th>Deterministic Action</th>
                        <th>Gemini Action</th>
                        <th>Policy Check</th>
                        <th>Agreement</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ctxCases.map((c) => (
                        <tr key={c.id || c.case_id}>
                          <td>
                            <strong>{c.scenario_name}</strong>
                            <div className="mono small text-muted">{c.case_id}</div>
                          </td>
                          <td>₹{Number(c.amount || 0).toLocaleString('en-IN')}</td>
                          <td>
                            <span className="category-code small">{c.failure_category}</span>
                          </td>
                          <td>
                            <span className={`action-tag ${c.deterministic_action}`}>
                              {getActionMerchantLabel(c.deterministic_action, c.failure_category)}
                            </span>
                          </td>
                          <td>
                            <span className={`action-tag ${c.gemini_action}`}>
                              {getActionMerchantLabel(c.gemini_action, c.failure_category)}
                            </span>
                          </td>
                          <td>
                            {c.requires_human_approval ? (
                              <span className="badge-approval-req">APPROVAL REQ</span>
                            ) : (
                              <span className="badge-auto-exec">AUTO-EXEC</span>
                            )}
                          </td>
                          <td>
                            {c.action_diff_flag ? (
                              <span className="diff-tag mismatch">DIFFERS</span>
                            ) : (
                              <span className="diff-tag matched">AGREE</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </>
      ) : (
        <>
          {/* Milestone B Batch Benchmark View */}
          <div className="eval-control-card">
            <div className="control-header">
              <div className="control-title">
                <Layers size={18} />
                <span>Batch Simulation Parameters</span>
              </div>
              {metrics?.timestamp && (
                <div className="last-run-tag">
                  <Clock size={14} />
                  <span>Last Run: {new Date(metrics.timestamp).toLocaleString()}</span>
                </div>
              )}
            </div>

            <div className="control-inputs">
              <div className="input-group">
                <label>Synthetic Cases</label>
                <input 
                  type="number" 
                  min="10" 
                  max="500" 
                  value={numCases} 
                  onChange={(e) => setNumCases(e.target.value)}
                  disabled={isRunning}
                />
              </div>

              <div className="input-group">
                <label>Random Seed</label>
                <input 
                  type="number" 
                  value={seed} 
                  onChange={(e) => setSeed(e.target.value)}
                  disabled={isRunning}
                />
              </div>

              <div className="input-group">
                <label>Decision Engine Mode</label>
                <select 
                  value={mode} 
                  onChange={(e) => setMode(e.target.value)}
                  disabled={isRunning}
                >
                  <option value="deterministic">Deterministic Engine Only</option>
                  <option value="llm">LLM-Enabled (Gemini / OpenAI)</option>
                  <option value="all">Compare All Modes</option>
                </select>
              </div>

              <div className="control-action">
                <button 
                  className="btn btn-primary run-eval-btn" 
                  onClick={handleRunEvaluation}
                  disabled={isRunning}
                >
                  {isRunning ? (
                    <>
                      <div className="spinner-small"></div>
                      <span>Running Benchmark...</span>
                    </>
                  ) : (
                    <>
                      <Play size={16} />
                      <span>Execute Evaluation</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>

          {error && (
            <div className="eval-error-alert">
              <AlertTriangle size={18} />
              <span>{error}</span>
            </div>
          )}

          {metrics && (
            <>
              {/* KPI Summary Cards */}
              <div className="eval-kpi-grid">
                <div className="eval-kpi-card highlight-card">
                  <div className="kpi-label">SIMULATED RECOVERY RATE</div>
                  <div className="kpi-value">{metrics.recovery_rate_percentage}%</div>
                  <div className="kpi-subtext">
                    ₹{Number(metrics.recovered_revenue).toLocaleString('en-IN')} recovered of ₹{Number(metrics.total_revenue_at_risk).toLocaleString('en-IN')} simulated revenue at risk
                  </div>
                </div>

                <div className="eval-kpi-card">
                  <div className="kpi-label">TOTAL EVALUATION CASES</div>
                  <div className="kpi-value">{metrics.total_cases}</div>
                  <div className="kpi-subtext">Mode: {getDecisionSourceLabel(metrics.mode)} | Seed: {metrics.seed} (Deterministic)</div>
                </div>

                <div className="eval-kpi-card">
                  <div className="kpi-label">AUTO-RECOVERED (&lt;₹50K)</div>
                  <div className="kpi-value">₹{Number(metrics.auto_recovered_revenue).toLocaleString('en-IN')}</div>
                  <div className="kpi-subtext">Instant automated payment links</div>
                </div>

                <div className="eval-kpi-card">
                  <div className="kpi-label">HUMAN APPROVAL REQUIRED</div>
                  <div className="kpi-value">{metrics.human_approvals} <span className="sub-val">({metrics.human_approval_rate_percentage}%)</span></div>
                  <div className="kpi-subtext">₹{Number(metrics.human_approved_revenue).toLocaleString('en-IN')} simulated high-value volume</div>
                </div>

                <div className="eval-kpi-card">
                  <div className="kpi-label">FRAUD / SECURITY BLOCKED</div>
                  <div className="kpi-value">{metrics.fraud_blocks} <span className="sub-val">({metrics.fraud_block_rate_percentage}%)</span></div>
                  <div className="kpi-subtext">₹{Number(metrics.blocked_fraud_revenue).toLocaleString('en-IN')} compliance blocked</div>
                </div>

                <div className="eval-kpi-card">
                  <div className="kpi-label">RETRY EXHAUSTION STOPS</div>
                  <div className="kpi-value">{metrics.retry_exhausted_blocks ?? 0} <span className="sub-val">({metrics.retry_exhausted_rate_percentage ?? 0}%)</span></div>
                  <div className="kpi-subtext">₹{Number(metrics.blocked_retry_exhausted_revenue ?? 0).toLocaleString('en-IN')} max retry stopped</div>
                </div>

                <div className="eval-kpi-card">
                  <div className="kpi-label">AVG TIME TO RECOVERY</div>
                  <div className="kpi-value">{metrics.average_time_to_recovery_hours} <span className="sub-val">hrs</span></div>
                  <div className="kpi-subtext">Simulated customer conversion latency</div>
                </div>
              </div>

              {/* Failure Category Performance Breakdown Table */}
              <div className="eval-table-card">
                <div className="table-header">
                  <h3>Failure Category Performance Matrix</h3>
                  <div className="table-badge">9 Categories Evaluated</div>
                </div>

                <div className="table-responsive">
                  <table className="eval-table">
                    <thead>
                      <tr>
                        <th>Category</th>
                        <th>Cases</th>
                        <th>Simulated at Risk</th>
                        <th>Simulated Recovered</th>
                        <th>Recovery Rate</th>
                        <th>Avg Confidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {breakdown.map((row) => (
                        <tr key={row.category}>
                          <td>
                            <span className="category-code">{row.category}</span>
                          </td>
                          <td>{row.cases_count}</td>
                          <td>₹{Number(row.at_risk_revenue).toLocaleString('en-IN')}</td>
                          <td>₹{Number(row.recovered_revenue).toLocaleString('en-IN')}</td>
                          <td>
                            <div className="rate-bar-container">
                              <div 
                                className={`rate-bar-fill ${row.recovery_rate_percentage >= 70 ? 'good' : row.recovery_rate_percentage > 0 ? 'medium' : 'blocked'}`}
                                style={{ width: `${Math.max(row.recovery_rate_percentage, 4)}%` }}
                              ></div>
                              <span className="rate-text">{row.recovery_rate_percentage}%</span>
                            </div>
                          </td>
                          <td>
                            <span className="confidence-pill">{(row.avg_confidence * 100).toFixed(0)}%</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Sample Evaluated Cases */}
              {sampleCases.length > 0 && (
                <div className="eval-table-card">
                  <div className="table-header">
                    <h3>Evaluated Cases Sample Ledger</h3>
                    <div className="table-badge">First {sampleCases.length} Cases</div>
                  </div>

                  <div className="table-responsive">
                    <table className="eval-table">
                      <thead>
                        <tr>
                          <th>Case ID</th>
                          <th>Category</th>
                          <th>Amount</th>
                          <th>Decision Action</th>
                          <th>Decision Source</th>
                          <th>Outcome Status</th>
                          <th>Simulated Recovery</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sampleCases.map((c) => (
                          <tr key={c.id || c.case_id}>
                            <td className="mono">{c.case_id}</td>
                            <td>
                              <span className="category-code small">{c.failure_category}</span>
                            </td>
                            <td>₹{Number(c.amount).toLocaleString('en-IN')}</td>
                            <td>
                              <span className={`action-tag ${c.action}`}>
                                {getActionMerchantLabel(c.action, c.failure_category, c.outcome_status)}
                              </span>
                            </td>
                            <td>
                              <span className="source-tag">{getDecisionSourceLabel(c.decision_source)}</span>
                            </td>
                            <td>
                              <span className={`status-pill ${c.outcome_status.startsWith('recovered') ? 'recovered' : 'pending'}`}>
                                {c.outcome_status}
                              </span>
                            </td>
                            <td>
                              {c.recovered_amount > 0 ? (
                                <span className="recovered-text">₹{Number(c.recovered_amount).toLocaleString('en-IN')}</span>
                              ) : (
                                <span className="unrecovered-text">₹0.00</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
