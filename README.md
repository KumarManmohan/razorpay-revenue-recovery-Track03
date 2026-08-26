# AI Revenue Recovery Agent (Razorpay Track 03)

An intelligent revenue-recovery system that detects payment revenue at risk, diagnoses failed-payment scenarios, recommends bounded recovery actions using Google Gemini with authoritative deterministic guardrails, executes controlled recovery operations via Razorpay Test Mode, and maintains an auditable payment lifecycle.

---

## 🏛️ System Architecture

```text
Razorpay Webhooks
       ↓
Webhook Authentication / Idempotency
       ↓
Recovery Case + Payment Attempts
       ↓
Failure Classification (9 Error Categories)
       ↓
Sanitized Context Builder
       ↓
Gemini Contextual Decisioning
       ↓
Deterministic Policy Guardrails (₹50k+ Approval / Fraud Blocks)
       ↓
Recovery Executor (Bounded Actions Only)
       ↓
Razorpay Payment Link (Test Mode)
       ↓
Payment Webhook (payment.captured / payment_link.paid)
       ↓
Reconciliation Engine
       ↓
Recovered Revenue + Chronological Audit Trail
```

---

## 📁 Project Structure

```text
Razorpay Track 03/
├── app/
│   ├── __init__.py            # Makes 'app' a Python package
│   ├── config.py              # Loads and validates environment variables
│   ├── security.py            # API key authentication & sliding-window rate limiter
│   ├── razorpay_client.py     # Razorpay Test Mode client & HMAC signature verification
│   ├── revenue_risk.py        # Revenue-at-Risk calculation engine
│   ├── failure_classifier.py  # 9-category payment failure classifier
│   ├── recovery_decision.py   # Bounded deterministic policy decision engine
│   ├── recovery_executor.py   # Test Mode Payment Link creator & error sanitizer
│   ├── ai_recovery_agent.py   # Gemini AI reasoning agent with deterministic guardrails
│   ├── notification_service.py# Test-safe mock customer notifications & anti-spam
│   ├── demo_dataset.py        # Deterministic demo scenario generator
│   ├── database.py            # SQLite state persistence & WAL mode configuration
│   ├── evaluation_engine.py   # Synthetic batch evaluation harness
│   ├── contextual_evaluator.py# 16-case contextual AI evaluation engine
│   └── main.py                # FastAPI application, security middleware & REST endpoints
├── frontend/                  # React 19 + Vite Merchant Dashboard
│   ├── src/
│   │   ├── components/        # Header, Sidebar, StatsOverview, CasesTable,
│   │   │                      # CaseDetailModal, AuditLogView, EvaluationView, Toast
│   │   ├── api.js             # REST API Client with optional X-API-Key forwarding
│   │   ├── App.jsx            # Main Dashboard Application
│   │   └── index.css          # Modern light fintech design system
│   └── dist/                  # Production build assets
├── scripts/
│   ├── seed_demo_data.py      # Demo dataset seeding script
│   ├── inspect_real_cases.py  # Diagnostic script for real Razorpay test cases
│   ├── run_batch_evaluation.py# Milestone B evaluation runner
│   └── run_contextual_evaluation.py # Milestone C contextual evaluation runner
├── data/                      # SQLite storage (recovery.db & evaluation.db, gitignored)
├── tests/                     # 200 Automated unit and integration tests (100% passing)
├── .env                       # Local environment variables (never committed)
├── .env.example               # Template for environment variables
├── requirements.txt           # Python backend dependencies
└── README.md                  # System documentation
```

---

## ⚙️ Environment Configuration

Copy the example environment file to create your local `.env`:

```powershell
cp .env.example .env
```

### Configuration Variables

| Variable | Description |
| :--- | :--- |
| `RAZORPAY_KEY_ID` | Razorpay Test Mode Key ID (e.g. `rzp_test_...`). |
| `RAZORPAY_KEY_SECRET` | Razorpay Test Mode Key Secret. Used for authenticated SDK operations. |
| `RAZORPAY_WEBHOOK_SECRET` | Webhook secret configured in Razorpay Dashboard for HMAC-SHA256 signature verification. |
| `GEMINI_API_KEY` | Google Gemini API Key (Free Tier supported). Enables contextual AI recovery decisioning. |
| `MERCHANT_API_KEY` | Optional security key protecting administrative mutation endpoints (`/recovery/execute`, `/approve`, `/reject`, `/notify`). |
| `ALLOWED_ORIGINS` | Comma-separated list of allowed CORS origins (default: `http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000`). |

