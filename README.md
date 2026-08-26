# AI Revenue Recovery Agent (Razorpay Track 03)

An AI-assisted revenue recovery system for failed Razorpay payments where AI recommends a recovery strategy, deterministic policy governs what can execute, Razorpay handles payment execution, and every important state transition is auditable.

---

## 💡 Why This Problem Matters

Payment failures in online commerce are a major source of revenue leakage, yet recovering them is non-trivial:

* **Aggressive Automation Risks**: Naive auto-retrying or spamming customers with payment links annoys legitimate buyers, damages brand trust, and risks chargebacks.
* **Passive Abandonment Costs**: Doing nothing leaves potentially recoverable failed payments unresolved.
* **Complex Multi-Signal Context**: A failed transaction is not a single error code—it involves customer tenure, transaction size, failure history, risk classifications, and payment methods.
* **The Engineering Challenge**: Effective recovery requires balancing **contextual intelligence** (diagnosing *why* a failure occurred and *how* to approach the customer) with **strict deterministic financial safety** (ensuring AI cannot overcharge, bypass fraud blocks, exceed retry budgets, or double-count reconciled revenue).

---

## 🏛️ System Architecture

```text
Razorpay Webhook (payment.failed)
        ↓
[ Layer 1: Cryptographic Authentication ] ── HMAC-SHA256 Verification & Event-Level Idempotency
        ↓
[ Layer 2: Ingestion & Failure Context ] ──── Unified Case Record + Multi-Attempt Tracking
        ↓
[ Layer 3: Error Classification ] ────────── 9 Standard Payment Failure Categories
        ↓
[ Layer 4: AI Contextual Reasoning ] ─────── Google Gemini (Sanitized Prompt, Contextual Reasoning)
        ↓
[ Layer 5: Deterministic Policy Authority ] ── Hard Guardrails: ₹50k+ Approval, Fraud Halt, Max 3 Retries
        ↓
[ Layer 6: Guarded Execution Engine ] ────── Authoritative Amount Binding & Sibling Link Voiding
        ↓
Razorpay Payment Link (Test Mode)
        ↓
Customer Payment Webhook (payment.captured / payment_link.paid)
        ↓
[ Layer 7: Financial Reconciliation ] ────── Idempotent Ledger Update & Automatic Sibling Cancellation
        ↓
Recovered Revenue + Chronological Audit Trail
```

---

## 🧠 AI Judgment: What AI Does vs What Deterministic Code Controls

> **Core Principle: AI recommends. Policy governs. Execution enforces.**

The system uses LLMs where unstructured reasoning and contextual prioritization add value, but deliberately restricts the model from having direct financial execution authority.

| Decision / Operational Boundary | Handled By | Why This Boundary Exists |
| :--- | :---: | :--- |
| **Failure Diagnosis & Contextual Analysis** | **AI (Gemini)** | Evaluates customer profile, tenure, prior successes, and failure descriptions to suggest an optimal approach. |
| **Strategy & Tone Recommendation** | **AI (Gemini)** | Suggests discrete recovery strategy (`SEND_PAYMENT_LINK`, `SEND_INVOICE`, `WAIT`, `NO_ACTION`, `INVESTIGATE`). |
| **High-Value Gating ($\ge$ ₹50,000)** | **Deterministic Policy** | High-value payments unconditionally mandate merchant approval. The LLM cannot override this rule. |
| **Fraud & Security Halts** | **Deterministic Policy** | Stolen card or compliance errors (`FRAUD_OR_SECURITY`) force `NO_ACTION` and block automated outreach. |
| **Retry Exhaustion Limits** | **Deterministic Policy** | Enforces a hard stop after $\ge 3$ failed attempts (`MAX_FAILED_ATTEMPTS = 3`), automatically cancelling open links. |
| **Financial Execution & Link Issuance** | **Deterministic Engine** | Links are generated strictly via authenticated Razorpay SDK calls with amounts bound from the database case. |
| **Payment Reconciliation** | **Deterministic Engine** | Reconciles captured payments idempotently based on cryptographically signed Razorpay webhook payloads. |
| **Sibling Link Cancellation** | **Deterministic Engine** | Voiding open secondary links upon payment is executed deterministically to prevent duplicate customer charges. |

---

## 🛡️ Financial Integrity & Safety Controls

