import json
import os
import tempfile
import unittest
from unittest.mock import patch
from app.database import (
    init_db,
    create_or_get_recovery_case,
    update_recovery_decision,
    update_execution_status,
    reconcile_recovery_payment,
    get_case_by_id,
    get_case_with_audit,
    get_dashboard_stats,
)
from app.razorpay_client import verify_webhook_signature
from app.revenue_risk import analyze_payment_failure


class TestRecoveryReconciliation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_reconciliation.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_successful_recovery_payment_reconciliation(self):
        """Test reconciling a successful payment with a recovery case via server-side lookup."""
        # 1. Create a failed payment case and mark it executed with a payment link
        failed_case = {
            "event_id": "evt_recon_001",
            "payment_id": "pay_failed_recon_001",
            "amount": 1250.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }
        case_record, _ = create_or_get_recovery_case(failed_case, db_path=self.db_path)
        case_id = case_record["id"]

        update_execution_status(
            case_id,
            {
                "status": "executed",
                "payment_link_id": "plink_recon_test_001",
                "payment_link_url": "https://rzp.io/i/recon001",
            },
            db_path=self.db_path,
        )

        # 2. Trigger Reconciliation for the payment link payment
        reconciled_case, status = reconcile_recovery_payment(
            case_id_or_link_id="plink_recon_test_001",
            recovered_payment_id="pay_captured_recon_999",
            recovered_amount=1250.0,
            metadata={"event_name": "payment.captured"},
            db_path=self.db_path,
        )

        self.assertEqual(status, "reconciled")
        self.assertIsNotNone(reconciled_case)
        self.assertEqual(reconciled_case["execution_status"], "recovered")
        self.assertEqual(reconciled_case["recovered_amount"], 1250.0)
        self.assertEqual(reconciled_case["recovered_payment_id"], "pay_captured_recon_999")
        self.assertIsNotNone(reconciled_case["recovered_at"])

        # 3. Verify append-only audit trail
        audit_data = get_case_with_audit(case_id, db_path=self.db_path)
        audit_events = [a["event_type"] for a in audit_data["audit"]]
        self.assertIn("RECOVERY_PAYMENT_DETECTED", audit_events)
        self.assertIn("RECOVERY_CASE_RECONCILED", audit_events)
        self.assertIn("REVENUE_RECOVERED", audit_events)

    def test_reconciliation_by_case_id(self):
        """Test reconciliation matching directly on risk_case_id from webhook notes."""
        failed_case = {
            "event_id": "evt_recon_002",
            "payment_id": "pay_failed_recon_002",
            "amount": 3400.0,
            "currency": "INR",
        }
        case_record, _ = create_or_get_recovery_case(failed_case, db_path=self.db_path)
        case_id = case_record["id"]

        reconciled_case, status = reconcile_recovery_payment(
            case_id_or_link_id=case_id,
            recovered_payment_id="pay_captured_002",
            recovered_amount=3400.0,
            db_path=self.db_path,
        )
        self.assertEqual(status, "reconciled")
        self.assertEqual(reconciled_case["execution_status"], "recovered")

    def test_unmatched_payment_reconciliation(self):
        """Test that unknown/unmatched payment links or cases return not found."""
        reconciled_case, status = reconcile_recovery_payment(
            case_id_or_link_id="non_existent_case_or_link",
            recovered_payment_id="pay_unknown_003",
            recovered_amount=500.0,
            db_path=self.db_path,
        )
        self.assertIsNone(reconciled_case)
        self.assertEqual(status, "Case not found.")

    def test_idempotent_duplicate_reconciliation(self):
        """Test that duplicate webhook deliveries do not re-apply or duplicate revenue."""
        failed_case = {
            "event_id": "evt_recon_004",
            "payment_id": "pay_failed_recon_004",
            "amount": 5000.0,
        }
        case_record, _ = create_or_get_recovery_case(failed_case, db_path=self.db_path)
        case_id = case_record["id"]

        # First reconciliation
        c1, status1 = reconcile_recovery_payment(
            case_id_or_link_id=case_id,
            recovered_payment_id="pay_captured_004",
            recovered_amount=5000.0,
            db_path=self.db_path,
        )
        self.assertEqual(status1, "reconciled")

        # Duplicate delivery with the same payment ID
        c2, status2 = reconcile_recovery_payment(
            case_id_or_link_id=case_id,
            recovered_payment_id="pay_captured_004",
            recovered_amount=5000.0,
            db_path=self.db_path,
        )
        self.assertEqual(status2, "already_reconciled")

        # Verify audit count is not duplicated
        audit_data = get_case_with_audit(case_id, db_path=self.db_path)
        recon_events = [a for a in audit_data["audit"] if a["event_type"] == "REVENUE_RECOVERED"]
        self.assertEqual(len(recon_events), 1)

    def test_dashboard_stats_after_reconciliation(self):
        """Test that KPI metrics accurately reflect recovered revenue after reconciliation."""
        # Create at-risk case of ₹10,000
        case1, _ = create_or_get_recovery_case({
            "event_id": "evt_stat_p11_01",
            "payment_id": "pay_stat_p11_01",
            "amount": 10000.0,
            "risk_status": "at_risk",
        }, db_path=self.db_path)

        # Create another at-risk case of ₹10,000
        case2, _ = create_or_get_recovery_case({
            "event_id": "evt_stat_p11_02",
            "payment_id": "pay_stat_p11_02",
            "amount": 10000.0,
            "risk_status": "at_risk",
        }, db_path=self.db_path)

        # Before reconciliation
        stats_before = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats_before["total_revenue_at_risk"], 20000.0)
        self.assertEqual(stats_before["recovered_revenue"], 0.0)
        self.assertEqual(stats_before["recovery_rate_percentage"], 0.0)

        # Reconcile Case 1
        reconcile_recovery_payment(
            case_id_or_link_id=case1["id"],
            recovered_payment_id="pay_cap_stat_01",
            recovered_amount=10000.0,
            db_path=self.db_path,
        )

        # After reconciliation
        stats_after = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats_after["total_revenue_at_risk"], 10000.0)  # Decreased by ₹10,000 to ₹10,000 (Case 2)
        self.assertEqual(stats_after["recovered_revenue"], 10000.0)
        self.assertEqual(stats_after["historical_exposure"], 20000.0)
        self.assertEqual(stats_after["recovery_rate_percentage"], 50.0)

    def test_existing_payment_failed_webhook_regression(self):
        """Verify payment.failed webhook processing remains completely operational."""
        failed_payload = {
            "event": "payment.failed",
            "id": "evt_regression_p11",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_reg_p11_001",
                        "amount": 150000,  # 1500 INR in paise
                        "currency": "INR",
                        "status": "failed",
                        "error_description": "Card expired",
                    }
                }
            },
        }
        analyzed = analyze_payment_failure(failed_payload)
        self.assertEqual(analyzed["amount"], 1500.0)
        self.assertEqual(analyzed["risk_status"], "at_risk")
        self.assertEqual(analyzed["error_description"], "Card expired")


if __name__ == "__main__":
    unittest.main()
