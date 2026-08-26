"""
Batch Recovery Evaluation & Simulation Engine (Milestone B.1)

Provides isolated, deterministic, and reproducible evaluation of the AI Recovery Agent
over synthetic datasets with balanced 9-category distributions, decision-sensitive outcome
modeling, per-case LLM vs. Deterministic comparison, and safety compliance tracking.
"""

import hashlib
import json
import logging
import os
import random
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.failure_classifier import (
    CATEGORY_AUTHENTICATION_REQUIRED,
    CATEGORY_BANK_DECLINED,
    CATEGORY_CARD_EXPIRED,
    CATEGORY_CARD_LIMIT_EXCEEDED,
    CATEGORY_FRAUD_OR_SECURITY,
    CATEGORY_INSUFFICIENT_FUNDS,
    CATEGORY_INVALID_CARD,
    CATEGORY_TEMPORARY_GATEWAY_ERROR,
    CATEGORY_UNKNOWN,
    classify_payment_failure,
)
from app.recovery_decision import (
    ALLOWED_ACTIONS,
    HIGH_VALUE_THRESHOLD,
    decide_recovery_action,
)
from app.ai_recovery_agent import (
    GeminiProvider,
    OpenAIProvider,
    ai_decide_recovery_action,
)
from app.config import settings

logger = logging.getLogger(__name__)

ALL_FAILURE_CATEGORIES = [
    CATEGORY_INSUFFICIENT_FUNDS,
    CATEGORY_CARD_LIMIT_EXCEEDED,
    CATEGORY_CARD_EXPIRED,
    CATEGORY_AUTHENTICATION_REQUIRED,
    CATEGORY_INVALID_CARD,
    CATEGORY_BANK_DECLINED,
    CATEGORY_TEMPORARY_GATEWAY_ERROR,
    CATEGORY_FRAUD_OR_SECURITY,
    CATEGORY_UNKNOWN,
]

# Explicit templates guaranteed to classify strictly into each specific normalized category
CATEGORY_TEMPLATES = {
    CATEGORY_INSUFFICIENT_FUNDS: [
        ("BAD_REQUEST_ERROR", "Payment failed: Insufficient funds in linked bank account."),
        ("INSUFFICIENT_FUNDS", "Transaction declined: Insufficient balance to cover charge."),
        ("BAD_REQUEST_ERROR", "Low balance error: Customer account has insufficient funds."),
    ],
    CATEGORY_CARD_LIMIT_EXCEEDED: [
        ("BAD_REQUEST_ERROR", "The transaction amount exceeds the daily card spending limit."),
        ("CARD_LIMIT_EXCEEDED", "Card limit exceeded for current billing cycle."),
        ("BAD_REQUEST_ERROR", "Declined: Credit card purchase limit reached."),
    ],
    CATEGORY_CARD_EXPIRED: [
        ("BAD_REQUEST_ERROR", "The card expiry date has passed. Please update card details."),
        ("CARD_EXPIRED", "Transaction declined: Card validity expired."),
        ("BAD_REQUEST_ERROR", "Card expired in previous month. Expiration date invalid."),
    ],
    CATEGORY_AUTHENTICATION_REQUIRED: [
        ("BAD_REQUEST_ERROR", "3DS verification was incomplete or timed out during authentication."),
        ("AUTHENTICATION_FAILED", "OTP verification failed during two-factor authentication."),
        ("BAD_REQUEST_ERROR", "Customer dropped out of 3D Secure challenge session."),
    ],
    CATEGORY_INVALID_CARD: [
        ("BAD_REQUEST_ERROR", "The card number or CVV entered is invalid. Invalid card details."),
        ("INVALID_CARD_DETAILS", "Invalid card credentials supplied at checkout."),
        ("BAD_REQUEST_ERROR", "Card verification value (CVV) mismatch. Invalid card number."),
    ],
    CATEGORY_BANK_DECLINED: [
        ("BAD_REQUEST_ERROR", "Your payment didn't go through as it was declined by the bank."),
        ("BANK_DECLINED", "Issuing bank declined transaction due to policy restrictions."),
        ("BAD_REQUEST_ERROR", "Transaction not permitted by issuing bank policy."),
    ],
    CATEGORY_TEMPORARY_GATEWAY_ERROR: [
        ("GATEWAY_ERROR", "Gateway timeout during bank communication."),
        ("GATEWAY_TIMEOUT", "Temporary network failure between payment aggregator and card network."),
        ("BANK_UNAVAILABLE", "Banking system connection reset. Service unavailable temporarily."),
    ],
    CATEGORY_FRAUD_OR_SECURITY: [
        ("CARD_BLOCKED", "Transaction blocked: Stolen card reported or fraud alert triggered."),
        ("FRAUD_RISK", "Security violation lock: Suspected fraudulent transaction pattern."),
        ("BLACKLISTED_INSTRUMENT", "Instrument blacklisted due to fraud risk compliance restriction."),
    ],
    CATEGORY_UNKNOWN: [
        ("UNKNOWN_ERROR", "An unknown payment error occurred. Unclassified decline code."),
        ("UNRECOGNIZED_CODE", "Unhandled internal error occurred during payment processing."),
        ("UNDEFINED_ERROR", "Payment processing terminated with unknown status."),
    ],
}