> **IMPORTANT**:
> * Real secrets belong **only** in your local `.env` file.
> * The `.env` file is excluded in `.gitignore` and must **never** be committed to source control.
> * `.env.example` contains sanitized placeholders only.

---

## 🧠 AI & Deterministic Safety Configuration

* **Gemini LLM Integration**: Google Gemini (`gemini-2.5-flash`) acts as the reasoning engine to evaluate customer context (tenure, previous successful payments, retry count, error history).
* **Deterministic Fallback**: If `GEMINI_API_KEY` is not provided, the model times out, or API quota is reached, the system automatically falls back to deterministic decision rules without disruption.
* **Authoritative Server Guardrails**: The LLM is sandboxed and only suggests bounded actions (`SEND_PAYMENT_LINK`, `SEND_INVOICE`, `WAIT`, `NO_ACTION`, `INVESTIGATE`):
  * **`SEND_PAYMENT_LINK`**: Primary demonstrated executable recovery mechanism via Razorpay Test Mode Payment Links.
  * **`WAIT`**: Passive / deferred recovery hold that prevents immediate link creation while waiting for a subsequent payment event or merchant intervention (not an autonomous scheduled background retry).
  * **`SEND_INVOICE`**: Advisory invoice strategy for B2B/high-value corporate failures (mock / evaluation-only; not part of the demonstrated Razorpay Test Mode Payment Link execution path).
  * **`NO_ACTION`**: Permanent execution halt for suspected fraud or exhausted retry limits.
  * **`INVESTIGATE`**: Merchant review queue for missing or indeterminate metadata.
* **Hard Policy Constraints**:
  * Mandatory human approval for high-value cases ($\ge$ ₹50,000).
  * Immediate execution block (`NO_ACTION`) for suspected fraud or stolen card failures.
  * Deterministic retry exhaustion stopping rules (`MAX_FAILED_ATTEMPTS = 3`, `MAX_IGNORED_RECOVERY_LINKS = 2`).
  * The LLM has zero direct access to Razorpay execution tools or API secrets.

---

## 📊 Data Tiers & Canonical Evidence Isolation

The system distinguishes three distinct data tiers:

1. **Razorpay Test Mode (Operational Proof)**:
   * Genuine Razorpay Test Mode webhook events, test payments, and real Razorpay payment links (`plink_...`).
   * Stored in `data/recovery.db`.
   * Proves end-to-end integration (failure $\rightarrow$ AI decision $\rightarrow$ payment link $\rightarrow$ payment $\rightarrow$ reconciliation).
   * **Observed in Razorpay Test Mode; not real merchant money.**
2. **Demo Scenarios (Operational UI Walkthrough)**:
   * Seeded synthetic cases covering all 9 failure categories for interactive dashboard walkthroughs.
   * Stored in `data/recovery.db` alongside test mode records.
3. **Evaluation / Benchmark (Isolated Simulation)**:
   * Isolated benchmark datasets (100-case synthetic batch evaluation & 16-case contextual evaluation).
   * Strictly persisted in a separate database (`data/evaluation.db`).
   * **Zero interaction with operational ledgers or real Razorpay APIs.**

---

## 📈 Canonical Evidence Scorecard

### A. Real Razorpay Test Mode Proof (`data/recovery.db`)
* **Observed Real Transactions**: 3 test cases totaling ₹51,441.00 (`case_pay_TT0g8mGaP6dv1S` ₹850, `case_order_TTJcCYBHmCjzW7` ₹589, `case_order_TTgKEsWWMViDKP` ₹50,002).
* **Payment Lifecycle**: Real `payment.failed` webhook $\rightarrow$ recovery link created $\rightarrow$ payer completed in Test Mode $\rightarrow$ `payment.captured` reconciliation.
* **Duplicate Payment Protection**: Verified on real ₹589 double-payment scenario (zero revenue double-counting).
* **Sibling Link Cancellation**: Verified bidirectional cancellation on ₹50,002 high-value order.

