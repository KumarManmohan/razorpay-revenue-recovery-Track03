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

## 🔬 Engineering Journey & Key Decisions

This project is an **AI-assisted revenue recovery system for Razorpay merchants**. It processes failed payment webhooks by combining contextual AI evaluation with strict deterministic policy governance, automated execution, category-aware customer outreach, and webhook-driven financial reconciliation.

```text
Payment Failure → Failure Analysis → Gemini Recommendation → Deterministic Policy
→ Recovery Execution → Customer Communication → Razorpay Webhook → Reconciliation → Audit
```

During iterative development and live Test Mode validation, several critical system-level discoveries shaped the final architecture:

1. **Dual Payment Entry Paths**: Failed payments originate either from direct store checkout (`client.order.create`) or pre-existing Razorpay Payment Links. The engine dynamically issues a fresh link (`PAYMENT_LINK_CREATED`) only when no active path exists; for existing payment links, it preserves the active link (`PAYMENT_PATH_PRESERVED`) to eliminate duplicate customer obligations.
2. **Decoupled Automation State vs. Financial Outcome**: Testing revealed that automation stopping upon retry exhaustion (`RECOVERY_EXHAUSTED`) is distinct from financial reconciliation. If a customer subsequently completes a payment via an in-flight checkout, the system safely records `Automation Stopped + Financially Recovered` without double-counting revenue.
3. **Actionable Current State vs. Historical Metadata**: Historical approval metadata previously leaked resolved cases into the merchant review tab. The queue was re-architected so *Awaiting Review* strictly reflects cases currently requiring merchant intervention, excluding terminal or recovered records.
4. **Bounded Customer Communication**: Free-form LLM-generated customer outreach was rejected in favor of deterministic, category-aware templates (e.g., bank decline vs. insufficient funds) paired with anti-spam deduplication (`NOTIFICATION_BLOCKED_DUPLICATE`). Outreach operates under `MockNotificationProvider` for safe local simulation.
5. **Dashboard State Synchronization**: To eliminate manual browser refreshes after asynchronous webhook arrivals, a centralized 5-second polling loop with Page Visibility lifecycle management was implemented, avoiding the operational complexity of WebSockets or SSE for this deployment tier.
6. **Authoritative Source of Truth**: Local browser-side checkout callbacks can fire in simulation without server-side settlement. The system treats verified Razorpay `payment.captured` webhooks as the authoritative server-side signal used to confirm financial recovery.

The implementation is verified by **210 automated backend tests (100% passing)** and validated across live Razorpay Test Mode payment flows. All operations run strictly under Test Mode with mock notifications and zero live financial exposure.

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
[ Layer 6: Guarded Execution Engine ] ────── Dual Entry: Create New Link (Checkout) OR Preserve Link
        ↓
Razorpay Payment Link (Test Mode: Created / Preserved)
        ↓
[ Layer 7: Category-Aware Customer Outreach ] ─ Deterministic Templates + Anti-Spam (Mock/Test Mode)
        ↓
Customer Payment Webhook (payment.captured / payment_link.paid)
        ↓
[ Layer 8: Financial Reconciliation ] ────── Idempotent Ledger Update & Sibling Link Voiding
        ↓
Recovered Revenue + Chronological Audit Trail
```

---

## 💳 Supported Payment Entry Paths

The system seamlessly accommodates both direct checkout checkouts and pre-existing payment links:

```text
1. Direct Razorpay Checkout / Order
   Direct Order Checkout → payment failure → no active Payment Link detected 
   → Guarded Execution Engine creates a NEW Recovery Payment Link (if recovery permitted)

2. Existing Razorpay Payment Link
   Payment Link checkout → payment failure → active Payment Link detected 
   → Guarded Execution Engine PRESERVES existing active link (zero duplicate link creation)