def generate_synthetic_evaluation_dataset(num_cases: int = 100, seed: int = 42) -> List[Dict[str, Any]]:
    """
    Generates a balanced, deterministic synthetic payment failure dataset.
    Guarantees at least 10 cases for every one of the 9 failure categories.
    Varied transaction amounts, recurring flags, attempt counts, and high-value tiers.
    """
    rng = random.Random(seed)
    dataset: List[Dict[str, Any]] = []

    # Calculate baseline count per category (at least 10 for each of the 9 categories)
    base_per_cat = num_cases // len(ALL_FAILURE_CATEGORIES)  # 11 for 100
    remainder = num_cases % len(ALL_FAILURE_CATEGORIES)      # 1 for 100

    category_plan: List[str] = []
    for i, cat in enumerate(ALL_FAILURE_CATEGORIES):
        count = base_per_cat + (1 if i < remainder else 0)
        category_plan.extend([cat] * count)

    # Shuffle categories deterministically using seeded RNG
    rng.shuffle(category_plan)

    # 10% of cases designated as Enterprise/High-Value (>= 50k)
    high_val_indices = set(rng.sample(range(num_cases), max(1, int(num_cases * 0.10))))

    for idx, category in enumerate(category_plan):
        case_num = idx + 1
        is_high_val = idx in high_val_indices

        if is_high_val:
            amount = round(rng.uniform(52000.0, 115000.0), 2)
        else:
            tier = rng.choice([(450.0, 1800.0), (1800.0, 8500.0), (8500.0, 38000.0)])
            amount = round(rng.uniform(tier[0], tier[1]), 2)

        templates = CATEGORY_TEMPLATES[category]
        error_code, error_desc = rng.choice(templates)

        is_recurring = rng.random() < 0.35
        payment_attempts = rng.choice([1, 1, 1, 2, 2, 3])

        case_record = {
            "case_id": f"eval_case_{seed}_{case_num:04d}",
            "order_id": f"eval_order_{seed}_{case_num:04d}",
            "payment_id": f"pay_eval_{seed}_{case_num:04d}",
            "amount": amount,
            "currency": "INR",
            "payment_status": "failed",
            "is_recurring_revenue": is_recurring,
            "payment_attempts_count": payment_attempts,
            "error_code": error_code,
            "error_description": error_desc,
            "synthetic_category": category,
            "customer_id": f"eval_user_{case_num:03d}@example.com",
            "seed": seed,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        dataset.append(case_record)

    return dataset


def init_evaluation_db(db_path: str = "data/evaluation.db") -> None:
    """Initializes the dedicated SQLite evaluation database schema."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluation_runs (
                run_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                seed INTEGER NOT NULL,
                total_cases INTEGER NOT NULL,
                total_revenue_at_risk REAL NOT NULL,
                recovered_revenue REAL NOT NULL,
                recovery_rate_percentage REAL NOT NULL,
                llm_decisions INTEGER NOT NULL,
                fallback_decisions INTEGER NOT NULL,
                human_approvals INTEGER NOT NULL,
                fraud_blocks INTEGER NOT NULL,
                metrics_json TEXT NOT NULL,
                category_breakdown_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluation_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                amount REAL NOT NULL,
                failure_category TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                decision_source TEXT NOT NULL,
                requires_human_approval INTEGER NOT NULL,
                outcome_status TEXT NOT NULL,
                recovered_amount REAL NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES evaluation_runs(run_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluation_comparisons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                deterministic_action TEXT NOT NULL,
                llm_action TEXT NOT NULL,
                deterministic_confidence REAL NOT NULL,
                llm_confidence REAL NOT NULL,
                deterministic_outcome TEXT NOT NULL,
                llm_outcome TEXT NOT NULL,
                action_diff_flag INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


def evaluate_case_decision(
    case: Dict[str, Any],
    mode: str = "deterministic",
    llm_provider: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Evaluates a risk case using the AI / deterministic recovery engine.
    Applies failure classification, prompts, and server-side guardrails.
    """
    classified = classify_payment_failure(
        error_code=case.get("error_code"),
        error_description=case.get("error_description"),
        is_recurring=case.get("is_recurring_revenue", False),
        amount=case.get("amount", 0.0),
    )
    case_context = {
        "payment_id": case["payment_id"],
        "order_id": case["order_id"],
        "amount": case["amount"],
        "currency": case.get("currency", "INR"),
        "payment_status": "failed",
        "is_recurring_revenue": case.get("is_recurring_revenue", False),
        "failure_category": classified["category"],
        "failure_category_label": classified["category_label"],
        "error_code": case.get("error_code"),
        "error_description": case.get("error_description"),
        "payment_attempts_count": case.get("payment_attempts_count", 1),
    }

    if mode == "deterministic":
        decision = decide_recovery_action(case_context)
        decision["decision_source"] = "deterministic_fallback"
    else:
        decision = ai_decide_recovery_action(case_context, llm_provider=llm_provider)

    decision["failure_category"] = classified["category"]
    decision["failure_category_label"] = classified["category_label"]
    return decision


def simulate_recovery_outcome(
    case: Dict[str, Any],
    decision: Dict[str, Any],
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Decision-sensitive outcome simulation model.
    The simulated recovery outcome is strictly determined by the chosen action,
    approval requirement, and category solvability.
    """
    stable_seed = int(
        hashlib.sha256(
            f"{seed}_{case['case_id']}_{decision['action']}".encode("utf-8")
        ).hexdigest()[:16],
        16,
    )
    rng = random.Random(stable_seed)

    amount = float(case["amount"])
    action = decision["action"]
    category = decision["failure_category"]
    requires_approval = decision.get("requires_human_approval", False)
    confidence = float(decision.get("confidence", 0.85))

    # 1. Action: NO_ACTION or FRAUD_OR_SECURITY (Zero recovery, halted immediately)
    if category == CATEGORY_FRAUD_OR_SECURITY:
        return {
            "outcome_status": "blocked_fraud_security",
            "recovered_amount": 0.0,
            "time_to_recovery_hours": 0.0,
            "recovery_channel": "none",
        }
    if action == "NO_ACTION":
        return {
            "outcome_status": "blocked_retry_exhausted",
            "recovered_amount": 0.0,
            "time_to_recovery_hours": 0.0,
            "recovery_channel": "none",
        }

    # 2. High-Value Human Approval (>= 50k or explicit approval guardrail)
    if requires_approval or amount >= HIGH_VALUE_THRESHOLD:
        # Merchant review approval rate: 80%
        merchant_approved = rng.random() < 0.80
        if merchant_approved:
            if action == "SEND_PAYMENT_LINK":
                # Payer completion on high-value approved link: 85%
                if rng.random() < 0.85:
                    return {
                        "outcome_status": "recovered_human_approved",
                        "recovered_amount": amount,
                        "time_to_recovery_hours": round(rng.uniform(4.0, 24.0), 1),
                        "recovery_channel": "payment_link_approved",
                    }
                else:
                    return {
                        "outcome_status": "link_unpaid_after_approval",
                        "recovered_amount": 0.0,
                        "time_to_recovery_hours": 0.0,
                        "recovery_channel": "payment_link_approved",
                    }
            elif action == "INVESTIGATE":
                # Manual ops investigation converts 40%
                if rng.random() < 0.40:
                    return {
                        "outcome_status": "recovered_manual_investigation",
                        "recovered_amount": amount,
                        "time_to_recovery_hours": round(rng.uniform(24.0, 48.0), 1),
                        "recovery_channel": "manual_review",
                    }
                else:
                    return {
                        "outcome_status": "investigation_unresolved",
                        "recovered_amount": 0.0,
                        "time_to_recovery_hours": 0.0,
                        "recovery_channel": "manual_review",
                    }
        else:
            return {
                "outcome_status": "rejected_by_merchant",
                "recovered_amount": 0.0,
                "time_to_recovery_hours": 0.0,
                "recovery_channel": "rejected",
            }

    # 3. Action: SEND_PAYMENT_LINK (< 50k automated execution)
    if action == "SEND_PAYMENT_LINK":
        base_conversion = {
            CATEGORY_CARD_LIMIT_EXCEEDED: 0.90,
            CATEGORY_CARD_EXPIRED: 0.92,
            CATEGORY_AUTHENTICATION_REQUIRED: 0.88,
            CATEGORY_INSUFFICIENT_FUNDS: 0.84,
            CATEGORY_INVALID_CARD: 0.80,
            CATEGORY_BANK_DECLINED: 0.75,
            CATEGORY_UNKNOWN: 0.45,
            CATEGORY_TEMPORARY_GATEWAY_ERROR: 0.60,
        }.get(category, 0.70)

        # Objective scenario conversion (independent of model-reported confidence to eliminate circularity)
        if rng.random() < base_conversion:
            return {
                "outcome_status": "recovered_payment_link",
                "recovered_amount": amount,
                "time_to_recovery_hours": round(rng.uniform(0.2, 6.0), 1),
                "recovery_channel": "payment_link_auto",
            }
        else:
            return {
                "outcome_status": "link_unpaid",
                "recovered_amount": 0.0,
                "time_to_recovery_hours": 0.0,
                "recovery_channel": "payment_link_auto",
            }

    # 4. Action: WAIT (Automated delayed gateway retry)
    if action == "WAIT":
        # 45% recovery on delayed bank retry without disturbing customer
        if rng.random() < 0.45:
            return {
                "outcome_status": "recovered_gateway_retry",
                "recovered_amount": amount,
                "time_to_recovery_hours": round(rng.uniform(0.5, 2.0), 1),
                "recovery_channel": "gateway_retry",
            }
        else:
            return {
                "outcome_status": "pending_gateway_retry",
                "recovered_amount": 0.0,
                "time_to_recovery_hours": 0.0,
                "recovery_channel": "gateway_retry",
            }

    # 5. Action: INVESTIGATE (< 50k)
    if action == "INVESTIGATE":
        # Manual ops review converts 25% of low-value unknown declines
        if rng.random() < 0.25:
            return {
                "outcome_status": "recovered_manual_review",
                "recovered_amount": amount,
                "time_to_recovery_hours": round(rng.uniform(12.0, 36.0), 1),
                "recovery_channel": "manual_review",
            }
        else:
            return {
                "outcome_status": "investigation_pending",
                "recovered_amount": 0.0,
                "time_to_recovery_hours": 0.0,
                "recovery_channel": "manual_review",
            }

    # Fallback unrecovered
    return {
        "outcome_status": "unrecovered_other",
        "recovered_amount": 0.0,
        "time_to_recovery_hours": 0.0,
        "recovery_channel": "none",
    }


def run_batch_evaluation(
    num_cases: int = 100,
    seed: int = 42,
    mode: str = "all",
    db_path: str = "data/evaluation.db",
    llm_provider: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Runs a batch recovery evaluation over the synthetic dataset.
    Supports deterministic mode, LLM mode, and unified dual comparison.
    """
    init_evaluation_db(db_path)
    dataset = generate_synthetic_evaluation_dataset(num_cases=num_cases, seed=seed)

    # Resolve LLM provider if needed
    resolved_provider = llm_provider
    if mode in ["llm", "all"] and resolved_provider is None:
        gemini_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
        if gemini_key:
            resolved_provider = GeminiProvider(api_key=gemini_key)
        elif settings.OPENAI_API_KEY:
            resolved_provider = OpenAIProvider(api_key=settings.OPENAI_API_KEY)

    results_by_mode: Dict[str, Any] = {}
    modes_to_run = ["deterministic", "llm"] if mode == "all" else [mode]

    for current_mode in modes_to_run:
        run_id = f"eval_run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{current_mode}_{seed}"
        total_revenue_at_risk = sum(c["amount"] for c in dataset)
        total_recovered_revenue = 0.0
        auto_recovered_revenue = 0.0
        human_approved_revenue = 0.0
        pending_escalated_revenue = 0.0
        blocked_fraud_revenue = 0.0
        blocked_retry_exhausted_revenue = 0.0

        llm_decisions = 0
        fallback_decisions = 0
        human_approvals = 0
        fraud_blocks = 0
        retry_exhausted_blocks = 0
        recovery_times: List[float] = []

        cat_stats: Dict[str, Dict[str, Any]] = {
            cat: {
                "category": cat,
                "cases_count": 0,
                "at_risk_revenue": 0.0,
                "recovered_revenue": 0.0,
                "recovery_rate_percentage": 0.0,
                "confidence_sum": 0.0,
                "avg_confidence": 0.0,
                "actions_count": {},
            }
            for cat in ALL_FAILURE_CATEGORIES
        }

        evaluated_records: List[Dict[str, Any]] = []

        for case in dataset:
            decision = evaluate_case_decision(case, mode=current_mode, llm_provider=resolved_provider)
            outcome = simulate_recovery_outcome(case, decision, seed=seed)

            amount = float(case["amount"])
            recovered_amt = float(outcome["recovered_amount"])
            total_recovered_revenue += recovered_amt

            if outcome["outcome_status"].startswith("recovered_human_approved") or outcome["outcome_status"].startswith("recovered_manual"):
                human_approved_revenue += recovered_amt
            elif outcome["outcome_status"].startswith("recovered"):
                auto_recovered_revenue += recovered_amt
            elif outcome["outcome_status"] == "blocked_fraud_security":
                blocked_fraud_revenue += amount
            elif outcome["outcome_status"] == "blocked_retry_exhausted":
                blocked_retry_exhausted_revenue += amount
            else:
                pending_escalated_revenue += amount

            if outcome["time_to_recovery_hours"] > 0:
                recovery_times.append(outcome["time_to_recovery_hours"])

            if decision.get("decision_source") == "llm":
                llm_decisions += 1
            else:
                fallback_decisions += 1

            if decision.get("requires_human_approval"):
                human_approvals += 1

            if decision.get("failure_category") == CATEGORY_FRAUD_OR_SECURITY:
                fraud_blocks += 1
            elif decision.get("action") == "NO_ACTION":
                retry_exhausted_blocks += 1

            cat = decision["failure_category"]
            if cat in cat_stats:
                cat_stats[cat]["cases_count"] += 1
                cat_stats[cat]["at_risk_revenue"] += amount
                cat_stats[cat]["recovered_revenue"] += recovered_amt
                cat_stats[cat]["confidence_sum"] += decision.get("confidence", 0.0)
                act = decision.get("action", "UNKNOWN")
                cat_stats[cat]["actions_count"][act] = cat_stats[cat]["actions_count"].get(act, 0) + 1

            record = {
                "run_id": run_id,
                "case_id": case["case_id"],
                "amount": amount,
                "failure_category": cat,
                "action": decision["action"],
                "confidence": decision.get("confidence", 0.0),
                "decision_source": decision.get("decision_source", "deterministic_fallback"),
                "requires_human_approval": 1 if decision.get("requires_human_approval") else 0,
                "outcome_status": outcome["outcome_status"],
                "recovered_amount": recovered_amt,
                "reason": decision.get("reason", ""),
                "created_at": case["created_at"],
            }
            evaluated_records.append(record)

        category_breakdown: List[Dict[str, Any]] = []
        for cat, stats in cat_stats.items():
            cnt = stats["cases_count"]
            at_risk = stats["at_risk_revenue"]
            rec = stats["recovered_revenue"]
            rate = round((rec / at_risk * 100.0), 1) if at_risk > 0 else 0.0
            avg_conf = round((stats["confidence_sum"] / cnt), 2) if cnt > 0 else 0.0

            category_breakdown.append({
                "category": cat,
                "cases_count": cnt,
                "at_risk_revenue": round(at_risk, 2),
                "recovered_revenue": round(rec, 2),
                "recovery_rate_percentage": rate,
                "avg_confidence": avg_conf,
                "actions_count": stats["actions_count"],
            })

        recovery_rate_pct = round((total_recovered_revenue / total_revenue_at_risk * 100.0), 1) if total_revenue_at_risk > 0 else 0.0
        unrecovered_revenue = round(total_revenue_at_risk - total_recovered_revenue, 2)
        avg_failed_amount = round(total_revenue_at_risk / num_cases, 2) if num_cases > 0 else 0.0
        recovered_count = sum(1 for r in evaluated_records if r["recovered_amount"] > 0)
        avg_recovered_amount = round(total_recovered_revenue / recovered_count, 2) if recovered_count > 0 else 0.0
        avg_time_to_recovery = round(sum(recovery_times) / len(recovery_times), 1) if recovery_times else 0.0

        metrics = {
            "run_id": run_id,
            "mode": current_mode,
            "seed": seed,
            "total_cases": num_cases,
            "total_revenue_at_risk": round(total_revenue_at_risk, 2),
            "recovered_revenue": round(total_recovered_revenue, 2),
            "unrecovered_revenue": unrecovered_revenue,
            "recovery_rate_percentage": recovery_rate_pct,
            "auto_recovered_revenue": round(auto_recovered_revenue, 2),
            "human_approved_revenue": round(human_approved_revenue, 2),
            "pending_or_escalated_revenue": round(pending_escalated_revenue, 2),
            "blocked_fraud_revenue": round(blocked_fraud_revenue, 2),
            "blocked_retry_exhausted_revenue": round(blocked_retry_exhausted_revenue, 2),
            "total_halted_revenue": round(blocked_fraud_revenue + blocked_retry_exhausted_revenue, 2),
            "average_failed_amount": avg_failed_amount,
            "average_recovered_amount": avg_recovered_amount,
            "average_time_to_recovery_hours": avg_time_to_recovery,
            "llm_decisions": llm_decisions,
            "fallback_decisions": fallback_decisions,
            "llm_decision_rate_percentage": round(llm_decisions / num_cases * 100.0, 1) if num_cases > 0 else 0.0,
            "fallback_rate_percentage": round(fallback_decisions / num_cases * 100.0, 1) if num_cases > 0 else 0.0,
            "human_approvals": human_approvals,
            "human_approval_rate_percentage": round(human_approvals / num_cases * 100.0, 1) if num_cases > 0 else 0.0,
            "fraud_blocks": fraud_blocks,
            "fraud_block_rate_percentage": round(fraud_blocks / num_cases * 100.0, 1) if num_cases > 0 else 0.0,
            "retry_exhausted_blocks": retry_exhausted_blocks,
            "retry_exhausted_rate_percentage": round(retry_exhausted_blocks / num_cases * 100.0, 1) if num_cases > 0 else 0.0,
            "total_halted_blocks": fraud_blocks + retry_exhausted_blocks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_simulated_evaluation": True,
        }

        # Save to database
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                INSERT INTO evaluation_runs (
                    run_id, mode, seed, total_cases, total_revenue_at_risk, recovered_revenue,
                    recovery_rate_percentage, llm_decisions, fallback_decisions, human_approvals,
                    fraud_blocks, metrics_json, category_breakdown_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                run_id,
                current_mode,
                seed,
                num_cases,
                round(total_revenue_at_risk, 2),
                round(total_recovered_revenue, 2),
                recovery_rate_pct,
                llm_decisions,
                fallback_decisions,
                human_approvals,
                fraud_blocks,
                json.dumps(metrics),
                json.dumps(category_breakdown),
            ))

            conn.executemany("""
                INSERT INTO evaluation_cases (
                    run_id, case_id, amount, failure_category, action, confidence,
                    decision_source, requires_human_approval, outcome_status, recovered_amount, reason, created_at
                ) VALUES (:run_id, :case_id, :amount, :failure_category, :action, :confidence,
                          :decision_source, :requires_human_approval, :outcome_status, :recovered_amount, :reason, :created_at)
            """, evaluated_records)
            conn.commit()

        results_by_mode[current_mode] = {
            "metrics": metrics,
            "category_breakdown": category_breakdown,
            "cases_sample": evaluated_records[:25],
        }

    # If both modes ran, generate per-case comparison matrix and safety audit
    comparison_summary = None
    comparison_table: List[Dict[str, Any]] = []

    if "deterministic" in results_by_mode and "llm" in results_by_mode:
        det_cases = {c["case_id"]: c for c in results_by_mode["deterministic"]["cases_sample"]}
        llm_cases = {c["case_id"]: c for c in results_by_mode["llm"]["cases_sample"]}

        action_diff_count = 0
        fraud_violations = 0
        high_value_bypasses = 0
        unsupported_actions = 0

        for case in dataset:
            c_id = case["case_id"]
            d_dec = evaluate_case_decision(case, mode="deterministic")
            l_dec = evaluate_case_decision(case, mode="llm", llm_provider=resolved_provider)

            d_out = simulate_recovery_outcome(case, d_dec, seed=seed)
            l_out = simulate_recovery_outcome(case, l_dec, seed=seed)

            d_act = d_dec["action"]
            l_act = l_dec["action"]
            diff = (d_act != l_act)
            if diff:
                action_diff_count += 1

            # Safety checks
            cat = case["synthetic_category"]
            amt = float(case["amount"])

            if cat == CATEGORY_FRAUD_OR_SECURITY and (d_act != "NO_ACTION" or l_act != "NO_ACTION"):
                fraud_violations += 1

            if amt >= HIGH_VALUE_THRESHOLD and (not d_dec["requires_human_approval"] or not l_dec["requires_human_approval"]):
                high_value_bypasses += 1

            if d_act not in ALLOWED_ACTIONS or l_act not in ALLOWED_ACTIONS:
                unsupported_actions += 1

            row = {
                "case_id": c_id,
                "category": cat,
                "amount": amt,
                "deterministic_action": d_act,
                "llm_action": l_act,
                "deterministic_confidence": round(d_dec.get("confidence", 0.0), 2),
                "llm_confidence": round(l_dec.get("confidence", 0.0), 2),
                "final_llm_action_after_guardrails": l_act,
                "deterministic_outcome": d_out["outcome_status"],
                "llm_outcome": l_out["outcome_status"],
                "action_diff_flag": 1 if diff else 0,
            }
            comparison_table.append(row)

        comparison_summary = {
            "total_cases_evaluated": num_cases,
            "action_difference_count": action_diff_count,
            "action_difference_percentage": round(action_diff_count / num_cases * 100.0, 1) if num_cases > 0 else 0.0,
            "fraud_auto_execution_violations": fraud_violations,
            "high_value_approval_bypasses": high_value_bypasses,
            "unsupported_actions_count": unsupported_actions,
            "deterministic_recovery_rate": results_by_mode["deterministic"]["metrics"]["recovery_rate_percentage"],
            "llm_recovery_rate": results_by_mode["llm"]["metrics"]["recovery_rate_percentage"],
            "deterministic_recovered_revenue": results_by_mode["deterministic"]["metrics"]["recovered_revenue"],
            "llm_recovered_revenue": results_by_mode["llm"]["metrics"]["recovered_revenue"],
        }

        # Save comparison table
        with sqlite3.connect(db_path) as conn:
            conn.executemany("""
                INSERT INTO evaluation_comparisons (
                    run_id, case_id, category, amount, deterministic_action, llm_action,
                    deterministic_confidence, llm_confidence, deterministic_outcome, llm_outcome,
                    action_diff_flag, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, [(
                f"cmp_{seed}",
                r["case_id"],
                r["category"],
                r["amount"],
                r["deterministic_action"],
                r["llm_action"],
                r["deterministic_confidence"],
                r["llm_confidence"],
                r["deterministic_outcome"],
                r["llm_outcome"],
                r["action_diff_flag"],
            ) for r in comparison_table])
            conn.commit()

    if mode == "all":
        primary = results_by_mode["deterministic"]
        primary["comparison"] = comparison_summary
        primary["comparison_table"] = comparison_table[:25]
        primary["all_modes"] = results_by_mode
        return primary
    else:
        return results_by_mode[mode]


def get_latest_evaluation_report(db_path: str = "data/evaluation.db") -> Optional[Dict[str, Any]]:
    """Retrieves the most recent evaluation report from the evaluation database."""
    if not os.path.exists(db_path):
        return None

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT * FROM evaluation_runs ORDER BY rowid DESC LIMIT 1
        """).fetchone()

        if not row:
            return None

        metrics = json.loads(row["metrics_json"])
        category_breakdown = json.loads(row["category_breakdown_json"])

        cases_rows = conn.execute("""
            SELECT * FROM evaluation_cases WHERE run_id = ? ORDER BY id ASC LIMIT 25
        """, (row["run_id"],)).fetchall()

        cases_sample = [dict(c) for c in cases_rows]

        # Fetch comparisons if available
        cmp_rows = conn.execute("""
            SELECT * FROM evaluation_comparisons ORDER BY id DESC LIMIT 25
        """).fetchall()
        comparison_sample = [dict(c) for c in cmp_rows]

        return {
            "metrics": metrics,
            "category_breakdown": category_breakdown,
            "cases_sample": cases_sample,
            "comparison_table": comparison_sample,
        }