### B. 100-Case Synthetic Recovery Benchmark (`data/evaluation.db`, Seed 42, Run `eval_run_20260825_172822_llm_42`)
* **Total Evaluated Cases**: 100 cases (balanced across 9 failure categories).
* **Simulated Historical Exposure**: ₹1,561,712.61.
* **Simulated Recovered Revenue**: ₹480,965.86.
* **Simulated Recovery Rate**: 30.8%.
* **Simulated Automated Recovery (< ₹50k)**: ₹366,618.69.
* **Simulated Human-Approved Recovery**: ₹114,347.17 (44 cases gated for merchant review).
* **Simulated Blocked Fraud Exposure**: ₹176,576.76 (11 cases, 100% compliance halted, 0 leakage).
* **Simulated Retry Exhaustion Stops**: ₹417,628.21 (17 cases stopped after $\ge 3$ failed attempts).
* **Total Automated Stops**: ₹594,204.97 (28 cases).
* **Financial Policy Violations**: 0 (100% safety compliance).

### C. 16-Case Synthetic Contextual Intelligence Evaluation (`data/evaluation.db`, Run `ctx_eval_20260824_214431`)
* **Evaluated Scenarios**: 16 multi-signal cases (Scenarios A through P).
* **Policy Action Safety Agreement**: 100.0% (16/16).
* **Priority Alignment**: 75.0% (12/16).
* **Escalation Alignment**: 75.0% (12/16).
* **Explanation Quality**: 3.12 / 4.0 average rubric score.

> **Simulation Limitation & Independence Disclosure**:
> Outcome generation is completely independent of the model's self-reported confidence. Confidence is evaluated separately rather than being used to scale simulated recovery probability. Synthetic recovery outcomes are generated using predefined category and action conversion assumptions; they represent a controlled simulation harness and are not observed live customer payment behavior.

---

## 🚀 Quickstart Guide

### 1. Install Backend Dependencies & Start Server
```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 2. Install Frontend Dependencies & Start Dashboard
```powershell
cd frontend
npm install
npm run dev
```

### 3. Production Frontend (Served directly by FastAPI)
```powershell
cd frontend
npm run build
# Then open http://127.0.0.1:8000/dashboard
```

---

## 🔍 Accessing the Application

* **Merchant Recovery Dashboard**: [http://localhost:5173](http://localhost:5173) (Dev) or [http://127.0.0.1:8000/dashboard](http://127.0.0.1:8000/dashboard) (Built)
* **Interactive API Documentation (Swagger)**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
* **Health Check Endpoint**: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
* **KPI Metrics API**: [http://127.0.0.1:8000/recovery/stats](http://127.0.0.1:8000/recovery/stats)

---

## 🧪 Running Automated Tests

The backend regression test suite contains 200 automated tests and is 100% passing:

```powershell
.\venv\Scripts\python.exe -m unittest discover tests
```

---

## 🔗 Real Razorpay Test Mode Webhook Integration

> **⚠️ TEST MODE ONLY** — All workflows operate under Razorpay Test Mode. No real money or real customer payments are affected.

### Step 1 — Start the Backend Server
```powershell
.\venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```

### Step 2 — Expose Localhost via Public HTTPS Tunnel
```powershell
cloudflared tunnel --url http://localhost:8000
```
*Copies public HTTPS URL (e.g. `https://example.trycloudflare.com`).*

### Step 3 — Configure Webhook in Razorpay Dashboard
1. Go to [Razorpay Dashboard](https://dashboard.razorpay.com) → switch to **Test Mode** (toggle in top-right).
2. Navigate to **Account & Settings** → **Webhooks** → **+ Add New Webhook**.
3. Set Webhook URL: `https://<your-tunnel-url>/webhooks/razorpay`.
4. Enter your chosen **Webhook Secret**.
5. Enable events: `payment.failed`, `payment.captured`, `payment_link.paid`.
6. Set `RAZORPAY_WEBHOOK_SECRET` in your local `.env`.

### Step 4 — Simulate Payment Failure & Complete Recovery
1. Trigger a test failure via checkout or Swagger (`/revenue-risk/analyze`).
2. Observe the automated classification, AI contextual decision, and generated Payment Link in the dashboard (`/dashboard`).
3. Complete the payment using [Razorpay Test Cards](https://razorpay.com/docs/payments/payments/test-card-upi-details/).
4. The incoming `payment.captured` webhook automatically reconciles the case to **recovered** status.

---

## 🔮 Future Extensions

The current system focuses specifically on failed payment revenue recovery via Razorpay Payment Links. Future adapters could extend the same core state machine, governance guardrails, and audit ledger to:

* **Razorpay Subscription Recovery**: Automated dunning and smart retry scheduling via Razorpay Subscriptions API.
* **Magic Checkout Cart Abandonment**: Detecting abandoned checkout sessions and issuing targeted recovery links.
* **Multi-Channel Dispatch**: Official WhatsApp / SMS recovery notifications with merchant template approval.