1. **Cryptographic Webhook Authentication**: All incoming webhook bodies are verified using HMAC-SHA256 with constant-time signature comparison (`hmac.compare_digest`) before any JSON parsing or database write.
2. **Event-Level Idempotency**: Webhook `event_id` tracking ensures repeated deliveries of the same webhook return `already_processed` without duplicating case state or financial metrics.
3. **One Active Payment Path**: If a case already has an active Payment Link, the execution engine preserves the existing link rather than creating duplicate open obligations.
4. **Bidirectional Sibling Link Cancellation**: When a customer pays any recovery link for an order, all remaining open links for that case are immediately cancelled via the Razorpay API.
5. **Authoritative Amount Binding**: Link amounts and currencies derive strictly from verified server-side case records; client-side inputs cannot alter recovery amounts.
6. **Reconciliation Idempotency**: Once marked `recovered`, subsequent capture events for the same case return `already_recovered` with zero duplicate revenue addition.

---

## 🛠️ Engineering Challenges & Failure Recovery

During development, several non-trivial failures across systems boundaries were investigated, root-caused, and repaired:

```text
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. NON-DETERMINISTIC EVALUATION BENCHMARK                                                                   │
│    Symptom:      Running the identical Seed-42 benchmark produced 30.8% in one run and 41.3% in a subsequent│
│                  run, despite identical decision inputs and safety classifications.                         │
│    Root Cause:   Per-case RNG seeding relied on Python's process-randomized built-in hash() function, which is│
│                  unsuitable for reproducible RNG seeding across independent processes.                      │
│    Fix:          Replaced process-dependent hash() with deterministic SHA-256 seed generation.              │
│    Verification: Verified exact mathematical reproducibility across isolated runs (Canonical 36.6% baseline).│
├─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 2. DASHBOARD DID NOT REFRESH AFTER TEST PAYMENT                                                             │
│    Symptom:      New Razorpay Test Mode payments were captured, but financial KPIs remained unchanged.      │
│    Root Cause:   Traced the complete pipeline (Razorpay -> Cloudflare tunnel -> FastAPI -> SQLite -> React).│
│                  Discovered the ephemeral Cloudflare Quick Tunnel had expired after a restart, causing      │
│                  Razorpay webhooks to hit a dead endpoint. The Dashboard database was entirely correct.     │
│    Fix:          Restored the active tunnel and updated Razorpay webhook settings.                          │
│    Verification: Confirmed end-to-end webhook delivery, SQLite case ingestion, and instant UI refresh.      │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 3. SELF-HEALING PAYMENT LINK URL RESOLUTION                                                                 │
│    Symptom:      A recovery case contained a valid payment_link_id but payment_link_url was NULL.            │
│    Root Cause:   The initial webhook arrived during account credential bootstrapping, failing initial URL   │
│                  fetch and storing NULL. The case-detail endpoint previously returned raw stored columns.   │
│    Fix:          Added on-demand self-healing lazy resolution in GET /recovery/cases/{id}. When a missing    │
│                  URL is detected on a valid link ID, the backend fetches the short_url read-only from       │
│                  Razorpay and safely persists ONLY payment_link_url without altering case state.             │
│    Verification: Verified live case (case_order_TUWjnc8gGMZDOw -> https://rzp.io/rzp/JFFeAAh), zero state   │
│                  mutation, cached subsequent queries, and 4 focused unit tests.                             │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 4. EXHAUSTED CASE STEPPER STATE CONTRADICTION                                                               │
│    Symptom:      A 3-attempt retry-exhaustion case displayed "Link Issued" after "Automation Stopped".       │
│    Root Cause:   The UI lifecycle stepper treated the historical presence of payment_link_url as an active  │
│                  step, even though the link had been cancelled upon exhaustion.                             │
│    Fix:          Decoupled the active operational stepper from historical audit logs. Terminal exhausted     │
│                  states now terminate cleanly at "Policy Authority: Automation Stopped".                    │
│    Verification: Confirmed visual state termination while preserving all historical link events in the audit│
│                  trail.                                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

> **Design Refinement Note**: During Dashboard polish, an attempt was made to enlarge typography on Cards 3 and 4 to fill whitespace. Inspection revealed this distorted visual balance without adding operational value. The change was rejected and replaced with structured contextual footers (`1 case awaiting merchant review` / `₹2.69L recovered · ₹5.57L processed`).

---

## 📊 Data Tiers & Canonical Evidence Isolation

The system strictly enforces physical separation between operational merchant records and synthetic simulation benchmarks:

```text
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. OPERATIONAL LEDGER (data/recovery.db)                                                                    │
│    * Genuine Razorpay Test Mode webhook events, test captures, and live Razorpay payment links.             │
│    * Curated demo scenario cases covering all 9 failure categories for interactive UI walkthroughs.         │
│    * Powers the Merchant Recovery Dashboard (GET /recovery/stats, GET /recovery/cases).                     │
│    * Observed in Razorpay Test Mode; not real merchant money.                                               │
├─────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 2. ISOLATED SIMULATION SANDBOX (data/evaluation.db)                                                         │
│    * Controlled benchmark datasets: 100-case synthetic batch evaluation & 16-case contextual evaluation.     │
│    * Strictly persisted in a separate database file.                                                        │
│    * ZERO interaction with operational dashboard metrics or live Razorpay APIs.                             │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📈 Canonical Evidence Scorecard