```

* **No Unnecessary Link Duplication**: If an active link is already attached to the case, the system reuses and preserves that link (`PAYMENT_PATH_PRESERVED`).
* **Dynamic Generation Only When Needed**: A fresh Razorpay Payment Link (`PAYMENT_LINK_CREATED`) is issued exclusively when no active payment path exists (such as standard direct web store orders).

---

## 🧠 AI Judgment: What AI Does vs What Deterministic Code Controls

> **Core Principle: AI recommends. Policy governs. Execution enforces. Notification communicates.**

The system uses LLMs where unstructured reasoning and contextual prioritization add value, but deliberately restricts the model from having direct financial execution or unrestricted communication authority.

| Decision / Operational Boundary | Handled By | Why This Boundary Exists |
| :--- | :---: | :--- |
| **Failure Diagnosis & Contextual Analysis** | **AI (Gemini)** | Evaluates customer profile, tenure, prior successes, and failure descriptions to suggest an optimal approach. |
| **Strategy & Tone Recommendation** | **AI (Gemini)** | Suggests discrete recovery strategy (`SEND_PAYMENT_LINK`, `SEND_INVOICE`, `WAIT`, `NO_ACTION`, `INVESTIGATE`). Model confidence represents an internal heuristic ranking, not a calibrated mathematical probability. |
| **High-Value Gating ($\ge$ ₹50,000)** | **Deterministic Policy** | High-value payments unconditionally mandate merchant human review. The LLM cannot override this rule. |
| **Fraud & Security Halts** | **Deterministic Policy** | Stolen card or compliance errors (`FRAUD_OR_SECURITY`) force `NO_ACTION` and block automated outreach. |
| **Retry Exhaustion Limits** | **Deterministic Policy** | Enforces a hard stop after $\ge 3$ failed attempts (`MAX_FAILED_ATTEMPTS = 3`), automatically cancelling open links. |
| **Deterministic Fallback** | **Deterministic Engine** | If Gemini API is unreachable, times out, or returns invalid schema, system falls back safely to deterministic rules with zero downtime. |
| **Financial Execution & Link Issuance** | **Deterministic Engine** | Links are generated strictly via authenticated Razorpay SDK calls with amounts bound from verified server-side records. |
| **Customer Communication & Guidance** | **Deterministic Notification Layer** | Customer messages use deterministic, category-aware templates (Mock/Test Mode) governed by policy and anti-spam controls. The LLM does not generate free-form customer copy. |
| **Payment Reconciliation** | **Deterministic Engine** | Reconciles captured payments idempotently based on cryptographically signed Razorpay webhook payloads. |
| **Sibling Link Cancellation** | **Deterministic Engine** | Voiding open secondary links upon payment is executed deterministically to prevent duplicate customer charges. |

---

## 📬 Category-Aware Customer Communication & Anti-Spam

The system implements a deterministic, category-aware customer communication layer (Mock/Test Mode):

* **Actionable Categories (Permitted Outreach)**:
  * `BANK_DECLINED`: Suggests retrying with an alternate bank or card.
  * `INSUFFICIENT_FUNDS`: Prompts checking account balance or switching to credit/UPI.
  * `CARD_LIMIT_EXCEEDED`: Suggests contacting card issuer or using netbanking.
  * `CARD_EXPIRED`: Prompts updating card expiry details or using a newer card.
  * `INVALID_CARD`: Prompts re-entering correct card credentials.
  * `AUTHENTICATION_REQUIRED`: Advises completing 3D Secure / OTP authentication.
* **Suppressed States (Zero Outreach)**: Automated outreach is strictly blocked for `TEMPORARY_GATEWAY_ERROR` (deferred hold), `FRAUD_OR_SECURITY` (compliance halt), `UNKNOWN` (investigation required), retry-exhausted cases, and unapproved high-value transactions ($\ge ₹50,000$).
* **Anti-Spam Deduplication**: Prevents duplicate customer outreach across repeated retries (`NOTIFICATION_BLOCKED_DUPLICATE`). At most one recovery communication is dispatched per case unless manually overridden by a merchant admin.
* **Test Mode Disclaimer**: Customer notifications are dispatched exclusively through `MockNotificationProvider`. No live emails, SMS, SendGrid, or WhatsApp messages are sent. `NOTIFICATION_SENT` represents successful dispatch through the local mock transport.

---

## 🛡️ Financial Integrity & Recovery Boundaries

1. **Cryptographic Webhook Authentication**: All incoming webhook bodies are verified using HMAC-SHA256 with constant-time signature comparison (`hmac.compare_digest`) before any JSON parsing or database write.
2. **Event-Level Idempotency**: Webhook `event_id` tracking ensures repeated deliveries of the same webhook return `already_processed` without duplicating case state or financial metrics.
3. **High-Value Merchant Approval**: Any recovery exceeding **₹50,000** is gated in the *Awaiting Review* queue until a human merchant explicitly approves or rejects the action.
4. **Retry Budget Exhaustion**: If a case accumulates 3 failed payment attempts (`MAX_FAILED_ATTEMPTS = 3`), automated recovery halts (`RECOVERY_EXHAUSTED`) and active recovery links are automatically cancelled to protect the customer.
5. **Decoupled Automation State vs. Financial Outcome**: Automation state and financial outcome are tracked separately. If a customer successfully pays after automation was halted, the system safely reconciles the capture (`Automation Stopped + Financially Recovered`) without double-counting revenue.
6. **Authoritative Amount Binding**: Link amounts and currencies derive strictly from verified server-side case records; client-side inputs cannot alter recovery amounts.
7. **Idempotent Double-Payment Protection**: If a second capture webhook arrives for an already-recovered case, the system records it as a duplicate attempt (`DUPLICATE_PAYMENT_DETECTED`) without double-counting recovered revenue.

---

## 📜 Chronological Audit Trail

Every state transition, policy decision, and financial action records an immutable audit log entry in the SQLite ledger:

```text
PAYMENT_FAILED                  ── Webhook received for failed payment attempt
RISK_ANALYZED                   ── Revenue-at-risk & category classification computed
RECOVERY_DECIDED                ── AI recommendation or deterministic policy evaluated
PAYMENT_LINK_CREATED            ── New Razorpay Test Mode Payment Link generated
PAYMENT_PATH_PRESERVED          ── Existing active Payment Link preserved without duplication
NOTIFICATION_SENT               ── Test customer communication dispatched (Mock Mode)
NOTIFICATION_BLOCKED_DUPLICATE  ── Duplicate outreach suppressed to prevent customer spam
RECOVERY_EXHAUSTED              ── Hard retry limit reached (automation halted)
RECOVERY_PAYMENT_DETECTED       ── Captured payment detected via webhook
RECOVERY_CASE_RECONCILED        ── Case reconciled with payment record in database
REVENUE_RECOVERED               ── Recovered revenue credited to merchant ledger
```

---

## 🖥️ Merchant Recovery Dashboard

* **Real-Time Automatic Background Refresh**: The dashboard continuously syncs with the backend via **5-second polling**.
* **Page Visibility Lifecycle**: Polling automatically pauses when the browser tab is hidden/minimized and immediately triggers a fresh fetch when the user returns to the tab.
* **Zero Page Flickering**: Updates are applied through React state reconciliation (`setStats`, `setCases`, `setSelectedCaseData`) without full-page reloads.
* **Live Case Detail Sync**: If a merchant has a Case Detail modal open when a payment captures, the modal updates its financial metrics, status badges, and timeline live.

---

## 📊 Data Tiers & Canonical Evidence Isolation

The system strictly enforces physical separation between operational merchant records and synthetic simulation benchmarks:

```text
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. OPERATIONAL LEDGER (data/recovery.db)                                                                    │
│    * Genuine Razorpay Test Mode webhook events, test captures, and active Razorpay Test Mode Payment Links. │
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
* **Verified Test Mode Recovery**: Over ₹2,74,000+ recovered across verified Razorpay Test Mode transactions.
* **Dual Payment Entry Validation**:
  * Direct Checkout failure $\rightarrow$ automatic creation of fresh Payment Link $\rightarrow$ successful customer recovery.
  * Existing Payment Link failure $\rightarrow$ preserved active link without duplication $\rightarrow$ successful customer recovery.
