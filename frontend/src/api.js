/**
 * API Service for interacting with the FastAPI Backend
 * Connects to http://127.0.0.1:8000 (or /api proxy)
 */

const API_BASE_URL = (typeof window !== 'undefined' && window.location.port && window.location.port !== '8000') 
  ? 'http://127.0.0.1:8000' 
  : '';
const MERCHANT_API_KEY = import.meta.env?.VITE_MERCHANT_API_KEY || '';

async function request(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint}`;
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  // Optionally attach Merchant API Key if configured in development environment
  if (MERCHANT_API_KEY) {
    headers['X-API-Key'] = MERCHANT_API_KEY;
  }

  const config = {
    ...options,
    headers,
  };

  try {
    const response = await fetch(url, config);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const errorMsg = data?.detail?.message || data?.message || response.statusText || 'Request failed';
      throw new Error(errorMsg);
    }
    return data;
  } catch (err) {
    console.error(`API Error on ${endpoint}:`, err);
    throw err;
  }
}

export const api = {
  // Dashboard Stats
  getStats: () => request('/recovery/stats'),

  // Recovery Cases
  getCases: (limit = 100) => request(`/recovery/cases?limit=${limit}`),
  getCaseDetails: (caseId) => request(`/recovery/cases/${caseId}`),

  // Global Audit Trail
  getAuditEvents: (limit = 100, caseId = null) => {
    const q = caseId ? `?limit=${limit}&case_id=${encodeURIComponent(caseId)}` : `?limit=${limit}`;
    return request(`/recovery/audit-events${q}`);
  },


  // Human Actions
  approveCase: (caseId, approver = 'merchant_admin', notes = '') =>
    request(`/recovery/cases/${caseId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approver, notes }),
    }),

  rejectCase: (caseId, approver = 'merchant_admin', reason = '') =>
    request(`/recovery/cases/${caseId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ approver, reason }),
    }),

  // Customer Notifications (Test-Safe Mock)
  notifyCustomer: (caseId, recipient, channel = 'EMAIL') =>
    request(`/recovery/cases/${caseId}/notify`, {
      method: 'POST',
      body: JSON.stringify({ recipient, channel }),
    }),

  // System Diagnostics
  getHealth: () => request('/health'),
  testRazorpay: () => request('/razorpay-test'),

  // Batch Recovery Evaluation (Simulation / Benchmark)
  getLatestEvaluation: () => request('/evaluation/latest'),
  runEvaluation: (num_cases = 100, seed = 42, mode = 'deterministic') =>
    request('/evaluation/run', {
      method: 'POST',
      body: JSON.stringify({ num_cases, seed, mode }),
    }),

  // Contextual Recovery Intelligence Evaluation (Milestone C)
  getLatestContextualEvaluation: () => request('/evaluation/contextual'),
  runContextualEvaluation: () =>
    request('/evaluation/contextual/run', {
      method: 'POST',
    }),
};