### A. Real Razorpay Test Mode Proof (`data/recovery.db`)
* **Real Operational Recovery**: **11 recovered Test Mode cases** totaling **₹2,53,863.30** (accounting for **94.4%** of all recovered revenue in the operational ledger).
* **Payment Lifecycle**: Real `payment.failed` webhook $\rightarrow$ recovery link created $\rightarrow$ payer completed in Test Mode $\rightarrow$ `payment.captured` reconciliation.
* **Retry Exhaustion**: Verified on real 3-attempt failure case (`case_order_TU4S0Jyoa0yEGc`, ₹7,859) with automatic link cancellation and execution halt.
* **Active Demo Link**: Verified active Test Mode Payment Link (`case_order_TUWjnc8gGMZDOw`, ₹1,001) available for live completion during walkthroughs.

### B. 100-Case Synthetic Recovery Benchmark (`data/evaluation.db`, Seed 42, Run `eval_run_20260825_172822_llm_42`)
* **Total Evaluated Cases**: 100 cases (balanced across 9 failure categories).
* **Simulated Historical Exposure**: ₹1,561,712.61.
* **Simulated Recovered Revenue**: ₹480,965.86.
* **Simulated Recovery Rate**: 30.8% (Historical run preserved; Canonical deterministic baseline: 36.6%).
* **Simulated Human-Approved Recovery**: ₹114,347.17 (44 cases gated for merchant review).
* **Simulated Blocked Fraud Exposure**: ₹176,576.76 (11 cases, 100% compliance halted, 0 leakage).
* **Simulated Retry Exhaustion Stops**: ₹417,628.21 (17 cases stopped after $\ge 3$ failed attempts).
* **Financial Policy Violations**: 0 (100% safety compliance).

### C. 16-Case Synthetic Contextual Intelligence Evaluation (`data/evaluation.db`, Run `ctx_eval_20260824_214431`)
* **Evaluated Scenarios**: 16 multi-signal cases (Scenarios A through P).
* **Policy Action Safety Agreement**: 100.0% (16/16).
* **Priority Alignment**: 75.0% (12/16).
* **Escalation Alignment**: 75.0% (12/16).
* **Explanation Quality**: 3.12 / 4.0 average rubric score.

> **Simulation Limitation Disclosure**: Synthetic recovery outcomes are generated using predefined category and conversion assumptions within a controlled benchmark harness; they do not represent live customer payments.

---

## 🛡️ Security Architecture

* **Mutation Endpoint Authentication**: Administrative endpoints (`/recovery/execute`, `/approve`, `/reject`, `/notify`) are protected via `require_merchant_auth` using constant-time `hmac.compare_digest` header validation.
* **Rate Limiting**: Sliding-window in-memory rate limiter protects endpoints from burst traffic (30 req/min) returning HTTP 429 with `Retry-After`.
* **Payload Size Protection**: Middleware enforces a 512 KB request ceiling (`MAX_REQUEST_BODY_SIZE_BYTES`), preventing memory exhaustion attacks.
* **Error Sanitization**: Unhandled server exceptions return sanitized JSON responses with internal reference IDs (`err_...`), preventing database paths or stack traces from leaking to clients.
* **SQL Injection Immunity**: 100% parameterized SQLite queries across all CRUD operations.

---

## 📁 Project Structure