* **Retry Exhaustion & Safe Post-Exhaustion Reconciliation**: Verified on live 3-attempt failure cases (`RECOVERY_EXHAUSTED`), with subsequent legitimate capture reconciling financial metrics safely.
* **Awaiting Review Precision**: Authoritative exclusion of terminal, recovered, or exhausted cases from the approval queue.

### B. 100-Case Synthetic Recovery Benchmark (`data/evaluation.db`, Seed 42, Canonical Run `eval_run_20260826_173735_deterministic_42`)
* **Total Evaluated Cases**: 100 cases (balanced across 9 failure categories).
* **Simulated Total Exposure**: ₹1,561,712.61.
* **Simulated Recovered Revenue**: ₹571,587.12.
* **Canonical Simulated Recovery Rate**: **36.6%** on the reproducible Seed-42 evaluation dataset. Under deterministic SHA-256 simulation seeding, both LLM and deterministic evaluation modes produce the same canonical 36.6% simulated recovery rate on these identical synthetic cases.
* **Historical Pre-Fix Artifact**: An earlier run recorded 30.8% (`eval_run_20260825_172822_llm_42`, ₹480,965.86). That historical run was affected by Python's process-randomized built-in `hash()` function for simulation seeding (documented in Engineering Challenge #1) and is a development artifact rather than an AI-vs-deterministic performance comparison.
* **Simulated Human-Approved Recovery**: ₹114,347.17 (44 cases gated for merchant review).
* **Simulated Blocked Fraud Exposure**: ₹176,576.76 (11 cases, 100% compliance halted, 0 leakage).
* **Simulated Retry Exhaustion Stops**: ₹417,628.21 (17 cases stopped after $\ge 3$ failed attempts).
* **Financial Policy Violations**: 0 (100% safety compliance).

