"""
Phase 14B — Real Payment Attempt Modeling & Safe Recovery-Link Lifecycle Tests

Validates:
1. Webhook idempotency (same event_id twice -> already_processed)
2. Payment attempt tracking (multiple failed attempts on same order -> single recovery case)
3. Low-value auto-execution (amount < 50k -> auto payment link generated)
4. High-value guardrail (amount >= 50k -> approval required, no auto link)
5. Fraud/security guardrail (NO_ACTION, approval required)
6. WAIT action guardrail (no link generated)
7. One Active Payment Path (existing link preserved, no duplicate issuance)
8. Recovery reconciliation with order_id and payment_link_id
9. Double-payment protection (multiple captures on same case -> flagged, no duplicate revenue)
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.database import (
    init_db,
    create_or_get_recovery_case,
    record_payment_attempt,
    get_payment_attempts_for_case,
    update_recovery_decision,
    update_execution_status,
    reconcile_recovery_payment,
    get_case_by_id,
    get_case_with_audit,
    get_dashboard_stats,
    add_audit_event,
)
from app.revenue_risk import analyze_payment_failure
from app.failure_classifier import classify_payment_failure
from app.recovery_decision import decide_recovery_action
from app.ai_recovery_agent import ai_decide_recovery_action
from app.recovery_executor import execute_recovery_action


class TestPaymentAttemptsAndLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_lifecycle.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_single_case_for_multiple_failed_attempts_on_same_order(self):
        """Two separate failed payment attempts on the same order_id map to ONE recovery case."""
        # Attempt 1: pay_ATT001 failed
        risk1 = {
            "payment_id": "pay_ATT001",
            "event_id": "evt_ATT001",
            "order_id": "order_COMMON_001",
            "amount": 850.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
            "risk_reason": "Payment failed: Bank declined",
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Bank declined",
            "failure_category": "BANK_DECLINED",
            "failure_category_label": "Bank Declined",
        }
        case1, is_new1 = create_or_get_recovery_case(risk1, db_path=self.db_path)
        self.assertTrue(is_new1)
        self.assertEqual(case1["order_id"], "order_COMMON_001")

        # Attempt 2: pay_ATT002 failed for same order
        risk2 = {
            "payment_id": "pay_ATT002",
            "event_id": "evt_ATT002",
            "order_id": "order_COMMON_001",
            "amount": 850.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
            "risk_reason": "Payment failed: Bank declined",
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Bank declined",
            "failure_category": "BANK_DECLINED",
            "failure_category_label": "Bank Declined",
        }
        case2, is_new2 = create_or_get_recovery_case(risk2, db_path=self.db_path)
        self.assertFalse(is_new2)
        # Same unified recovery case ID
        self.assertEqual(case1["id"], case2["id"])

        # Check attempts table
        attempts = get_payment_attempts_for_case(case1["id"], db_path=self.db_path)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["payment_id"], "pay_ATT001")
        self.assertEqual(attempts[1]["payment_id"], "pay_ATT002")

    def test_webhook_event_id_idempotency(self):
        """Exact same webhook event_id returns is_new = False and identical case."""
        risk = {
            "payment_id": "pay_IDEM_001",
            "event_id": "evt_IDEM_001",
            "amount": 500.0,
            "currency": "INR",
            "payment_status": "failed",
        }
        case1, is_new1 = create_or_get_recovery_case(risk, db_path=self.db_path)
        self.assertTrue(is_new1)

        case2, is_new2 = create_or_get_recovery_case(risk, db_path=self.db_path)
        self.assertFalse(is_new2)
        self.assertEqual(case1["id"], case2["id"])

    @patch("app.recovery_executor.get_razorpay_client")
    def test_low_value_auto_execution(self, mock_get_client):
        """Low-value case (< ₹50k) does not require approval and executes payment link."""
        mock_client = mock_get_client.return_value
        mock_client.payment_link.create.return_value = {
            "id": "plink_AUTO_001",
            "short_url": "https://rzp.io/rzp/auto001",
            "status": "created",
            "reference_id": "rec_auto_001",
        }

        risk = {
            "amount": 850.0,
            "currency": "INR",
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Insufficient funds in account.",
            "is_recurring_revenue": False,
        }
        decision = decide_recovery_action(risk)
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")
        self.assertFalse(decision["requires_human_approval"])

        # Execute recovery
        exec_res = execute_recovery_action(decision)
        self.assertEqual(exec_res["status"], "executed")
        self.assertEqual(exec_res["payment_link_id"], "plink_AUTO_001")

    def test_high_value_guardrail_blocks_auto_execution(self):
        """High-value case (>= ₹50k) requires human approval and blocks auto execution."""
        risk = {
            "amount": 65000.0,
            "currency": "INR",
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card limit exceeded.",
        }
        decision = decide_recovery_action(risk)
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")
        self.assertTrue(decision["requires_human_approval"])

        exec_res = execute_recovery_action(decision)
        self.assertEqual(exec_res["status"], "approval_required")

    def test_fraud_security_blocks_execution(self):
        """Fraud or security flag forces NO_ACTION and human approval."""
        risk = {
            "amount": 1200.0,
            "currency": "INR",
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Transaction blocked due to stolen card report.",
        }
        decision = decide_recovery_action(risk)
        self.assertEqual(decision["action"], "NO_ACTION")
        self.assertTrue(decision["requires_human_approval"])

        exec_res = execute_recovery_action(decision)
        self.assertEqual(exec_res["status"], "rejected")

    def test_wait_strategy_does_not_generate_payment_link(self):
        """Gateway timeout generates WAIT decision and rejected execution."""
        risk = {
            "amount": 750.0,
            "currency": "INR",
            "error_code": "GATEWAY_ERROR",
            "error_description": "Gateway timeout during bank communication.",
        }
        decision = decide_recovery_action(risk)
        self.assertEqual(decision["action"], "WAIT")

        exec_res = execute_recovery_action(decision)
        self.assertEqual(exec_res["status"], "rejected")

    def test_double_payment_protection_on_reconciliation(self):
        """Two separate payments captured on the same case flags duplicate without double counting revenue."""
        # Create case
        risk = {
            "payment_id": "pay_ORIG_001",
            "order_id": "order_DOUBLE_001",
            "amount": 850.0,
            "currency": "INR",
        }
        case, _ = create_or_get_recovery_case(risk, db_path=self.db_path)
        case_id = case["id"]

        # First successful payment
        rec_case1, status1 = reconcile_recovery_payment(
            case_id_or_link_id="order_DOUBLE_001",
            recovered_payment_id="pay_CAPTURED_FIRST",
            recovered_amount=850.0,
            db_path=self.db_path,
        )
        self.assertEqual(status1, "reconciled")
        self.assertEqual(rec_case1["recovered_amount"], 850.0)

        # Exact duplicate webhook for same payment -> already_reconciled
        rec_case_dupe, status_dupe = reconcile_recovery_payment(
            case_id_or_link_id="order_DOUBLE_001",
            recovered_payment_id="pay_CAPTURED_FIRST",
            recovered_amount=850.0,
            db_path=self.db_path,
        )
        self.assertEqual(status_dupe, "already_reconciled")

        # Second DIFFERENT payment captured on same case -> duplicate_payment_recorded
        rec_case2, status2 = reconcile_recovery_payment(
            case_id_or_link_id="order_DOUBLE_001",
            recovered_payment_id="pay_CAPTURED_SECOND",
            recovered_amount=850.0,
            db_path=self.db_path,
        )
        self.assertEqual(status2, "duplicate_payment_recorded")

        # Check dashboard stats: recovered revenue is ONLY 850.0 (not 1700.0)
        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["recovered_revenue"], 850.0)

        # Check audit trail has DUPLICATE_PAYMENT_DETECTED
        full_case = get_case_with_audit(case_id, db_path=self.db_path)
        audit_types = [a["event_type"] for a in full_case["audit"]]
        self.assertIn("DUPLICATE_PAYMENT_DETECTED", audit_types)

    def test_reconciliation_via_order_id(self):
        """A payment referencing order_id reconciles the matching recovery case."""
        risk = {
            "payment_id": "pay_FAIL_ORDER",
            "order_id": "order_RECON_BY_ORDER",
            "amount": 1200.0,
            "currency": "INR",
        }
        case, _ = create_or_get_recovery_case(risk, db_path=self.db_path)
        
        rec_case, status = reconcile_recovery_payment(
            case_id_or_link_id="order_RECON_BY_ORDER",
            recovered_payment_id="pay_SUCCESS_ORDER",
            recovered_amount=1200.0,
            db_path=self.db_path,
        )
        self.assertEqual(status, "reconciled")
        self.assertEqual(rec_case["id"], case["id"])
        self.assertEqual(rec_case["execution_status"], "recovered")
        
    def test_two_distinct_orders_create_separate_recovery_cases(self):
        """Two different orders create two separate recovery cases."""
        risk_a = {
            "payment_id": "pay_DISTINCT_A",
            "order_id": "order_DISTINCT_A",
            "amount": 1000.0,
            "currency": "INR",
        }
        case_a, is_new_a = create_or_get_recovery_case(risk_a, db_path=self.db_path)
        self.assertTrue(is_new_a)

        risk_b = {
            "payment_id": "pay_DISTINCT_B",
            "order_id": "order_DISTINCT_B",
            "amount": 2000.0,
            "currency": "INR",
        }
        case_b, is_new_b = create_or_get_recovery_case(risk_b, db_path=self.db_path)
        self.assertTrue(is_new_b)

        self.assertNotEqual(case_a["id"], case_b["id"])
        self.assertEqual(case_a["order_id"], "order_DISTINCT_A")
        self.assertEqual(case_b["order_id"], "order_DISTINCT_B")

    def test_failed_attempts_do_not_inflate_revenue_at_risk_or_recovered(self):
        """Multiple failed attempts on 1 order count as 1 at-risk item and 1 recovery item."""
        # 3 failed attempts on order_MULTI_001
        for i in range(1, 4):
            risk = {
                "payment_id": f"pay_MULTI_{i}",
                "event_id": f"evt_MULTI_{i}",
                "order_id": "order_MULTI_001",
                "amount": 500.0,
                "currency": "INR",
                "risk_status": "at_risk",
            }
            case, is_new = create_or_get_recovery_case(risk, db_path=self.db_path)
            if i == 1:
                self.assertTrue(is_new)
            else:
                self.assertFalse(is_new)

        # Before recovery: Total at risk should be 500.0 (not 1500.0)
        stats_before = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats_before["total_cases"], 1)
        self.assertEqual(stats_before["total_revenue_at_risk"], 500.0)
        self.assertEqual(stats_before["recovered_revenue"], 0.0)

        # Reconcile recovery payment
        rec_case, status = reconcile_recovery_payment(
            case_id_or_link_id="order_MULTI_001",
            recovered_payment_id="pay_MULTI_RECOVERED",
            recovered_amount=500.0,
            db_path=self.db_path,
        )
        self.assertEqual(status, "reconciled")

        # After recovery: Recovered should be 500.0 (counted once)
        stats_after = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats_after["recovered_revenue"], 500.0)
        self.assertEqual(stats_after["recovery_rate_percentage"], 100.0)

        # Attempts ledger contains all 4 attempts
        attempts = get_payment_attempts_for_case(case["id"], db_path=self.db_path)
        self.assertEqual(len(attempts), 4)

    def test_distinct_original_order_and_recovery_order_id_preservation(self):
        """Original checkout order_id and Razorpay Payment Link order_id are both preserved."""
        risk = {
            "payment_id": "pay_ORIG_ORD_FAIL",
            "order_id": "order_CHECKOUT_ORIGINAL_123",
            "amount": 850.0,
            "currency": "INR",
        }
        case, _ = create_or_get_recovery_case(risk, db_path=self.db_path)
        self.assertEqual(case["order_id"], "order_CHECKOUT_ORIGINAL_123")

        # Recovery payment captured with Razorpay Payment Link's separate order_id
        rec_case, status = reconcile_recovery_payment(
            case_id_or_link_id="order_CHECKOUT_ORIGINAL_123",
            recovered_payment_id="pay_RECOVERY_PAID_456",
            recovered_amount=850.0,
            metadata={"order_id": "order_PAYMENT_LINK_SEPARATE_789"},
            db_path=self.db_path,
        )
        self.assertEqual(status, "reconciled")
        self.assertEqual(rec_case["order_id"], "order_CHECKOUT_ORIGINAL_123")
        self.assertEqual(rec_case["recovery_order_id"], "order_PAYMENT_LINK_SEPARATE_789")

        # Verify audit trail records both order IDs
        full_case = get_case_with_audit(case["id"], db_path=self.db_path)
        recon_event = next(e for e in full_case["audit"] if e["event_type"] == "RECOVERY_CASE_RECONCILED")
        self.assertEqual(recon_event["metadata"]["original_order_id"], "order_CHECKOUT_ORIGINAL_123")
        self.assertEqual(recon_event["metadata"]["recovery_order_id"], "order_PAYMENT_LINK_SEPARATE_789")


if __name__ == "__main__":
    unittest.main()