```text
Razorpay Track 03/
├── app/
│   ├── __init__.py            # Python package initialization
│   ├── config.py              # Centralized environment settings & validation
│   ├── security.py            # API key authentication & sliding-window rate limiter
│   ├── razorpay_client.py     # Razorpay Test Mode SDK client & HMAC verification
│   ├── revenue_risk.py        # Revenue-at-Risk calculation & metadata extractors
│   ├── failure_classifier.py  # 9-category payment failure classifier
│   ├── recovery_decision.py   # Bounded deterministic policy decision engine
│   ├── recovery_executor.py   # Payment Link creator, canceller & error sanitizer
│   ├── ai_recovery_agent.py   # Gemini AI reasoning agent with deterministic fallback
│   ├── notification_service.py# Anti-spam mock customer notification dispatcher
│   ├── demo_dataset.py        # Deterministic demo scenario generator
│   ├── database.py            # SQLite state persistence & WAL mode configuration
│   ├── evaluation_engine.py   # Synthetic batch evaluation harness
│   ├── contextual_evaluator.py# 16-case contextual AI evaluation engine
│   └── main.py                # FastAPI application, security middleware & REST routes
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
│   ├── run_batch_evaluation.py# Batch evaluation runner
│   └── run_contextual_evaluation.py # Contextual evaluation runner
├── data/                      # SQLite storage (recovery.db & evaluation.db, gitignored)
├── tests/                     # 200 Automated unit and integration tests (100% passing)
├── .env.example               # Sanitized template for environment variables
├── requirements.txt           # Python backend dependencies
└── README.md                  # Engineering documentation
```

---

## ⚙️ Environment Configuration

Copy the example environment file to create your local `.env`:

```powershell
cp .env.example .env
```

| Variable | Description |
| :--- | :--- |
| `RAZORPAY_KEY_ID` | Razorpay Test Mode Key ID (e.g. `rzp_test_...`). |
| `RAZORPAY_KEY_SECRET` | Razorpay Test Mode Key Secret for authenticated API operations. |
| `RAZORPAY_WEBHOOK_SECRET` | Webhook secret for HMAC-SHA256 signature verification. |
| `GEMINI_API_KEY` | Google Gemini API Key (Free Tier supported). Enables contextual AI reasoning. |
| `MERCHANT_API_KEY` | Optional security key protecting administrative mutation endpoints. |
| `ALLOWED_ORIGINS` | Comma-separated list of allowed CORS origins (default: `http://localhost:5173,http://localhost:8000`). |

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

The complete backend regression test suite contains **200 automated tests** (100% passing):

```powershell
.\venv\Scripts\python.exe -m unittest discover tests
```

---

## 🔗 Real Razorpay Test Mode Webhook Integration

> **⚠️ TEST MODE ONLY** — All workflows operate under Razorpay Test Mode. No real money or real customer payments are affected.

1. **Start the Backend Server**:
   ```powershell
   uvicorn app.main:app --reload --port 8000
   ```
2. **Expose Localhost via Public HTTPS Tunnel**:
   ```powershell
   cloudflared tunnel --url http://localhost:8000
   ```
3. **Configure Webhook in Razorpay Dashboard**:
   * Navigate to **Account & Settings** $\rightarrow$ **Webhooks** $\rightarrow$ **+ Add New Webhook**.
   * Webhook URL: `https://<your-tunnel-url>/webhooks/razorpay`.
   * Enable events: `payment.failed`, `payment.captured`, `payment_link.paid`.
   * Configure `RAZORPAY_WEBHOOK_SECRET` in your `.env`.
4. **Trigger Payment Failure & Complete Recovery**:
   * Simulate a test failure via checkout or Swagger (`/revenue-risk/analyze`).
   * Observe the automated classification, AI contextual recommendation, and generated Payment Link in the dashboard (`/dashboard`).
   * Complete payment using [Razorpay Test Cards](https://razorpay.com/docs/payments/payments/test-card-upi-details/).
   * The incoming `payment.captured` webhook automatically reconciles the case to **recovered** status.

---

## 🔮 Engineering Boundaries & Future Extensions

### Current System Scope
* **Razorpay Test Mode**: Operates within Razorpay sandbox rails.
* **Development Webhook Tunneling**: Ephemeral Cloudflare Quick Tunnel used for local development; production deployment would use static ingress endpoints.
* **Process-Local Rate Limiting**: In-memory sliding window; production multi-worker clusters would use Redis.

### Future Architectural Adapters
* **Razorpay Subscription Recovery**: Automated dunning and smart retry scheduling via Razorpay Subscriptions API.
* **Magic Checkout Abandonment**: Detecting abandoned checkout sessions and issuing targeted recovery links.
* **Multi-Channel Dispatch**: WhatsApp / SMS recovery notifications with merchant template approval.