### C. 16-Case Synthetic Contextual Intelligence Evaluation (`data/evaluation.db`, Run `ctx_eval_20260824_214431`)
* **Evaluated Scenarios**: 16 multi-signal cases (Scenarios A through P: customer tenure, prior ignored links, transient vs. persistent outages).
* **Policy Safety Agreement**: 100.0% (16/16 compliance with deterministic financial guardrails).
* **Priority Alignment**: 75.0% (12/16 agreement with ground-truth business urgency).
* **Escalation Alignment**: 75.0% (12/16 agreement with human escalation recommendations).
* **Explanation Quality**: 3.12 / 4.0 average rubric score.

> **Simulation Limitation Disclosure**: Synthetic recovery outcomes are generated using predefined category and conversion assumptions within a controlled benchmark harness; they do not represent live customer payments or empirical production recovery rates.

---

## 🛡️ Security Architecture

* **Mutation Endpoint Authentication**: Administrative endpoints (`/recovery/execute`, `/approve`, `/reject`, `/notify`) are protected via `require_merchant_auth` using constant-time `hmac.compare_digest` header validation.
* **Rate Limiting**: Sliding-window in-memory rate limiter protects endpoints from burst traffic (30 req/min) returning HTTP 429 with `Retry-After`.
* **Payload Size Protection**: Middleware enforces a 512 KB request ceiling (`MAX_REQUEST_BODY_SIZE_BYTES`), preventing memory exhaustion attacks.
* **Error Sanitization**: Unhandled server exceptions return sanitized JSON responses with internal reference IDs (`err_...`), preventing database paths or stack traces from leaking to clients.
* **SQL Injection Immunity**: 100% parameterized SQLite queries across all CRUD operations.
* **Customer Outreach Governance**: Automated test-safe customer notifications (Mock/Test Mode) are governed by deterministic policy suppression (blocking fraud, exhausted, and unapproved states), recipient PII masking (`_mask_recipient`), and anti-spam deduplication (enforcing at most one recovery dispatch per case).

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
│   └── src/
│       ├── components/        # Header, Sidebar, StatsOverview, CasesTable,
│       │                      # CaseDetailModal, AuditLogView, EvaluationView, Toast
│       ├── api.js             # REST API Client with optional X-API-Key forwarding
│       ├── App.jsx            # Main Dashboard Application with 5s background polling
│       └── index.css          # Modern light fintech design system
├── scripts/
│   ├── seed_demo_data.py      # Demo dataset seeding script
│   ├── inspect_real_cases.py  # Diagnostic script for real Razorpay test cases
│   ├── run_batch_evaluation.py# Batch evaluation runner
│   └── run_contextual_evaluation.py # Contextual evaluation runner
├── data/                      # SQLite storage (recovery.db & evaluation.db, gitignored)
├── tests/                     # 210 Automated unit and integration tests (100% passing)
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
| `GEMINI_API_KEY` | Google Gemini API key for contextual AI reasoning. |
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

- **Merchant Dashboard (development):** `http://localhost:5173`
- **Merchant Dashboard (built):** `http://127.0.0.1:8000/dashboard`
- **API Documentation:** `http://127.0.0.1:8000/docs`
- **Health Check:** `http://127.0.0.1:8000/health`

---

## 🧪 Running Automated Tests

The complete backend regression test suite contains **210 automated tests** (100% passing):

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
* **Mock Notification Provider**: Test Mode mock delivery simulating email outreach with deduplication.

### Future Architectural Adapters
* **Razorpay Subscription Recovery**: Automated dunning and smart retry scheduling via Razorpay Subscriptions API.
* **Magic Checkout Abandonment**: Detecting abandoned checkout sessions and issuing targeted recovery links.
* **Production Multi-Channel Dispatch**: Real SMTP / WhatsApp / SMS gateway adapters with merchant template management.
