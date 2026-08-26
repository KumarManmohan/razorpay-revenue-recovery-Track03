"""
Contextual Recovery Intelligence Evaluator (Milestone 15A)

Evaluates the AI agent's ability to reason over multi-dimensional payment and customer history context
(e.g., attempt history, prior link outcomes, customer tenure, recent successes vs. repeated declines)
and compares it with both the deterministic policy and a deterministic heuristic baseline.
"""

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

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
    build_sanitized_recovery_context,
)
from app.config import settings

logger = logging.getLogger(__name__)


def generate_contextual_benchmark_dataset() -> List[Dict[str, Any]]:
    """
    Generates a curated 16-case complex contextual benchmark dataset with explicit human-authored ground truth.
    Each scenario combines failure category, customer tenure, retry history, link response history, and financial risk.
    """
    cases = [
        # --- Scenario A: Loyal Customer / First Failure ---
        {
            "case_id": "ctx_01_loyal_subscriber_card_limit",
            "scenario_name": "Loyal Subscriber 1st Card Limit Exceeded",
            "amount": 7500.0,
            "currency": "INR",
            "is_recurring_revenue": True,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card limit exceeded for transaction.",
            "payment_attempts_count": 1,
            "previous_failed_attempts_count": 0,
            "previous_successful_payments_count": 18,
            "time_since_last_successful_payment_days": 30.0,
            "time_since_last_failed_attempt_hours": 0.5,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 24,
            "customer_id": "aravind.loyal@example.com",
            "expected_actions": ["SEND_PAYMENT_LINK"],
            "expected_priority": ["HIGH"],
            "expected_escalation": False,
            "required_context_factors": [
                "customer_tenure",
                "previous_successful_payments",
                "failed_attempt_count",
            ],
            "context_hypothesis": "High-tenure loyal subscriber experiencing transient limit. Priority is HIGH to protect MRR, standard link without escalation.",
        },
        # --- Scenario B: Repeated Failure / Multiple Ignored Links ---
        {
            "case_id": "ctx_02_chronic_limit_ignored_links",
            "scenario_name": "Chronic Limit Decline with Multiple Ignored Links",
            "amount": 7500.0,
            "currency": "INR",
            "is_recurring_revenue": True,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card spending limit reached.",
            "payment_attempts_count": 4,
            "previous_failed_attempts_count": 3,
            "previous_successful_payments_count": 1,
            "time_since_last_successful_payment_days": 60.0,
            "time_since_last_failed_attempt_hours": 48.0,
            "has_active_recovery_link": True,
            "prior_recovery_links_count": 2,
            "recovery_link_previously_ignored": True,
            "customer_tenure_months": 2,
            "customer_id": "rohit.unresp@example.com",
            "expected_actions": ["SEND_PAYMENT_LINK", "INVESTIGATE"],
            "expected_priority": ["MEDIUM", "HIGH"],
            "expected_escalation": True,
            "required_context_factors": [
                "failed_attempt_count",
                "ignored_recovery_link",
                "customer_tenure",
            ],
            "context_hypothesis": "Customer previously ignored 2 recovery links over 48 hours. Standard links are ineffective; escalation recommended.",
        },
        # --- Scenario C: Transient Gateway Error with Recent Success ---
        {
            "case_id": "ctx_03_transient_gateway_recent_success",
            "scenario_name": "Transient Gateway Glitch with Recent Success",
            "amount": 1200.0,
            "currency": "INR",
            "is_recurring_revenue": False,
            "error_code": "GATEWAY_ERROR",
            "error_description": "Gateway timeout during bank communication.",
            "payment_attempts_count": 1,
            "previous_failed_attempts_count": 0,
            "previous_successful_payments_count": 6,
            "time_since_last_successful_payment_days": 0.01,
            "time_since_last_failed_attempt_hours": 0.1,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 8,
            "customer_id": "priya.shopper@example.com",
            "expected_actions": ["WAIT"],
            "expected_priority": ["LOW"],
            "expected_escalation": False,
            "required_context_factors": [
                "transient_gateway_error",
                "previous_successful_payments",
            ],
            "context_hypothesis": "Recent successful transaction minutes prior indicates transient aggregator spike. WAIT is mandatory to avoid double charging.",
        },
        # --- Scenario D: Persistent Gateway Outage Over Multiple Days ---
        {
            "case_id": "ctx_04_persistent_gateway_outage",
            "scenario_name": "Persistent Gateway Outage Spanning 72 Hours",
            "amount": 3500.0,
            "currency": "INR",
            "is_recurring_revenue": True,
            "error_code": "GATEWAY_ERROR",
            "error_description": "Gateway connection reset timeout.",
            "payment_attempts_count": 5,
            "previous_failed_attempts_count": 4,
            "previous_successful_payments_count": 2,
            "time_since_last_successful_payment_days": 60.0,
            "time_since_last_failed_attempt_hours": 72.0,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 3,
            "customer_id": "deepak.timeout@example.com",
            "expected_actions": ["WAIT", "INVESTIGATE", "SEND_PAYMENT_LINK"],
            "expected_priority": ["MEDIUM", "HIGH"],
            "expected_escalation": True,
            "required_context_factors": [
                "failed_attempt_count",
                "transient_gateway_error",
            ],
            "context_hypothesis": "5th gateway timeout spanning 72 hours. Retrying blindly is failing; escalation to ops/engineering is recommended.",
        },
        # --- Scenario E: High-Value Transaction with Loyal Customer ---
        {
            "case_id": "ctx_05_high_value_loyal_vip",
            "scenario_name": "High-Value Transaction (₹85,000) Loyal VIP",
            "amount": 85000.0,
            "currency": "INR",
            "is_recurring_revenue": False,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Transaction declined by issuing bank.",
            "payment_attempts_count": 1,
            "previous_failed_attempts_count": 0,
            "previous_successful_payments_count": 30,
            "time_since_last_successful_payment_days": 15.0,
            "time_since_last_failed_attempt_hours": 0.5,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 36,
            "customer_id": "vip.corp@example.com",
            "expected_actions": ["SEND_PAYMENT_LINK"],
            "expected_priority": ["HIGH"],
            "expected_escalation": False,
            "required_context_factors": [
                "high_value_transaction",
                "customer_tenure",
                "previous_successful_payments",
            ],
            "context_hypothesis": "High-value transaction >= ₹50,000 mandates human approval, but priority is HIGH due to 3-year VIP customer relationship.",
        },
        # --- Scenario F: High-Value First-Time Buyer ---
        {
            "case_id": "ctx_06_high_value_first_time_buyer",
            "scenario_name": "High-Value Transaction (₹75,000) First-Time Buyer",
            "amount": 75000.0,
            "currency": "INR",
            "is_recurring_revenue": False,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card limit exceeded for single purchase.",
            "payment_attempts_count": 1,
            "previous_failed_attempts_count": 0,
            "previous_successful_payments_count": 0,
            "time_since_last_successful_payment_days": None,
            "time_since_last_failed_attempt_hours": 1.0,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 0,
            "customer_id": "new.buyer75k@example.com",
            "expected_actions": ["SEND_PAYMENT_LINK"],
            "expected_priority": ["HIGH"],
            "expected_escalation": True,
            "required_context_factors": [
                "high_value_transaction",
                "customer_tenure",
            ],
            "context_hypothesis": "First-time buyer spending ₹75,000. High financial risk requiring human approval and merchant review.",
        },
        # --- Scenario G: Fraud / Stolen Instrument ---
        {
            "case_id": "ctx_07_fraud_stolen_instrument",
            "scenario_name": "Fraud Risk Stolen Card Blocked",
            "amount": 5000.0,
            "currency": "INR",
            "is_recurring_revenue": False,
            "error_code": "CARD_BLOCKED",
            "error_description": "Card has been reported stolen or compromised by cardholder.",
            "payment_attempts_count": 1,
            "previous_failed_attempts_count": 0,
            "previous_successful_payments_count": 0,
            "time_since_last_successful_payment_days": None,
            "time_since_last_failed_attempt_hours": 0.2,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 0,
            "customer_id": "suspicious.user@example.com",
            "expected_actions": ["NO_ACTION"],
            "expected_priority": ["HIGH"],
            "expected_escalation": True,
            "required_context_factors": [
                "fraud_indicator",
            ],
            "context_hypothesis": "Security stop: stolen instrument detected. Hard NO_ACTION policy enforced with zero automated link issuance.",
        },
        # --- Scenario H: Unknown Failure on Trusted High-Tenure Customer ---
        {
            "case_id": "ctx_08_unknown_error_high_tenure",
            "scenario_name": "Unknown Failure Code on 18-Month Customer",
            "amount": 12500.0,
            "currency": "INR",
            "is_recurring_revenue": True,
            "error_code": "UNEXPECTED_CORE_ERR_99",
            "error_description": "Internal issuer processing anomaly (code 99).",
            "payment_attempts_count": 2,
            "previous_failed_attempts_count": 1,
            "previous_successful_payments_count": 15,
            "time_since_last_successful_payment_days": 30.0,
            "time_since_last_failed_attempt_hours": 2.0,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 18,
            "customer_id": "rajesh.saas@example.com",
            "expected_actions": ["INVESTIGATE"],
            "expected_priority": ["HIGH", "MEDIUM"],
            "expected_escalation": True,
            "required_context_factors": [
                "customer_tenure",
                "previous_successful_payments",
            ],
            "context_hypothesis": "Unrecognized error on trusted subscriber. Requires investigation to preserve account relationship.",
        },
        # --- Scenario I: Expired Card on Active Monthly Subscription ---
        {
            "case_id": "ctx_09_expired_card_active_sub",
            "scenario_name": "Expired Card on Active Monthly Subscription",
            "amount": 4999.0,
            "currency": "INR",
            "is_recurring_revenue": True,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card expiry date has passed.",
            "payment_attempts_count": 1,
            "previous_failed_attempts_count": 0,
            "previous_successful_payments_count": 12,
            "time_since_last_successful_payment_days": 30.0,
            "time_since_last_failed_attempt_hours": 1.0,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 12,
            "customer_id": "vikram.sub@example.com",
            "expected_actions": ["SEND_PAYMENT_LINK"],
            "expected_priority": ["HIGH", "MEDIUM"],
            "expected_escalation": False,
            "required_context_factors": [
                "customer_tenure",
                "previous_successful_payments",
                "failed_attempt_count",
            ],
            "context_hypothesis": "Card expiration on 1-year subscriber. Standard payment link allows customer to enter new card details seamlessly.",
        },
        # --- Scenario J: 3DS Authentication Timeout on Mobile Checkout ---
        {
            "case_id": "ctx_10_auth_timeout_mobile_abandoned",
            "scenario_name": "3DS OTP Timeout on Mobile Checkout",
            "amount": 950.0,
            "currency": "INR",
            "is_recurring_revenue": False,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "3D Secure authentication timed out or was abandoned by user.",
            "payment_attempts_count": 1,
            "previous_failed_attempts_count": 0,
            "previous_successful_payments_count": 2,
            "time_since_last_successful_payment_days": 45.0,
            "time_since_last_failed_attempt_hours": 0.3,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 3,
            "customer_id": "sneha.cart@example.com",
            "expected_actions": ["SEND_PAYMENT_LINK"],
            "expected_priority": ["LOW", "MEDIUM"],
            "expected_escalation": False,
            "required_context_factors": [
                "failed_attempt_count",
            ],
            "context_hypothesis": "User abandoned OTP prompt. Reissuing link allows instant retry while intent is high.",
        },
        # --- Scenario K: Insufficient Funds on Month-End Salary Cycle ---
        {
            "case_id": "ctx_11_insufficient_funds_salary_cycle",
            "scenario_name": "Month-End Insufficient Funds on SaaS Plan",
            "amount": 2500.0,
            "currency": "INR",
            "is_recurring_revenue": True,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Insufficient funds in customer account.",
            "payment_attempts_count": 1,
            "previous_failed_attempts_count": 0,
            "previous_successful_payments_count": 5,
            "time_since_last_successful_payment_days": 29.0,
            "time_since_last_failed_attempt_hours": 2.0,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 6,
            "customer_id": "kavita.saas@example.com",
            "expected_actions": ["SEND_PAYMENT_LINK"],
            "expected_priority": ["MEDIUM"],
            "expected_escalation": False,
            "required_context_factors": [
                "previous_successful_payments",
                "failed_attempt_count",
            ],
            "context_hypothesis": "Transient balance shortfall on recurring subscriber. Payment link allows UPI or alternate card payment.",
        },
        # --- Scenario L: Chronic Insufficient Funds with Repeat Declines ---
        {
            "case_id": "ctx_12_repeat_insufficient_funds_churn",
            "scenario_name": "Chronic Insufficient Funds 4th Decline",
            "amount": 2500.0,
            "currency": "INR",
            "is_recurring_revenue": True,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Insufficient funds in customer account.",
            "payment_attempts_count": 4,
            "previous_failed_attempts_count": 3,
            "previous_successful_payments_count": 1,
            "time_since_last_successful_payment_days": 75.0,
            "time_since_last_failed_attempt_hours": 72.0,
            "has_active_recovery_link": True,
            "prior_recovery_links_count": 2,
            "recovery_link_previously_ignored": True,
            "customer_tenure_months": 2,
            "customer_id": "manoj.churn@example.com",
            "expected_actions": ["SEND_PAYMENT_LINK", "INVESTIGATE"],
            "expected_priority": ["LOW", "MEDIUM"],
            "expected_escalation": True,
            "required_context_factors": [
                "failed_attempt_count",
                "ignored_recovery_link",
            ],
            "context_hypothesis": "Customer repeatedly declines with ignored recovery links. High churn likelihood; escalation recommended.",
        },
        # --- Scenario M: Enterprise B2B Invoice Reconciliation ---
        {
            "case_id": "ctx_13_b2b_custom_invoice_reconciliation",
            "scenario_name": "Enterprise B2B Subscription Decline",
            "amount": 45000.0,
            "currency": "INR",
            "is_recurring_revenue": True,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Corporate card policy limit exceeded.",
            "payment_attempts_count": 2,
            "previous_failed_attempts_count": 1,
            "previous_successful_payments_count": 8,
            "time_since_last_successful_payment_days": 30.0,
            "time_since_last_failed_attempt_hours": 12.0,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 10,
            "customer_id": "finance@enterprise-corp.com",
            "expected_actions": ["SEND_INVOICE", "SEND_PAYMENT_LINK"],
            "expected_priority": ["HIGH"],
            "expected_escalation": True,
            "required_context_factors": [
                "customer_tenure",
                "failed_attempt_count",
            ],
            "context_hypothesis": "Enterprise tier corporate card failure. Formal B2B invoice or high-priority payment link with escalation fits corporate billing.",
        },
        # --- Scenario N: Blacklisted Velocity Attack ---
        {
            "case_id": "ctx_14_blacklisted_card_velocity_attack",
            "scenario_name": "Velocity Card Testing Attack Blocked",
            "amount": 250.0,
            "currency": "INR",
            "is_recurring_revenue": False,
            "error_code": "CARD_BLACKLISTED",
            "error_description": "Card blacklisted due to excessive velocity triggers.",
            "payment_attempts_count": 6,
            "previous_failed_attempts_count": 5,
            "previous_successful_payments_count": 0,
            "time_since_last_successful_payment_days": None,
            "time_since_last_failed_attempt_hours": 0.05,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 0,
            "customer_id": "attacker.bot@example.com",
            "expected_actions": ["NO_ACTION"],
            "expected_priority": ["HIGH"],
            "expected_escalation": True,
            "required_context_factors": [
                "fraud_indicator",
                "failed_attempt_count",
            ],
            "context_hypothesis": "Card testing velocity attack. Mandatory NO_ACTION and immediate security block.",
        },
        # --- Scenario O: Low-Value One-Off Micro-Purchase ---
        {
            "case_id": "ctx_15_micro_transaction_low_intent",
            "scenario_name": "Low-Value Micro-Transaction (₹49) Invalid CVV",
            "amount": 49.0,
            "currency": "INR",
            "is_recurring_revenue": False,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card CVV verification failed.",
            "payment_attempts_count": 1,
            "previous_failed_attempts_count": 0,
            "previous_successful_payments_count": 0,
            "time_since_last_successful_payment_days": None,
            "time_since_last_failed_attempt_hours": 0.1,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 0,
            "customer_id": "anon.buyer49@example.com",
            "expected_actions": ["SEND_PAYMENT_LINK"],
            "expected_priority": ["LOW"],
            "expected_escalation": False,
            "required_context_factors": [
                "failed_attempt_count",
            ],
            "context_hypothesis": "Low-value one-off checkout with typo. Low priority, standard automated link.",
        },
        # --- Scenario P: Indeterminate Missing Metadata ---
        {
            "case_id": "ctx_16_indeterminate_amount_missing_data",
            "scenario_name": "Indeterminate Missing Amount & Corrupt Payload",
            "amount": None,
            "currency": "INR",
            "is_recurring_revenue": False,
            "error_code": "UNKNOWN",
            "error_description": "Corrupt webhook payload without amount field.",
            "payment_attempts_count": 1,
            "previous_failed_attempts_count": 0,
            "previous_successful_payments_count": 0,
            "time_since_last_successful_payment_days": None,
            "time_since_last_failed_attempt_hours": 0.5,
            "has_active_recovery_link": False,
            "prior_recovery_links_count": 0,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 0,
            "customer_id": "corrupt.payload@example.com",
            "expected_actions": ["INVESTIGATE"],
            "expected_priority": ["MEDIUM", "HIGH"],
            "expected_escalation": True,
            "required_context_factors": [
                "missing_amount",
            ],
            "context_hypothesis": "Indeterminate amount metadata. Automated link creation blocked; INVESTIGATE with human review required.",
        },
    ]
    return cases


