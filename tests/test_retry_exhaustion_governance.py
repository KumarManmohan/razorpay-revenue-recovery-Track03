"""
tests/test_retry_exhaustion_governance.py
=========================================
Comprehensive regression and governance test suite for Milestone 16A:
Deterministic retry exhaustion stopping rules, terminal state governance,
link cancellation, AI guardrail override prevention, and post-exhaustion recovery.
"""

import gc
import json
import os
import unittest
import uuid
from unittest.mock import MagicMock, patch

from app.config import settings
from app.database import (
    _get_connection,
    add_audit_event,
    approve_case,
    count_failed_attempts_for_case,
    create_or_get_recovery_case,
    evaluate_case_exhaustion,
    exhaust_recovery_case,
    get_case_by_id,
    get_case_with_audit,
    init_db,
    reconcile_recovery_payment,
    record_payment_attempt,
    update_execution_status,
    update_recovery_decision,
)
from app.recovery_decision import decide_recovery_action
from app.recovery_executor import execute_recovery_action
from app.ai_recovery_agent import enforce_ai_guardrails


class TestRetryExhaustionGovernance(unittest.TestCase):
    """Rigorous unit and integration test suite for retry exhaustion governance."""

    def setUp(self):
        self.test_db = f"data/test_exhaustion_{uuid.uuid4().hex[:8]}.db"
        init_db(self.test_db)

    def tearDown(self):
        gc.collect()
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except PermissionError:
                pass

    def test_01_three_failed_attempts_exhaust_case(self):
        """Test 1: Three failed payment attempts on a case trigger terminal exhaustion."""
        case_id = "case_test_exhaust_01"
        order_id = "order_exhaust_001"

        # Create initial case
        risk_payload = {
            "payment_id": "pay_att_01",
            "order_id": order_id,
            "amount": 1500.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card declined: insufficient limit.",
        }
        case_dict, is_new = create_or_get_recovery_case(risk_payload, db_path=self.test_db)
        actual_id = case_dict["id"]

        # Record 2nd failed attempt
        record_payment_attempt(
            case_id=actual_id,
            payment_id="pay_att_02",
            order_id=order_id,
            amount=1500.0,
            status="failed",
            error_code="BAD_REQUEST_ERROR",
            error_description="Card declined 2nd time.",
            db_path=self.test_db,
        )

        # Record 3rd failed attempt
        record_payment_attempt(
            case_id=actual_id,
            payment_id="pay_att_03",
            order_id=order_id,
            amount=1500.0,
            status="failed",
            error_code="BAD_REQUEST_ERROR",
            error_description="Card declined 3rd time.",
            db_path=self.test_db,
        )

        # Count failed attempts
        failed_count = count_failed_attempts_for_case(actual_id, db_path=self.test_db)
        self.assertEqual(failed_count, 3)

        # Evaluate exhaustion
        updated_case = get_case_by_id(actual_id, db_path=self.test_db)
        is_exhausted, reason, meta = evaluate_case_exhaustion(updated_case, db_path=self.test_db)
        self.assertTrue(is_exhausted)
        self.assertIn("Maximum failed payment attempts reached", reason)

        # Transition to exhausted
        exhausted_case, status_msg = exhaust_recovery_case(actual_id, reason=reason, metadata=meta, db_path=self.test_db)
        self.assertEqual(exhausted_case["execution_status"], "exhausted")
        self.assertEqual(exhausted_case["risk_status"], "at_risk")  # Still at risk financially

        # Verify RECOVERY_EXHAUSTED audit event
        case_with_audit = get_case_with_audit(actual_id, db_path=self.test_db)
        event_types = [e["event_type"] for e in case_with_audit["audit"]]
        self.assertIn("RECOVERY_EXHAUSTED", event_types)

    def test_02_two_ignored_links_plus_48h_timeout_exhaust_case(self):
        """Test 2: Two ignored recovery links exceeding 48h timeout trigger exhaustion."""
        case_dict = {
            "id": "case_ignored_links_02",
            "amount": 2500.0,
            "currency": "INR",
            "prior_recovery_links_count": 2,
            "link_age_hours": 52.0,  # > 48h timeout
            "execution_status": "executed",
            "risk_status": "at_risk",
        }

        is_exhausted, reason, meta = evaluate_case_exhaustion(case_dict, db_path=self.test_db)
        self.assertTrue(is_exhausted)
        self.assertEqual(meta["condition"], "ignored_recovery_links_timeout")
        self.assertEqual(meta["prior_recovery_links_count"], 2)
        self.assertEqual(meta["timeout_hours"], 48)

    def test_03_one_ignored_link_does_not_exhaust(self):
        """Test 3: One ignored recovery link (even past 48h) does NOT prematurely exhaust."""
        case_dict = {
            "id": "case_single_link_03",
            "amount": 2500.0,
            "currency": "INR",
            "prior_recovery_links_count": 1,
            "link_age_hours": 72.0,
            "execution_status": "executed",
            "risk_status": "at_risk",
        }

        is_exhausted, reason, meta = evaluate_case_exhaustion(case_dict, db_path=self.test_db)
        self.assertFalse(is_exhausted)
        self.assertIsNone(reason)

    def test_04_three_attempts_but_payment_already_recovered(self):
        """Test 4: A case in recovered state NEVER transitions to exhausted regardless of attempts."""
        case_id = "case_recovered_immune_04"

        risk_payload = {
            "payment_id": "pay_rec_immune_01",
            "order_id": "order_immune_004",
            "amount": 850.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }
        case_dict, _ = create_or_get_recovery_case(risk_payload, db_path=self.test_db)
        actual_id = case_dict["id"]

        # Reconcile recovery
        reconcile_recovery_payment(
            case_id_or_link_id=actual_id,
            recovered_payment_id="pay_rec_success_01",
            recovered_amount=850.0,
            db_path=self.test_db,
        )

        rec_case = get_case_by_id(actual_id, db_path=self.test_db)
        self.assertEqual(rec_case["execution_status"], "recovered")

        # Simulate 3 failed attempts in attempts history
        for i in range(3):
            record_payment_attempt(
                case_id=actual_id,
                payment_id=f"pay_late_fail_{i}",
                status="failed",
                db_path=self.test_db,
            )

        is_exhausted, reason, meta = evaluate_case_exhaustion(rec_case, db_path=self.test_db)
        self.assertFalse(is_exhausted, "Recovered case must never be exhausted!")

    def test_05_gemini_recommends_recovery_after_exhaustion(self):
        """Test 5: Deterministic guardrails strictly override Gemini recommendations on exhausted cases."""
        exhausted_case = {
            "id": "case_gemini_override_05",
            "amount": 3500.0,
            "currency": "INR",
            "execution_status": "exhausted",
            "payment_attempts_count": 4,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card limit exceeded.",
        }

        # Simulated rogue AI recommendation
        raw_ai_recommendation = {
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.99,
            "urgency_score": 5,
            "priority": "HIGH",
            "escalation_recommended": False,
            "contextual_factors_used": ["customer_tenure"],
            "reason": "Customer is highly valued; retry sending payment link immediately.",
            "requires_human_approval": False,
        }

        guarded = enforce_ai_guardrails(raw_ai_recommendation, exhausted_case)
        self.assertEqual(guarded["action"], "NO_ACTION")
        self.assertTrue(guarded["requires_human_approval"])
        self.assertTrue(guarded["escalation_recommended"])
        self.assertIn("Automated recovery retry limit exhausted", guarded["reason"])
        self.assertIn("recovery_exhausted", guarded["contextual_factors_used"])

    def test_06_api_cannot_bypass_exhaustion(self):
        """Test 6: Administrative approve endpoint blocks execution for exhausted cases."""
        case_id = "case_api_block_06"
        risk_payload = {
            "payment_id": "pay_api_block_01",
            "order_id": "order_api_block_006",
            "amount": 500.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }
        case_dict, _ = create_or_get_recovery_case(risk_payload, db_path=self.test_db)
        actual_id = case_dict["id"]

        # Exhaust case
        exhaust_recovery_case(actual_id, reason="3 attempts failed", db_path=self.test_db)

        # Attempt to approve
        res_case, msg = approve_case(actual_id, approver="admin", db_path=self.test_db)
        self.assertIn("Case is in exhausted state", msg)
        self.assertEqual(res_case["execution_status"], "exhausted")

    def test_07_frontend_and_executor_cannot_bypass_exhaustion(self):
        """Test 7: Executor directly rejects execution if case is exhausted."""
        decision = {
            "action": "SEND_PAYMENT_LINK",
            "amount": 1000.0,
            "currency": "INR",
            "risk_case_id": "case_exec_block_07",
            "execution_status": "exhausted",
            "requires_human_approval": False,
        }

        result = execute_recovery_action(decision)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("Automated recovery retry limit exhausted", result["message"])

    def test_08_payment_captured_after_exhaustion(self):
        """Test 8: Legitimate payment captured on an exhausted case reconciles to recovered."""
        case_id = "case_late_pay_08"
        risk_payload = {
            "payment_id": "pay_late_01",
            "order_id": "order_late_008",
            "amount": 1200.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }
        case_dict, _ = create_or_get_recovery_case(risk_payload, db_path=self.test_db)
        actual_id = case_dict["id"]

        # Exhaust case
        exhaust_recovery_case(actual_id, reason="Max attempts reached", db_path=self.test_db)
        exhausted_record = get_case_by_id(actual_id, db_path=self.test_db)
        self.assertEqual(exhausted_record["execution_status"], "exhausted")

        # Customer pays later via valid capture
        reconciled_case, recon_status = reconcile_recovery_payment(
            case_id_or_link_id=actual_id,
            recovered_payment_id="pay_legit_late_08",
            recovered_amount=1200.0,
            db_path=self.test_db,
        )

        self.assertEqual(recon_status, "reconciled")
        self.assertEqual(reconciled_case["execution_status"], "recovered")
        self.assertEqual(reconciled_case["risk_status"], "recovered")
        self.assertEqual(reconciled_case["recovered_amount"], 1200.0)
        self.assertEqual(reconciled_case["recovered_payment_id"], "pay_legit_late_08")

    def test_09_duplicate_capture_after_post_exhaustion_recovery(self):
        """Test 9: Duplicate payment after post-exhaustion recovery is recorded as captured_duplicate without double-counting."""
        case_id = "case_dup_late_09"
        risk_payload = {
            "payment_id": "pay_dup_late_01",
            "order_id": "order_dup_late_009",
            "amount": 750.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }
        case_dict, _ = create_or_get_recovery_case(risk_payload, db_path=self.test_db)
        actual_id = case_dict["id"]

        exhaust_recovery_case(actual_id, reason="Exhausted", db_path=self.test_db)

        # 1st capture
        reconcile_recovery_payment(
            case_id_or_link_id=actual_id,
            recovered_payment_id="pay_capture_1st",
            recovered_amount=750.0,
            db_path=self.test_db,
        )

        # 2nd capture (duplicate)
        case_rec, status_msg = reconcile_recovery_payment(
            case_id_or_link_id=actual_id,
            recovered_payment_id="pay_capture_2nd",
            recovered_amount=750.0,
            db_path=self.test_db,
        )

        self.assertEqual(status_msg, "duplicate_payment_recorded")
        self.assertEqual(case_rec["recovered_amount"], 750.0)  # Unchanged!
        self.assertEqual(case_rec["recovered_payment_id"], "pay_capture_1st")  # Preserved!

    def test_10_late_failed_webhook_after_exhaustion(self):
        """Test 10: Additional failed webhook arriving after exhaustion remains exhausted."""
        order_id = "order_late_fail_010"
        risk_payload = {
            "payment_id": "pay_late_fail_01",
            "order_id": order_id,
            "amount": 900.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }
        case_dict, _ = create_or_get_recovery_case(risk_payload, db_path=self.test_db)
        actual_id = case_dict["id"]

        exhaust_recovery_case(actual_id, reason="Max attempts reached", db_path=self.test_db)

        # Late failed attempt arrives
        late_payload = {
            "payment_id": "pay_late_fail_02",
            "order_id": order_id,
            "amount": 900.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }
        updated_case, is_new = create_or_get_recovery_case(late_payload, db_path=self.test_db)
        self.assertFalse(is_new)
        self.assertEqual(updated_case["execution_status"], "exhausted")

    def test_11_concurrent_failure_events_single_exhaustion(self):
        """Test 11: Multiple failure events arriving near exhaustion produce a consistent single exhaustion state."""
        order_id = "order_concur_011"
        for i in range(4):
            payload = {
                "payment_id": f"pay_concur_{i}",
                "order_id": order_id,
                "amount": 400.0,
                "currency": "INR",
                "payment_status": "failed",
                "risk_status": "at_risk",
            }
            case_dict, _ = create_or_get_recovery_case(payload, db_path=self.test_db)
            is_exhausted, reason, meta = evaluate_case_exhaustion(case_dict, db_path=self.test_db)
            if is_exhausted and case_dict.get("execution_status") != "exhausted":
                exhaust_recovery_case(case_dict["id"], reason=reason, db_path=self.test_db)

        final_case = get_case_by_id(f"case_{order_id}", db_path=self.test_db)
        self.assertEqual(final_case["execution_status"], "exhausted")

    @patch("app.recovery_executor.get_razorpay_client")
    def test_13_exhaustion_emits_correct_cancellation_event_semantics(self, mock_client_getter):
        """Test 13: Link cancellation during exhaustion emits PAYMENT_LINK_CANCELLED_AFTER_EXHAUSTION, not recovery."""
        mock_client = MagicMock()
        mock_client_getter.return_value = mock_client

        case_data = {
            "order_id": "order_exhaust_cancel_013",
            "payment_id": "pay_ex_can_01",
            "amount": 1000.0,
            "currency": "INR",
            "payment_status": "failed",
            "payment_link_id": "plink_to_cancel_on_exhaustion",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.test_db)
        actual_id = case_record["id"]

        # Trigger exhaustion
        exhausted_case, _ = exhaust_recovery_case(
            actual_id,
            reason="Maximum failed payment attempts reached (3/3).",
            db_path=self.test_db,
        )
        self.assertEqual(exhausted_case["execution_status"], "exhausted")

        # Verify Razorpay cancel was called
        mock_client.payment_link.cancel.assert_called_once_with("plink_to_cancel_on_exhaustion")

        # Verify Audit Log Event Types & Content
        case_detail = get_case_with_audit(actual_id, db_path=self.test_db)
        audit_events = case_detail["audit"]
        event_types = [e["event_type"] for e in audit_events]

        self.assertIn("PAYMENT_LINK_CANCELLED_AFTER_EXHAUSTION", event_types)
        self.assertNotIn("PAYMENT_LINK_CANCELLED_AFTER_RECOVERY", event_types)

        # Verify exact event message and metadata
        cancel_event = next(e for e in audit_events if e["event_type"] == "PAYMENT_LINK_CANCELLED_AFTER_EXHAUSTION")
        self.assertIn("because retry limit was exhausted", cancel_event["message"])
        self.assertIn("cancelled_payment_link_id", cancel_event["metadata"])
        self.assertEqual(cancel_event["metadata"]["cancelled_payment_link_id"], "plink_to_cancel_on_exhaustion")
        self.assertNotIn("paid_payment_link_id", cancel_event["metadata"])


if __name__ == "__main__":
    unittest.main()
