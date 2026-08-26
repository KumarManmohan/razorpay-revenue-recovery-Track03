import json
import sqlite3
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from app.database import _get_connection, _now_iso, init_db
from app.failure_classifier import (
    CATEGORY_INSUFFICIENT_FUNDS,
    CATEGORY_CARD_LIMIT_EXCEEDED,
    CATEGORY_CARD_EXPIRED,
    CATEGORY_INVALID_CARD,
    CATEGORY_AUTHENTICATION_REQUIRED,
    CATEGORY_BANK_DECLINED,
    CATEGORY_TEMPORARY_GATEWAY_ERROR,
    CATEGORY_FRAUD_OR_SECURITY,
    CATEGORY_UNKNOWN,
    CATEGORY_LABELS,
)

DEMO_CASES: List[Dict[str, Any]] = [
    {
        "id": "case_demo_insufficient_funds_01",
        "event_id": "evt_demo_funds_001",
        "payment_id": "pay_demo_funds_001",
        "order_id": "order_demo_ecom_101",
        "subscription_id": None,
        "customer_id": "cust_demo_aarav_sharma",
        "amount": 2499.0,
        "currency": "INR",
        "payment_status": "failed",
        "is_recurring_revenue": 0,
        "risk_status": "at_risk",
        "risk_reason": "Payment failed: Insufficient funds in customer account",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Insufficient funds in customer account",
        "failure_category": CATEGORY_INSUFFICIENT_FUNDS,
        "failure_category_label": CATEGORY_LABELS[CATEGORY_INSUFFICIENT_FUNDS],
        "decision_action": "SEND_PAYMENT_LINK",
        "decision_confidence": 0.88,
        "decision_reason": "Payment failed due to insufficient account balance. Issuing a payment link allowing the customer to complete payment with another account or method.",
        "requires_human_approval": 0,
        "decision_source": "deterministic_fallback",
        "execution_status": "pending",
        "payment_link_id": None,
        "payment_link_url": None,
        "recovered_amount": None,
        "recovered_payment_id": None,
        "recovered_at": None,
    },
    {
        "id": "case_demo_card_limit_02",
        "event_id": "evt_demo_limit_002",
        "payment_id": "pay_demo_limit_002",
        "order_id": None,
        "subscription_id": "sub_demo_saas_pro",
        "customer_id": "cust_demo_priya_patel",
        "amount": 8999.0,
        "currency": "INR",
        "payment_status": "failed",
        "is_recurring_revenue": 1,
        "risk_status": "at_risk",
        "risk_reason": "Payment failed: Card limit exceeded for monthly transaction",
        "error_code": "GATEWAY_ERROR",
        "error_description": "Card limit exceeded for monthly transaction",
        "failure_category": CATEGORY_CARD_LIMIT_EXCEEDED,
        "failure_category_label": CATEGORY_LABELS[CATEGORY_CARD_LIMIT_EXCEEDED],
        "decision_action": "SEND_PAYMENT_LINK",
        "decision_confidence": 0.90,
        "decision_reason": "Recurring payment failed because the transaction exceeded the customer's card spending limit. Recommending a payment link allowing payment via a different card or UPI.",
        "requires_human_approval": 0,
        "decision_source": "deterministic_fallback",
        "execution_status": "executed",
        "payment_link_id": "plink_demo_limit_002",
        "payment_link_url": "https://rzp.io/i/demo_lim_002",
        "recovered_amount": None,
        "recovered_payment_id": None,
        "recovered_at": None,
    },
    {
        "id": "case_demo_card_expired_03",
        "event_id": "evt_demo_expired_003",
        "payment_id": "pay_demo_expired_003",
        "order_id": None,
        "subscription_id": "sub_demo_annual_vip",
        "customer_id": "cust_demo_rohit_verma",
        "amount": 14999.0,
        "currency": "INR",
        "payment_status": "failed",
        "is_recurring_revenue": 1,
        "risk_status": "at_risk",
        "risk_reason": "Payment failed: Card expired on registered account",
        "error_code": "CARD_EXPIRED",
        "error_description": "Card expired on registered account",
        "failure_category": CATEGORY_CARD_EXPIRED,
        "failure_category_label": CATEGORY_LABELS[CATEGORY_CARD_EXPIRED],
        "decision_action": "SEND_PAYMENT_LINK",
        "decision_confidence": 0.92,
        "decision_reason": "Recurring subscription payment failed because the customer's registered card has expired. Recommending an automated payment link for the customer to update card details.",
        "requires_human_approval": 0,
        "decision_source": "deterministic_fallback",
        "execution_status": "executed",
        "payment_link_id": "plink_demo_exp_003",
        "payment_link_url": "https://rzp.io/i/demo_exp_003",
        "recovered_amount": None,
        "recovered_payment_id": None,
        "recovered_at": None,
    },
    {
        "id": "case_demo_auth_required_04",
        "event_id": "evt_demo_auth_004",
        "payment_id": "pay_demo_auth_004",
        "order_id": "order_demo_checkout_204",
        "subscription_id": None,
        "customer_id": "cust_demo_ananya_deshmukh",
        "amount": 3500.0,
        "currency": "INR",
        "payment_status": "failed",
        "is_recurring_revenue": 0,
        "risk_status": "at_risk",
        "risk_reason": "Payment failed: Customer 3DS authentication timed out",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Customer 3DS authentication timed out",
        "failure_category": CATEGORY_AUTHENTICATION_REQUIRED,
        "failure_category_label": CATEGORY_LABELS[CATEGORY_AUTHENTICATION_REQUIRED],
        "decision_action": "SEND_PAYMENT_LINK",
        "decision_confidence": 0.90,
        "decision_reason": "Customer 3DS / OTP authentication timed out or was cancelled during checkout. Reissuing payment link to complete authentication.",
        "requires_human_approval": 0,
        "decision_source": "deterministic_fallback",
        "execution_status": "pending",
        "payment_link_id": None,
        "payment_link_url": None,
        "recovered_amount": None,
        "recovered_payment_id": None,
        "recovered_at": None,
    },
    {
        "id": "case_demo_invalid_card_05",
        "event_id": "evt_demo_invalid_005",
        "payment_id": "pay_demo_invalid_005",
        "order_id": "order_demo_cart_305",
        "subscription_id": None,
        "customer_id": "cust_demo_vikram_singh",
        "amount": 1200.0,
        "currency": "INR",
        "payment_status": "failed",
        "is_recurring_revenue": 0,
        "risk_status": "at_risk",
        "risk_reason": "Payment failed: Invalid card number or security CVV",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Invalid card number or security CVV",
        "failure_category": CATEGORY_INVALID_CARD,
        "failure_category_label": CATEGORY_LABELS[CATEGORY_INVALID_CARD],
        "decision_action": "SEND_PAYMENT_LINK",
        "decision_confidence": 0.88,
        "decision_reason": "Payment rejected due to invalid card number or security details. Sending payment link so customer can re-enter valid payment details.",
        "requires_human_approval": 0,
        "decision_source": "deterministic_fallback",
        "execution_status": "pending",
        "payment_link_id": None,
        "payment_link_url": None,
        "recovered_amount": None,
        "recovered_payment_id": None,
        "recovered_at": None,
    },
    {
        "id": "case_demo_bank_declined_06",
        "event_id": "evt_demo_bank_006",
        "payment_id": "pay_demo_bank_006",
        "order_id": "order_demo_sub_406",
        "subscription_id": None,
        "customer_id": "cust_demo_deepak_nair",
        "amount": 4200.0,
        "currency": "INR",
        "payment_status": "failed",
        "is_recurring_revenue": 0,
        "risk_status": "at_risk",
        "risk_reason": "Payment failed: Transaction declined by issuing bank (Do Not Honor)",
        "error_code": "GATEWAY_ERROR",
        "error_description": "Transaction declined by issuing bank (Do Not Honor)",
        "failure_category": CATEGORY_BANK_DECLINED,
        "failure_category_label": CATEGORY_LABELS[CATEGORY_BANK_DECLINED],
        "decision_action": "SEND_PAYMENT_LINK",
        "decision_confidence": 0.82,
        "decision_reason": "Payment was declined by customer's issuing bank. Sending a payment link allowing payment with a different bank card or UPI.",
        "requires_human_approval": 0,
        "decision_source": "deterministic_fallback",
        "execution_status": "pending",
        "payment_link_id": None,
        "payment_link_url": None,
        "recovered_amount": None,
        "recovered_payment_id": None,
        "recovered_at": None,
    },
    {
        "id": "case_demo_gateway_timeout_07",
        "event_id": "evt_demo_gateway_007",
        "payment_id": "pay_demo_gateway_007",
        "order_id": None,
        "subscription_id": "sub_demo_growth_plan",
        "customer_id": "cust_demo_kavita_reddy",
        "amount": 6500.0,
        "currency": "INR",
        "payment_status": "failed",
        "is_recurring_revenue": 1,
        "risk_status": "at_risk",
        "risk_reason": "Payment failed: Bank gateway timeout: system error 504",
        "error_code": "SERVER_ERROR",
        "error_description": "Bank gateway timeout: system error 504",
        "failure_category": CATEGORY_TEMPORARY_GATEWAY_ERROR,
        "failure_category_label": CATEGORY_LABELS[CATEGORY_TEMPORARY_GATEWAY_ERROR],
        "decision_action": "WAIT",
        "decision_confidence": 0.85,
        "decision_reason": "Temporary gateway or banking infrastructure glitch detected. Waiting before automated retry to prevent duplicate charges.",
        "requires_human_approval": 0,
        "decision_source": "deterministic_fallback",
        "execution_status": "pending",
        "payment_link_id": None,
        "payment_link_url": None,
        "recovered_amount": None,
        "recovered_payment_id": None,
        "recovered_at": None,
    },
    {
        "id": "case_demo_fraud_security_08",
        "event_id": "evt_demo_fraud_008",
        "payment_id": "pay_demo_fraud_008",
        "order_id": "order_demo_sec_508",
        "subscription_id": None,
        "customer_id": "cust_demo_suspicious_account",
        "amount": 18500.0,
        "currency": "INR",
        "payment_status": "failed",
        "is_recurring_revenue": 0,
        "risk_status": "at_risk",
        "risk_reason": "Payment failed: Card blocked: Reported lost or stolen instrument",
        "error_code": "SECURITY_ALERT",
        "error_description": "Card blocked: Reported lost or stolen instrument",
        "failure_category": CATEGORY_FRAUD_OR_SECURITY,
        "failure_category_label": CATEGORY_LABELS[CATEGORY_FRAUD_OR_SECURITY],
        "decision_action": "NO_ACTION",
        "decision_confidence": 0.95,
        "decision_reason": "Payment was flagged for security or fraud risk (blocked/stolen instrument). Automated recovery is halted and flagged for compliance review.",
        "requires_human_approval": 1,
        "decision_source": "deterministic_fallback",
        "execution_status": "approval_required",
        "payment_link_id": None,
        "payment_link_url": None,
        "recovered_amount": None,
        "recovered_payment_id": None,
        "recovered_at": None,
    },
    {
        "id": "case_demo_unknown_failure_09",
        "event_id": "evt_demo_unknown_009",
        "payment_id": "pay_demo_unknown_009",
        "order_id": "order_demo_unk_609",
        "subscription_id": None,
        "customer_id": "cust_demo_indeterminate_user",
        "amount": 5000.0,
        "currency": "INR",
        "payment_status": "failed",
        "is_recurring_revenue": 0,
        "risk_status": "needs_investigation",
        "risk_reason": "Uncertain risk state for payment status: 'failed'",
        "error_code": None,
        "error_description": None,
        "failure_category": CATEGORY_UNKNOWN,
        "failure_category_label": CATEGORY_LABELS[CATEGORY_UNKNOWN],
        "decision_action": "INVESTIGATE",
        "decision_confidence": 0.50,
        "decision_reason": "Payment failure occurred with no gateway error description or error code provided. Investigation required.",
        "requires_human_approval": 1,
        "decision_source": "deterministic_fallback",
        "execution_status": "approval_required",
        "payment_link_id": None,
        "payment_link_url": None,
        "recovered_amount": None,
        "recovered_payment_id": None,
        "recovered_at": None,
    },
    {
        "id": "case_demo_high_value_10",
        "event_id": "evt_demo_enterprise_010",
        "payment_id": "pay_demo_enterprise_010",
        "order_id": None,
        "subscription_id": "sub_demo_enterprise_annual",
        "customer_id": "cust_demo_acme_corp",
        "amount": 75000.0,
        "currency": "INR",
        "payment_status": "failed",
        "is_recurring_revenue": 1,
        "risk_status": "at_risk",
        "risk_reason": "Payment failed: Card limit exceeded on corporate card",
        "error_code": "GATEWAY_ERROR",
        "error_description": "Card limit exceeded on corporate card",
        "failure_category": CATEGORY_CARD_LIMIT_EXCEEDED,
        "failure_category_label": CATEGORY_LABELS[CATEGORY_CARD_LIMIT_EXCEEDED],
        "decision_action": "SEND_PAYMENT_LINK",
        "decision_confidence": 0.90,
        "decision_reason": "Recurring payment failed because the transaction exceeded the customer's card spending limit. Recommending a payment link allowing payment via a different card or UPI.",
        "requires_human_approval": 1,
        "decision_source": "deterministic_fallback",
        "execution_status": "approval_required",
        "payment_link_id": None,
        "payment_link_url": None,
        "recovered_amount": None,
        "recovered_payment_id": None,
        "recovered_at": None,
    },
    {
        "id": "case_demo_recovered_11",
        "event_id": "evt_demo_recovered_011",
        "payment_id": "pay_demo_recovered_011",
        "order_id": None,
        "subscription_id": "sub_demo_saas_scale",
        "customer_id": "cust_demo_techcorp_solutions",
        "amount": 12500.0,
        "currency": "INR",
        "payment_status": "failed",
        "is_recurring_revenue": 1,
        "risk_status": "at_risk",
        "risk_reason": "Payment failed: Insufficient funds in customer account",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Insufficient funds in customer account",
        "failure_category": CATEGORY_INSUFFICIENT_FUNDS,
        "failure_category_label": CATEGORY_LABELS[CATEGORY_INSUFFICIENT_FUNDS],
        "decision_action": "SEND_PAYMENT_LINK",
        "decision_confidence": 0.88,
        "decision_reason": "Recurring subscription payment failed due to insufficient funds in customer account. Sending a payment link so the customer can retry with an alternate card, UPI, or netbanking.",
        "requires_human_approval": 0,
        "decision_source": "deterministic_fallback",
        "execution_status": "recovered",
        "payment_link_id": "plink_demo_recov_011",
        "payment_link_url": "https://rzp.io/i/demo_rec_011",
        "recovered_amount": 12500.0,
        "recovered_payment_id": "pay_demo_captured_011_rec",
        "recovered_at": "2026-08-22T20:30:00+00:00",
    },
]


