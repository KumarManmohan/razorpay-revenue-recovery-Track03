import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.revenue_risk import extract_payment_link_id
from app.database import (
    init_db,
    create_or_get_recovery_case,
    reconcile_recovery_payment,
    get_case_with_audit,
    get_dashboard_stats,
    update_execution_status,
    cancel_open_payment_links_for_case,
)


class TestBidirectionalPaymentLinkCancellation(unittest.TestCase):
    """
    Milestone 14D — Bidirectional Payment-Link Cancellation Test Suite
    Tests both directions of Payment Link payment/cancellation:
    - Recovery Link Paid -> Original Link Cancelled
    - Original Link Paid -> Recovery Link Cancelled
    Plus explicit IDs, description extraction, fallback, failure resilience, and duplicate protection.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_bidirectional.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    # Test A — Recovery Link Paid
    @patch("app.recovery_executor.cancel_payment_link")
    def test_a_recovery_link_paid(self, mock_cancel):
        """When Recovery Link is paid, Original Link is cancelled and Recovery Link is spared."""
        mock_cancel.return_value = {"status": "cancelled", "payment_link_id": "plink_orig_123"}

        case_data = {
            "order_id": "order_test_a",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
            "original_payment_link_id": "plink_orig_123",
            "payment_link_id": "plink_recovery_456",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)

        reconciled_case, status = reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_recov_capture_a",
            recovered_amount=589.0,
            metadata={
                "payment_link_id": "plink_recovery_456",
                "order_id": "order_test_a",
            },
            db_path=self.db_path,
        )

        self.assertEqual(status, "reconciled")
        self.assertEqual(reconciled_case["execution_status"], "recovered")
        self.assertEqual(reconciled_case["recovered_amount"], 589.0)

        # Verify only the original link was cancelled
        mock_cancel.assert_called_once_with("plink_orig_123")

        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["recovered_revenue"], 589.0)
        self.assertEqual(stats["total_revenue_at_risk"], 0.0)

    # Test B — Original Link Paid
    @patch("app.recovery_executor.cancel_payment_link")
    def test_b_original_link_paid(self, mock_cancel):
        """When Original Link is paid, Recovery Link is cancelled and Original Link is spared."""
        mock_cancel.return_value = {"status": "cancelled", "payment_link_id": "plink_recovery_456"}

        case_data = {
            "order_id": "order_test_b",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
            "original_payment_link_id": "plink_orig_123",
            "payment_link_id": "plink_recovery_456",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)

        reconciled_case, status = reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_orig_capture_b",
            recovered_amount=589.0,
            metadata={
                "payment_link_id": "plink_orig_123",
                "order_id": "order_test_b",
            },
            db_path=self.db_path,
        )

        self.assertEqual(status, "reconciled")
        self.assertEqual(reconciled_case["execution_status"], "recovered")
        self.assertEqual(reconciled_case["recovered_amount"], 589.0)

        # Verify only the recovery link was cancelled
        mock_cancel.assert_called_once_with("plink_recovery_456")

        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["recovered_revenue"], 589.0)
        self.assertEqual(stats["total_revenue_at_risk"], 0.0)

    # Test C — Explicit Payment Link ID
    def test_c_explicit_payment_link_id(self):
        """Webhook with explicit payment_link_id is parsed accurately."""
        payment_entity = {
            "id": "pay_test_c",
            "payment_link_id": "plink_explicit_789",
            "description": "#other_link_ignored",
        }
        extracted = extract_payment_link_id(payment_entity)
        self.assertEqual(extracted, "plink_explicit_789")

    # Test D — Description Extraction
    def test_d_description_extraction(self):
        """Webhook with description '#TTgJGcGruj9C8H' correctly extracts 'plink_TTgJGcGruj9C8H'."""
        payment_entity = {
            "id": "pay_test_d",
            "description": "#TTgJGcGruj9C8H",
        }
        extracted = extract_payment_link_id(payment_entity)
        self.assertEqual(extracted, "plink_TTgJGcGruj9C8H")

        # Full format
        payment_entity_full = {
            "id": "pay_test_d2",
            "description": "#plink_TTgJGcGruj9C8H",
        }
        extracted_full = extract_payment_link_id(payment_entity_full)
        self.assertEqual(extracted_full, "plink_TTgJGcGruj9C8H")

    # Test E — No Link Identifier
    @patch("app.recovery_executor.cancel_payment_link")
    def test_e_no_link_identifier(self, mock_cancel):
        """When webhook has no link identifier, reconciliation succeeds without guessing paid link."""
        mock_cancel.return_value = {"status": "cancelled", "payment_link_id": "plink_recov_e"}

        case_data = {
            "order_id": "order_test_e",
            "amount": 1000.0,
            "currency": "INR",
            "payment_status": "failed",
            "original_payment_link_id": "plink_orig_e",
            "payment_link_id": "plink_recov_e",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)

        # Metadata has NO payment_link_id (paid_link_id is None)
        reconciled_case, status = reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_direct_capture_e",
            recovered_amount=1000.0,
            metadata={
                "order_id": "order_test_e",
            },
            db_path=self.db_path,
        )

        self.assertEqual(status, "reconciled")
        self.assertEqual(reconciled_case["execution_status"], "recovered")
        self.assertEqual(reconciled_case["recovered_amount"], 1000.0)

        # Both candidates are attempted for cancellation without crash or rollback
        self.assertEqual(mock_cancel.call_count, 2)

    # Test F — Paid Link Must Never Be Cancelled
    @patch("app.recovery_executor.cancel_payment_link")
    def test_f_paid_link_never_cancelled(self, mock_cancel):
        """Paid link is strictly excluded from cancellation calls."""
        mock_cancel.return_value = {"status": "cancelled", "payment_link_id": "plink_open_f"}

        case_data = {
            "order_id": "order_test_f",
            "amount": 250.0,
            "currency": "INR",
            "payment_status": "failed",
            "original_payment_link_id": "plink_paid_f",
            "payment_link_id": "plink_open_f",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)

        reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_f_capture",
            recovered_amount=250.0,
            metadata={"payment_link_id": "plink_paid_f"},
            db_path=self.db_path,
        )

        # Ensure plink_paid_f was NEVER sent to cancel_payment_link
        for call_args in mock_cancel.call_args_list:
            self.assertNotEqual(call_args[0][0], "plink_paid_f")
        mock_cancel.assert_called_once_with("plink_open_f")

    # Test G — Cancellation Failure
    @patch("app.recovery_executor.cancel_payment_link")
    def test_g_cancellation_failure_resilience(self, mock_cancel):
        """Cancellation API failure does NOT roll back recovery or corrupt case state."""
        mock_cancel.return_value = {
            "status": "failed",
            "payment_link_id": "plink_orig_g",
            "error": "Razorpay API 500 Network Timeout",
        }

        case_data = {
            "order_id": "order_test_g",
            "amount": 500.0,
            "currency": "INR",
            "payment_status": "failed",
            "original_payment_link_id": "plink_orig_g",
            "payment_link_id": "plink_recov_g",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)

        reconciled_case, status = reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_recov_g",
            recovered_amount=500.0,
            metadata={"payment_link_id": "plink_recov_g"},
            db_path=self.db_path,
        )

        # Recovery status is still successfully reconciled
        self.assertEqual(status, "reconciled")
        self.assertEqual(reconciled_case["execution_status"], "recovered")
        self.assertEqual(reconciled_case["recovered_amount"], 500.0)

        # Audit events record the cancellation failure without raising exception
        detail = get_case_with_audit(case_record["id"], db_path=self.db_path)
        event_types = [e["event_type"] for e in detail["audit"]]
        self.assertIn("PAYMENT_LINK_CANCELLATION_SKIPPED", event_types)

    # Test H — Duplicate Payment
    @patch("app.recovery_executor.cancel_payment_link")
    def test_h_duplicate_payment_protection(self, mock_cancel):
        """Second capture on recovered case records duplicate attempt without inflating revenue."""
        mock_cancel.return_value = {"status": "cancelled", "payment_link_id": "plink_orig_h"}

        case_data = {
            "order_id": "order_test_h",
            "amount": 300.0,
            "currency": "INR",
            "payment_status": "failed",
            "original_payment_link_id": "plink_orig_h",
            "payment_link_id": "plink_recov_h",
        }
        case_record, _ = create_or_get_recovery_case(case_data, db_path=self.db_path)

        # First capture
        reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_first_h",
            recovered_amount=300.0,
            metadata={"payment_link_id": "plink_recov_h"},
            db_path=self.db_path,
        )

        # Second capture
        _, status2 = reconcile_recovery_payment(
            case_id_or_link_id=case_record["id"],
            recovered_payment_id="pay_second_h",
            recovered_amount=300.0,
            metadata={"payment_link_id": "plink_orig_h"},
            db_path=self.db_path,
        )
        self.assertEqual(status2, "duplicate_payment_recorded")

        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["recovered_revenue"], 300.0)  # Revenue NOT doubled to 600

        detail = get_case_with_audit(case_record["id"], db_path=self.db_path)
        event_types = [e["event_type"] for e in detail["audit"]]
        self.assertIn("DUPLICATE_PAYMENT_DETECTED", event_types)

    # Test I — Multiple Candidate Links
    @patch("app.recovery_executor.cancel_payment_link")
    def test_i_multiple_candidate_links(self, mock_cancel):
        """Case with multiple candidate links cancels all eligible open links individually."""
        mock_cancel.side_effect = lambda link_id: {"status": "cancelled", "payment_link_id": link_id}

        case_dict = {
            "id": "case_test_i",
            "original_payment_link_id": "plink_orig_i",
            "payment_link_id": "plink_recov_i",
            "cancelled_payment_links": None,
        }

        # Suppose plink_recov_i is the paid link
        cancelled = cancel_open_payment_links_for_case(
            case_dict=case_dict,
            paid_link_id="plink_recov_i",
            db_path=self.db_path,
        )

        self.assertEqual(cancelled, ["plink_orig_i"])
        mock_cancel.assert_called_once_with("plink_orig_i")


if __name__ == "__main__":
    unittest.main()
