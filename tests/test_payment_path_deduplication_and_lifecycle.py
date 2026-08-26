import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.revenue_risk import extract_payment_link_id, analyze_payment_failure
from app.database import (
    init_db,
    create_or_get_recovery_case,
    reconcile_recovery_payment,
    get_case_with_audit,
    get_dashboard_stats,
    update_execution_status,
    cancel_open_payment_links_for_case,
)
from app.recovery_executor import cancel_payment_link, execute_recovery_action


class TestPaymentPathDeduplicationAndLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_lifecycle.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    # 1. Existing active Payment Link discovered -> no second link created
    def test_01_existing_link_discovered_no_second_link(self):
        payload = {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test_001",
                        "amount": 58900,
                        "currency": "INR",
                        "status": "failed",
                        "order_id": "order_test_001",
                        "description": "#TTJc1ucMZro9z3",
                    }
                }
            }
        }
        extracted = analyze_payment_failure(payload)
        self.assertEqual(extracted["payment_link_id"], "plink_TTJc1ucMZro9z3")
        self.assertEqual(extracted["original_payment_link_id"], "plink_TTJc1ucMZro9z3")

        case_record, is_new = create_or_get_recovery_case(extracted, db_path=self.db_path)
        self.assertTrue(is_new)
        self.assertEqual(case_record["payment_link_id"], "plink_TTJc1ucMZro9z3")
        self.assertEqual(case_record["original_payment_link_id"], "plink_TTJc1ucMZro9z3")

    # 2. Existing link stored on case -> reused
    def test_02_existing_link_stored_on_case_reused(self):
        case_data = {
            "order_id": "order_test_002",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
            "payment_link_id": "plink_existing_123",
            "payment_link_url": "https://rzp.io/i/plink_existing_123",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)
        self.assertEqual(case_record["payment_link_id"], "plink_existing_123")

        # Second attempt arrives without link info
        attempt_2 = {
            "order_id": "order_test_002",
            "payment_id": "pay_second_attempt",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
        }
        updated_case, is_new = create_or_get_recovery_case(attempt_2, db_path=self.db_path)
        self.assertFalse(is_new)
        self.assertEqual(updated_case["payment_link_id"], "plink_existing_123")

    # 3. Explicit payment_link_id takes priority over description parsing
    def test_03_explicit_link_id_priority(self):
        payment_entity = {
            "id": "pay_test_003",
            "payment_link_id": "plink_explicit_priority",
            "description": "#TTJc1ucMZro9z3",
        }
        link_id = extract_payment_link_id(payment_entity)
        self.assertEqual(link_id, "plink_explicit_priority")

    # 4. Description parsing only works for the validated Razorpay format
    def test_04_description_parsing_validation(self):
        # Valid full format
        self.assertEqual(extract_payment_link_id({"description": "#plink_TTJc1ucMZro9z3"}), "plink_TTJc1ucMZro9z3")
        # Valid 14-character suffix format
        self.assertEqual(extract_payment_link_id({"description": "#TTJc1ucMZro9z3"}), "plink_TTJc1ucMZro9z3")
        # Invalid / arbitrary text - must return None
        self.assertIsNone(extract_payment_link_id({"description": "Payment for shoes #123"}))
        self.assertIsNone(extract_payment_link_id({"description": "Testing"}))
        self.assertIsNone(extract_payment_link_id({"description": "#order_TTJcCYBHmCjzW7"}))

    # 5. No existing link -> new recovery link created
    @patch("app.recovery_executor.get_razorpay_client")
    def test_05_no_existing_link_creates_new_link(self, mock_client_getter):
        mock_client = MagicMock()
        mock_client.payment_link.create.return_value = {
            "id": "plink_new_recovery_999",
            "short_url": "https://rzp.io/rzp/newlink",
            "status": "created",
            "created_at": 1787512000,
        }
        mock_client_getter.return_value = mock_client

        decision = {
            "action": "SEND_PAYMENT_LINK",
            "requires_human_approval": False,
            "risk_case_id": "case_no_existing_link",
            "amount": 589.0,
            "currency": "INR",
        }
        res = execute_recovery_action(decision)
        self.assertEqual(res["status"], "executed")
        self.assertEqual(res["payment_link_id"], "plink_new_recovery_999")

    # 6. Successful recovery cancels remaining open recovery links
    @patch("app.recovery_executor.get_razorpay_client")
    def test_06_successful_recovery_cancels_remaining_open_links(self, mock_client_getter):
        mock_client = MagicMock()
        mock_client.payment_link.cancel.return_value = {"id": "plink_original_to_cancel", "status": "cancelled"}
        mock_client_getter.return_value = mock_client

        case_data = {
            "order_id": "order_test_006",
            "payment_id": "pay_initial_fail",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
            "original_payment_link_id": "plink_original_to_cancel",
            "payment_link_id": "plink_recovery_paid",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)

        # Reconcile via the recovery link
        reconciled, status = reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_rec_success",
            recovered_amount=589.0,
            metadata={"payment_link_id": "plink_recovery_paid"},
            db_path=self.db_path,
        )
        self.assertEqual(status, "reconciled")
        self.assertEqual(reconciled["execution_status"], "recovered")
        
        # Verify cancel was called for plink_original_to_cancel
        mock_client.payment_link.cancel.assert_called_once_with("plink_original_to_cancel")

        # Verify audit trail contains cancellation event
        detail = get_case_with_audit(case_record["id"], db_path=self.db_path)
        event_types = [ev["event_type"] for ev in detail["audit"]]
        self.assertIn("PAYMENT_LINK_CANCELLED_AFTER_RECOVERY", event_types)

    # 7. Paid link is never cancelled
    @patch("app.recovery_executor.get_razorpay_client")
    def test_07_paid_link_never_cancelled(self, mock_client_getter):
        mock_client = MagicMock()
        mock_client_getter.return_value = mock_client

        case_dict = {
            "id": "case_test_007",
            "payment_link_id": "plink_paid_link",
            "original_payment_link_id": "plink_paid_link",
        }
        cancelled = cancel_open_payment_links_for_case(case_dict, paid_link_id="plink_paid_link", db_path=self.db_path)
        self.assertEqual(cancelled, [])
        mock_client.payment_link.cancel.assert_not_called()

    # 8. Cancellation failure does not roll back recovered state
    @patch("app.recovery_executor.get_razorpay_client")
    def test_08_cancellation_failure_does_not_rollback_recovery(self, mock_client_getter):
        mock_client = MagicMock()
        mock_client.payment_link.cancel.side_effect = Exception("Razorpay API 500 error")
        mock_client_getter.return_value = mock_client

        case_data = {
            "order_id": "order_test_008",
            "payment_id": "pay_initial_fail_8",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
            "original_payment_link_id": "plink_error_link",
            "payment_link_id": "plink_paid_8",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)

        reconciled, status = reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_rec_success_8",
            recovered_amount=589.0,
            metadata={"payment_link_id": "plink_paid_8"},
            db_path=self.db_path,
        )
        self.assertEqual(status, "reconciled")
        self.assertEqual(reconciled["execution_status"], "recovered")
        self.assertEqual(reconciled["recovered_amount"], 589.0)

    # 9. Second successful payment is captured_duplicate
    def test_09_second_successful_payment_is_captured_duplicate(self):
        case_data = {
            "order_id": "order_test_009",
            "payment_id": "pay_initial_fail_9",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)

        # First capture
        reconciled, status1 = reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_first_capture",
            recovered_amount=589.0,
            db_path=self.db_path,
        )
        self.assertEqual(status1, "reconciled")

        # Second capture
        case2, status2 = reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_second_capture",
            recovered_amount=589.0,
            db_path=self.db_path,
        )
        self.assertEqual(status2, "duplicate_payment_recorded")

        detail = get_case_with_audit(case_record["id"], db_path=self.db_path)
        attempts = detail["attempts"]
        self.assertEqual(len(attempts), 3)  # 1 fail + 1 captured + 1 captured_duplicate
        self.assertEqual(attempts[2]["status"], "captured_duplicate")
        self.assertEqual(attempts[2]["payment_id"], "pay_second_capture")

    # 10. Recovered revenue remains counted exactly once
    def test_10_recovered_revenue_remains_counted_exactly_once(self):
        case_data = {
            "order_id": "order_test_010",
            "payment_id": "pay_initial_fail_10",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)

        reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_first_capture_10",
            recovered_amount=589.0,
            db_path=self.db_path,
        )

        reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_second_capture_10",
            recovered_amount=589.0,
            db_path=self.db_path,
        )

        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["recovered_revenue"], 589.0)  # NOT 1178.0

    # 11. Duplicate webhook idempotency still works
    def test_11_duplicate_webhook_idempotency(self):
        case_data = {
            "order_id": "order_test_011",
            "payment_id": "pay_initial_fail_11",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)

        _, status1 = reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_capture_11",
            recovered_amount=589.0,
            db_path=self.db_path,
        )
        self.assertEqual(status1, "reconciled")

        # Same payment webhook arrives again
        _, status2 = reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_capture_11",
            recovered_amount=589.0,
            db_path=self.db_path,
        )
        self.assertEqual(status2, "already_reconciled")

    # 12. Multiple payment attempts on same order still map to one recovery case
    def test_12_multiple_payment_attempts_map_to_one_case(self):
        attempt1 = {
            "order_id": "order_multi_attempt",
            "payment_id": "pay_att_1",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
            "error_description": "Bank decline 1",
        }
        case1, is_new1 = create_or_get_recovery_case(attempt1, db_path=self.db_path)
        self.assertTrue(is_new1)

        attempt2 = {
            "order_id": "order_multi_attempt",
            "payment_id": "pay_att_2",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
            "error_description": "Bank decline 2",
        }
        case2, is_new2 = create_or_get_recovery_case(attempt2, db_path=self.db_path)
        self.assertFalse(is_new2)
        self.assertEqual(case1["id"], case2["id"])

        detail = get_case_with_audit(case1["id"], db_path=self.db_path)
        self.assertEqual(len(detail["attempts"]), 2)

    # 13. Fraud/high-value guardrails remain unchanged
    def test_13_fraud_and_high_value_guardrails(self):
        from app.recovery_decision import decide_recovery_action

        # High-value (>= ₹50k)
        high_val_case = {
            "amount": 65000.0,
            "currency": "INR",
            "failure_category": "CARD_LIMIT_EXCEEDED",
        }
        dec_high = decide_recovery_action(high_val_case)
        self.assertTrue(dec_high["requires_human_approval"])

        # Fraud / Stolen card -> NO_ACTION
        fraud_case = {
            "amount": 500.0,
            "currency": "INR",
            "error_description": "Card reported stolen - fraud block",
            "failure_category": "FRAUD_OR_SECURITY",
        }
        dec_fraud = decide_recovery_action(fraud_case)
        self.assertEqual(dec_fraud["action"], "NO_ACTION")


if __name__ == "__main__":
    unittest.main()