def evaluate_deterministic_contextual_heuristic(case: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates a non-LLM, rule-based contextual heuristic baseline to compare against Gemini.
    Uses simple, transparent multi-dimensional rules without deep language reasoning.
    """
    amount = case.get("amount")
    is_recurring = bool(case.get("is_recurring_revenue"))
    attempts = case.get("payment_attempts_count", 1)
    tenure = case.get("customer_tenure_months", 0)
    prev_success = case.get("previous_successful_payments_count", 0)
    ignored_link = bool(case.get("recovery_link_previously_ignored"))
    err_desc = (case.get("error_description") or "").lower()
    err_code = str(case.get("error_code") or "").lower()

    # Determine Priority Heuristic
    is_high_val = amount is not None and amount >= HIGH_VALUE_THRESHOLD
    is_fraud = "stolen" in err_desc or "fraud" in err_desc or "blacklisted" in err_code or "blocked" in err_code
    is_gw_transient = "gateway" in err_desc and prev_success > 0 and attempts <= 1

    if is_high_val or is_fraud or (tenure >= 12 and is_recurring):
        priority = "HIGH"
        urgency_score = 5
    elif is_gw_transient or (amount is not None and amount < 500):
        priority = "LOW"
        urgency_score = 1
    else:
        priority = "MEDIUM"
        urgency_score = 3

    # Determine Escalation Heuristic
    if is_fraud or is_high_val or attempts >= 3 or ignored_link or amount is None:
        escalation = True
    else:
        escalation = False

    # Extract Baseline Factors
    factors = []
    if tenure > 0:
        factors.append("customer_tenure")
    if prev_success > 0:
        factors.append("previous_successful_payments")
    if attempts > 1:
        factors.append("failed_attempt_count")
    if ignored_link:
        factors.append("ignored_recovery_link")
    if is_high_val:
        factors.append("high_value_transaction")
    if is_fraud:
        factors.append("fraud_indicator")
    if "gateway" in err_desc:
        factors.append("transient_gateway_error")
    if amount is None:
        factors.append("missing_amount")

    # Generate Deterministic Baseline Reason
    reason = (
        f"Deterministic heuristic: amount={amount}, tenure={tenure}m, attempts={attempts}, "
        f"escalation={escalation}, priority={priority}."
    )

    return {
        "urgency_score": urgency_score,
        "priority": priority,
        "escalation_recommended": escalation,
        "contextual_factors_used": factors,
        "reason": reason,
    }


def score_explanation_quality(explanation: str, case: Dict[str, Any], action: str) -> Dict[str, Any]:
    """
    Evaluates explanation quality using a deterministic 5-point rubric:
    1. Identifies the specific failure cause (+1)
    2. References relevant customer profile / history / tenure (+1)
    3. Explains why the selected action / urgency is appropriate (+1)
    4. Acknowledges safety, fraud, or duplicate-charge considerations (+1)
    5. Specific and grounded without generic boilerplate (+1)
    """
    text = (explanation or "").lower()
    score = 0
    breakdown = {}

    # Criterion 1: Failure Cause Identified
    err_desc_words = [w for w in re.findall(r"\w+", (case.get("error_description") or "").lower()) if len(w) > 3]
    failure_matched = any(w in text for w in err_desc_words) or any(
        k in text for k in ["limit", "insufficient", "expired", "timeout", "declined", "fraud", "stolen", "gateway", "cvv", "otp", "blocked"]
    )
    if failure_matched:
        score += 1
        breakdown["failure_identified"] = True
    else:
        breakdown["failure_identified"] = False

    # Criterion 2: Customer History / Tenure Referenced
    history_matched = any(
        k in text for k in ["tenure", "month", "subscriber", "loyal", "previous", "prior", "attempt", "ignored", "history", "success", "vip", "first-time", "first time", "new customer"]
    )
    if history_matched:
        score += 1
        breakdown["history_referenced"] = True
    else:
        breakdown["history_referenced"] = False

    # Criterion 3: Action & Urgency Rationale
    action_matched = any(
        k in text for k in ["link", "invoice", "wait", "investigate", "no action", "halt", "pause", "reissue", "retry", "priority", "mrr", "revenue", "escalat"]
    )
    if action_matched:
        score += 1
        breakdown["action_rationale"] = True
    else:
        breakdown["action_rationale"] = False

    # Criterion 4: Safety / Guardrail / Risk Awareness
    safety_matched = any(
        k in text for k in ["approval", "duplicate", "fraud", "security", "protect", "risk", "charge twice", "stolen", "unauthorized", "guardrail", "human review"]
    )
    if safety_matched:
        score += 1
        breakdown["safety_awareness"] = True
    else:
        breakdown["safety_awareness"] = False

    # Criterion 5: Specificity & Grounding (Length >= 40 chars and no generic placeholder)
    is_grounded = len(explanation) >= 40 and not any(p in text for p in ["placeholder", "todo", "generic template", "null", "undefined"])
    if is_grounded:
        score += 1
        breakdown["grounded_and_specific"] = True
    else:
        breakdown["grounded_and_specific"] = False

    breakdown["total_score"] = score
    breakdown["max_score"] = 5
    return breakdown


def evaluate_context_factor_utilization(
    model_factors: List[str],
    required_factors: List[str],
) -> Dict[str, Any]:
    """
    Computes deterministic context-factor coverage and precision against ground truth.
    """
    normalized_model = {re.sub(r"[^a-z0-9_]", "_", f.lower().strip()) for f in model_factors if f}
    normalized_required = {re.sub(r"[^a-z0-9_]", "_", f.lower().strip()) for f in required_factors if f}

    if not normalized_required:
        return {
            "required_count": 0,
            "matched_count": 0,
            "coverage_percentage": 100.0,
            "matched_factors": [],
            "missing_factors": [],
        }

    # Match factor substrings or exact matches
    matched = []
    missing = []
    for req in normalized_required:
        if any(req in mf or mf in req for mf in normalized_model):
            matched.append(req)
        else:
            missing.append(req)

    coverage_pct = round(len(matched) / len(normalized_required) * 100.0, 1)
    return {
        "required_count": len(normalized_required),
        "matched_count": len(matched),
        "coverage_percentage": coverage_pct,
        "matched_factors": matched,
        "missing_factors": missing,
    }


def run_contextual_evaluation(
    db_path: str = "data/evaluation.db",
    llm_provider: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Executes the Contextual AI Intelligence Evaluation over the 16 complex benchmark cases.
    Compares Gemini advisory reasoning, deterministic policy, and the deterministic heuristic baseline.
    """
    dataset = generate_contextual_benchmark_dataset()
    total_cases = len(dataset)

    # Resolve Gemini / LLM provider if needed
    resolved_provider = llm_provider
    if resolved_provider is None:
        gemini_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
        if gemini_key:
            resolved_provider = GeminiProvider(api_key=gemini_key)
        elif settings.OPENAI_API_KEY:
            resolved_provider = OpenAIProvider(api_key=settings.OPENAI_API_KEY)

    evaluated_cases: List[Dict[str, Any]] = []
    policy_agreement_count = 0
    policy_difference_count = 0
    guardrail_override_count = 0
    fraud_blocks = 0
    human_approvals = 0
    fallback_count = 0
    rich_rationale_count = 0

    # Milestone 15A Contextual Metrics
    ai_priority_matches = 0
    baseline_priority_matches = 0
    ai_escalation_matches = 0
    baseline_escalation_matches = 0
    ai_total_factor_coverage = 0.0
    baseline_total_factor_coverage = 0.0
    ai_total_explanation_score = 0
    baseline_total_explanation_score = 0

    for case in dataset:
        c_id = case["case_id"]
        scenario = case["scenario_name"]
        expected_acts = case.get("expected_actions", [])
        expected_prio = case.get("expected_priority", ["MEDIUM"])
        if isinstance(expected_prio, str):
            expected_prio = [expected_prio]
        expected_esc = bool(case.get("expected_escalation", False))
        required_factors = case.get("required_context_factors", [])

        # 1. Authoritative Deterministic Execution Policy
        det_decision = decide_recovery_action(case)
        det_act = det_decision["action"]
        det_conf = round(float(det_decision.get("confidence", 0.85)), 2)

        # 2. Deterministic Contextual Baseline Heuristic
        baseline_ctx = evaluate_deterministic_contextual_heuristic(case)
        base_prio = baseline_ctx["priority"]
        base_esc = baseline_ctx["escalation_recommended"]
        base_factors = baseline_ctx["contextual_factors_used"]
        base_reason = baseline_ctx["reason"]

        # 3. Gemini Advisory Contextual Decision
        llm_decision = ai_decide_recovery_action(case, llm_provider=resolved_provider)
        llm_act = llm_decision["action"]
        llm_conf = round(float(llm_decision.get("confidence", 0.85)), 2)
        llm_prio = llm_decision.get("priority", "MEDIUM")
        llm_urgency = llm_decision.get("urgency_score", 3)
        llm_esc = bool(llm_decision.get("escalation_recommended", False))
        llm_factors = llm_decision.get("contextual_factors_used", [])
        llm_src = llm_decision.get("decision_source", "deterministic_fallback")
        llm_reason = llm_decision.get("reason", "")
        requires_approval = bool(llm_decision.get("requires_human_approval", False))

        if llm_src == "llm":
            rich_rationale_count += 1
        else:
            fallback_count += 1

        # Check Policy Alignment (Safety invariant)
        is_diff = (det_act != llm_act)
        if is_diff:
            policy_difference_count += 1
        else:
            policy_agreement_count += 1

        if requires_approval:
            human_approvals += 1

        if llm_act == "NO_ACTION" or "stolen" in (case.get("error_description") or "").lower():
            fraud_blocks += 1

        # Evaluate Ground Truth Priority Agreement
        ai_prio_match = (llm_prio in expected_prio)
        base_prio_match = (base_prio in expected_prio)
        if ai_prio_match:
            ai_priority_matches += 1
        if base_prio_match:
            baseline_priority_matches += 1

        # Evaluate Ground Truth Escalation Agreement
        ai_esc_match = (llm_esc == expected_esc)
        base_esc_match = (base_esc == expected_esc)
        if ai_esc_match:
            ai_escalation_matches += 1
        if base_esc_match:
            baseline_escalation_matches += 1

        # Evaluate Context Factor Utilization
        ai_factor_metrics = evaluate_context_factor_utilization(llm_factors, required_factors)
        base_factor_metrics = evaluate_context_factor_utilization(base_factors, required_factors)
        ai_total_factor_coverage += ai_factor_metrics["coverage_percentage"]
        baseline_total_factor_coverage += base_factor_metrics["coverage_percentage"]

        # Evaluate Explanation Quality Rubric (0-5)
        ai_rubric = score_explanation_quality(llm_reason, case, llm_act)
        base_rubric = score_explanation_quality(base_reason, case, det_act)
        ai_total_explanation_score += ai_rubric["total_score"]
        baseline_total_explanation_score += base_rubric["total_score"]

        evaluated_cases.append({
            "case_id": c_id,
            "scenario_name": scenario,
            "amount": case.get("amount"),
            "currency": case.get("currency", "INR"),
            "failure_category": det_decision.get("failure_category", "UNKNOWN"),
            "deterministic_action": det_act,
            "deterministic_confidence": det_conf,
            "gemini_action": llm_act,
            "gemini_confidence": llm_conf,
            "final_action_after_guardrails": llm_act,
            "requires_human_approval": requires_approval,
            "decision_source": llm_src,
            "action_difference_flag": is_diff,
            "expected_actions": expected_acts,
            "expected_priority": expected_prio,
            "expected_escalation": expected_esc,
            "gemini_priority": llm_prio,
            "gemini_urgency_score": llm_urgency,
            "gemini_escalation_recommended": llm_esc,
            "gemini_contextual_factors": llm_factors,
            "gemini_factor_coverage_pct": ai_factor_metrics["coverage_percentage"],
            "gemini_explanation_score": ai_rubric["total_score"],
            "baseline_priority": base_prio,
            "baseline_escalation_recommended": base_esc,
            "baseline_factor_coverage_pct": base_factor_metrics["coverage_percentage"],
            "baseline_explanation_score": base_rubric["total_score"],
            "deterministic_reason": det_decision.get("reason", ""),
            "gemini_contextual_reason": llm_reason,
            "context_hypothesis": case.get("context_hypothesis", ""),
        })

    # Summary Computations
    policy_agreement_pct = round((policy_agreement_count / total_cases * 100.0), 1) if total_cases > 0 else 0.0
    policy_diff_pct = round((policy_difference_count / total_cases * 100.0), 1) if total_cases > 0 else 0.0
    fallback_pct = round((fallback_count / total_cases * 100.0), 1) if total_cases > 0 else 0.0

    ai_prio_agreement_pct = round((ai_priority_matches / total_cases * 100.0), 1) if total_cases > 0 else 0.0
    base_prio_agreement_pct = round((baseline_priority_matches / total_cases * 100.0), 1) if total_cases > 0 else 0.0

    ai_esc_agreement_pct = round((ai_escalation_matches / total_cases * 100.0), 1) if total_cases > 0 else 0.0
    base_esc_agreement_pct = round((baseline_escalation_matches / total_cases * 100.0), 1) if total_cases > 0 else 0.0

    ai_avg_factor_cov_pct = round(ai_total_factor_coverage / total_cases, 1) if total_cases > 0 else 0.0
    base_avg_factor_cov_pct = round(baseline_total_factor_coverage / total_cases, 1) if total_cases > 0 else 0.0

    ai_avg_explanation_score = round(ai_total_explanation_score / total_cases, 2) if total_cases > 0 else 0.0
    base_avg_explanation_score = round(baseline_total_explanation_score / total_cases, 2) if total_cases > 0 else 0.0

    summary_metrics = {
        "total_contextual_cases": total_cases,
        "policy_agreement_count": policy_agreement_count,
        "policy_agreement_percentage": policy_agreement_pct,
        "policy_difference_count": policy_difference_count,
        "policy_difference_percentage": policy_diff_pct,
        "guardrail_override_count": guardrail_override_count,
        "fraud_blocks_enforced": fraud_blocks,
        "human_approvals_mandated": human_approvals,
        "fallback_count": fallback_count,
        "fallback_rate_percentage": fallback_pct,
        "rich_contextual_rationale_count": rich_rationale_count,
        # Milestone 15A Intelligence Metrics
        "ai_priority_agreement_percentage": ai_prio_agreement_pct,
        "baseline_priority_agreement_percentage": base_prio_agreement_pct,
        "ai_escalation_agreement_percentage": ai_esc_agreement_pct,
        "baseline_escalation_agreement_percentage": base_esc_agreement_pct,
        "ai_context_factor_coverage_percentage": ai_avg_factor_cov_pct,
        "baseline_context_factor_coverage_percentage": base_avg_factor_cov_pct,
        "ai_average_explanation_score": ai_avg_explanation_score,
        "baseline_average_explanation_score": base_avg_explanation_score,
        "evaluation_timestamp": datetime.now(timezone.utc).isoformat(),
        "is_simulated_evaluation": True,
    }

    # Save to isolated evaluation database (data/evaluation.db ONLY)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contextual_evaluations (
                run_id TEXT PRIMARY KEY,
                total_cases INTEGER NOT NULL,
                agreement_rate_percentage REAL NOT NULL,
                difference_count INTEGER NOT NULL,
                guardrail_overrides INTEGER NOT NULL,
                fraud_blocks INTEGER NOT NULL,
                human_approvals INTEGER NOT NULL,
                summary_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contextual_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                scenario_name TEXT NOT NULL,
                amount REAL,
                failure_category TEXT NOT NULL,
                deterministic_action TEXT NOT NULL,
                gemini_action TEXT NOT NULL,
                requires_human_approval INTEGER NOT NULL,
                action_diff_flag INTEGER NOT NULL,
                gemini_reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        run_id = f"ctx_eval_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        conn.execute("""
            INSERT INTO contextual_evaluations (
                run_id, total_cases, agreement_rate_percentage, difference_count,
                guardrail_overrides, fraud_blocks, human_approvals, summary_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            run_id,
            total_cases,
            policy_agreement_pct,
            policy_difference_count,
            guardrail_override_count,
            fraud_blocks,
            human_approvals,
            json.dumps(summary_metrics),
        ))

        conn.executemany("""
            INSERT INTO contextual_cases (
                run_id, case_id, scenario_name, amount, failure_category,
                deterministic_action, gemini_action, requires_human_approval,
                action_diff_flag, gemini_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, [
            (
                run_id,
                ec["case_id"],
                ec["scenario_name"],
                ec["amount"],
                ec["failure_category"],
                ec["deterministic_action"],
                ec["gemini_action"],
                1 if ec["requires_human_approval"] else 0,
                1 if ec["action_difference_flag"] else 0,
                ec["gemini_contextual_reason"],
            )
            for ec in evaluated_cases
        ])
        conn.commit()

    return {
        "run_id": run_id,
        "summary": summary_metrics,
        "evaluated_cases": evaluated_cases,
        "cases": evaluated_cases,
    }


def get_latest_contextual_evaluation(db_path: str = "data/evaluation.db") -> Optional[Dict[str, Any]]:
    """
    Retrieves the most recent contextual evaluation results from the isolated evaluation database.
    """
    if not os.path.exists(db_path):
        return None

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM contextual_evaluations ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None

            run_id = row["run_id"]
            summary = json.loads(row["summary_json"]) if row["summary_json"] else {}

            case_rows = conn.execute(
                "SELECT * FROM contextual_cases WHERE run_id = ? ORDER BY id ASC", (run_id,)
            ).fetchall()

            cases = [dict(c) for c in case_rows]

            return {
                "run_id": run_id,
                "summary": summary,
                "cases": cases,
                "evaluated_cases": cases,
                "created_at": row["created_at"],
            }
    except Exception as exc:
        logger.warning(f"[Contextual Evaluator] Failed to fetch latest evaluation: {exc}")
        return None