def reset_demo_dataset(db_path: Optional[str] = None) -> int:
    """
    Safely removes only demo cases (id LIKE 'case_demo_%') and their audit events,
    leaving any real Razorpay Test Mode transactions untouched.

    Returns:
        int: Number of demo cases deleted.
    """
    init_db(db_path)
    conn = _get_connection(db_path)
    try:
        with conn:
            # Delete audit events for demo cases
            conn.execute("DELETE FROM audit_events WHERE case_id LIKE 'case_demo_%'")
            # Delete demo cases
            cursor = conn.execute("DELETE FROM recovery_cases WHERE id LIKE 'case_demo_%'")
            return cursor.rowcount
    finally:
        conn.close()


def seed_demo_dataset(db_path: Optional[str] = None, reset_first: bool = True) -> Dict[str, int]:
    """
    Deterministically seeds the comprehensive Phase 13 demo dataset into SQLite.

    Args:
        db_path: Optional SQLite database file path.
        reset_first: If True, purges existing demo records before seeding.

    Returns:
        Dict with count of seeded cases and audit events.
    """
    init_db(db_path)
    if reset_first:
        reset_demo_dataset(db_path)

    conn = _get_connection(db_path)
    cases_seeded = 0
    events_seeded = 0
    now = _now_iso()

    try:
        with conn:
            for case in DEMO_CASES:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO recovery_cases (
                        id, event_id, payment_id, order_id, subscription_id, customer_id,
                        amount, currency, payment_status, is_recurring_revenue,
                        risk_status, risk_reason, error_code, error_description,
                        failure_category, failure_category_label,
                        decision_action, decision_confidence, decision_reason,
                        requires_human_approval, decision_source, execution_status,
                        payment_link_id, payment_link_url,
                        recovered_amount, recovered_payment_id, recovered_at,
                        created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        case["id"],
                        case["event_id"],
                        case["payment_id"],
                        case["order_id"],
                        case["subscription_id"],
                        case["customer_id"],
                        case["amount"],
                        case["currency"],
                        case["payment_status"],
                        case["is_recurring_revenue"],
                        case["risk_status"],
                        case["risk_reason"],
                        case["error_code"],
                        case["error_description"],
                        case["failure_category"],
                        case["failure_category_label"],
                        case["decision_action"],
                        case["decision_confidence"],
                        case["decision_reason"],
                        case["requires_human_approval"],
                        case["decision_source"],
                        case["execution_status"],
                        case["payment_link_id"],
                        case["payment_link_url"],
                        case["recovered_amount"],
                        case["recovered_payment_id"],
                        case["recovered_at"],
                        now,
                        now,
                    ),
                )
                cases_seeded += 1

                # Add initial audit events for each demo case
                conn.execute(
                    """
                    INSERT INTO audit_events (case_id, event_type, message, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        case["id"],
                        "WEBHOOK_RECEIVED",
                        f"Demo payment failure webhook received ({case['payment_id']}).",
                        json.dumps({"demo": True, "error_desc": case["error_description"]}),
                        now,
                    ),
                )
                events_seeded += 1

                conn.execute(
                    """
                    INSERT INTO audit_events (case_id, event_type, message, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        case["id"],
                        "REVENUE_RISK_ANALYZED",
                        f"Risk analysis complete: Classified as '{case['failure_category_label']}' (₹{case['amount']:.2f}).",
                        json.dumps({"failure_category": case["failure_category"]}),
                        now,
                    ),
                )
                events_seeded += 1

                if case["execution_status"] in ("executed", "recovered"):
                    conn.execute(
                        """
                        INSERT INTO audit_events (case_id, event_type, message, metadata, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            case["id"],
                            "RECOVERY_LINK_CREATED",
                            f"Demo recovery payment link generated: {case['payment_link_url']}",
                            json.dumps({"payment_link_id": case["payment_link_id"]}),
                            now,
                        ),
                    )
                    events_seeded += 1

                if case["execution_status"] == "recovered":
                    conn.execute(
                        """
                        INSERT INTO audit_events (case_id, event_type, message, metadata, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            case["id"],
                            "REVENUE_RECOVERED",
                            f"Demo revenue successfully recovered: ₹{case['recovered_amount']:.2f} via {case['recovered_payment_id']}.",
                            json.dumps({"recovered_amount": case["recovered_amount"], "payment_id": case["recovered_payment_id"]}),
                            case["recovered_at"] or now,
                        ),
                    )
                    events_seeded += 1

        return {"cases_seeded": cases_seeded, "events_seeded": events_seeded}
    finally:
        conn.close()
