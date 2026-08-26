import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from app.database import (
    init_db,
    create_or_get_recovery_case,
    update_recovery_decision,
    get_case_by_id,
    get_case_with_audit,
    approve_case,
    reject_case,
)
from app.notification_service import (
    send_recovery_notification,
    has_recent_notification,
    _mask_recipient,
    MockNotificationProvider,
)
from app.recovery_executor import execute_recovery_action


class TestHumanApprovalAndNotificationService(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_p9.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_recipient_masking(self):
        """Test that customer identifiers are masked for privacy."""
        self.assertEqual(_mask_recipient("alice@example.com"), "a***e@example.com")
        self.assertEqual(_mask_recipient("+919876543210"), "+9******10")
        self.assertEqual(_mask_recipient(""), "unknown")

    def test_human_approval_workflow_and_audit(self):
        """Test approving a gated recovery case records audit and transitions status."""
        risk_case = {
            "event_id": "evt_app_001",
            "payment_id": "pay_app_001",
            "amount": 75000.0,
            "currency": "INR",
            "payment_status": "failed",
        }
        case_record, _ = create_or_get_recovery_case(risk_case, db_path=self.db_path)
        case_id = case_record["id"]

        decision = {
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.85,
            "reason": "High-value failure requiring human review.",
            "requires_human_approval": True,
        }
        update_recovery_decision(case_id, decision, db_path=self.db_path)

        # Before approval
        case_before = get_case_by_id(case_id, db_path=self.db_path)
        self.assertEqual(case_before["execution_status"], "approval_required")
        self.assertEqual(case_before["requires_human_approval"], 1)

        # Grant human approval
        updated_case, msg = approve_case(
            case_id,
            approver="finance_manager_bob",
            notes="Customer KYC confirmed.",
            db_path=self.db_path,
        )
        self.assertEqual(updated_case["execution_status"], "approved")
        self.assertEqual(updated_case["requires_human_approval"], 0)

        # Verify audit history
        audit_data = get_case_with_audit(case_id, db_path=self.db_path)
        audit_events = [a["event_type"] for a in audit_data["audit"]]
        self.assertIn("HUMAN_APPROVAL_GRANTED", audit_events)

    def test_human_rejection_workflow(self):
        """Test rejecting a case marks execution_status as rejected and logs audit."""
        risk_case = {
            "event_id": "evt_rej_002",
            "payment_id": "pay_rej_002",
            "amount": 1000.0,
        }
        case_record, _ = create_or_get_recovery_case(risk_case, db_path=self.db_path)
        case_id = case_record["id"]

        updated_case, msg = reject_case(
            case_id,
            approver="fraud_officer_carol",
            reason="Suspected abusive retry pattern.",
            db_path=self.db_path,
        )
        self.assertEqual(updated_case["execution_status"], "rejected")

        audit_data = get_case_with_audit(case_id, db_path=self.db_path)
        audit_events = [a["event_type"] for a in audit_data["audit"]]
        self.assertIn("HUMAN_APPROVAL_REJECTED", audit_events)

    def test_cannot_approve_nonexistent_or_already_executed_case(self):
        """Test edge cases for approval."""
        res, msg = approve_case("case_non_existent", db_path=self.db_path)
        self.assertIsNone(res)
        self.assertEqual(msg, "Case not found.")

        # Create executed case
        risk_case = {"event_id": "evt_exec_003", "payment_id": "pay_exec_003", "amount": 500.0}
        case_record, _ = create_or_get_recovery_case(risk_case, db_path=self.db_path)
        from app.database import update_execution_status
        update_execution_status(case_record["id"], {"status": "executed"}, db_path=self.db_path)

        res2, msg2 = approve_case(case_record["id"], db_path=self.db_path)
        self.assertEqual(msg2, "Case is already executed.")

    def test_notification_service_and_antispam_duplicate_protection(self):
        """Test sending a mock notification and verifying anti-spam duplicate protection."""
        risk_case = {
            "event_id": "evt_notif_004",
            "payment_id": "pay_notif_004",
            "amount": 850.0,
        }
        case_record, _ = create_or_get_recovery_case(risk_case, db_path=self.db_path)
        case_id = case_record["id"]

        # First Notification Dispatch -> Should Succeed
        result1 = send_recovery_notification(
            case_id=case_id,
            recipient="testuser@example.com",
            payment_link_url="https://rzp.io/i/testlink004",
            amount=850.0,
            currency="INR",
            channel="EMAIL",
            db_path=self.db_path,
        )
        self.assertEqual(result1["status"], "sent")
        self.assertEqual(result1["recipient"], "t***r@example.com")
        self.assertTrue(has_recent_notification(case_id, "EMAIL", db_path=self.db_path))

        # Second Notification Dispatch -> Should be Blocked by Anti-Spam
        result2 = send_recovery_notification(
            case_id=case_id,
            recipient="testuser@example.com",
            payment_link_url="https://rzp.io/i/testlink004",
            amount=850.0,
            currency="INR",
            channel="EMAIL",
            db_path=self.db_path,
        )
        self.assertEqual(result2["status"], "blocked")
        self.assertIn("anti-spam", result2["reason"].lower())

        # Verify audit records for both events
        audit_data = get_case_with_audit(case_id, db_path=self.db_path)
        audit_types = [a["event_type"] for a in audit_data["audit"]]
        self.assertIn("NOTIFICATION_SENT", audit_types)
        self.assertIn("NOTIFICATION_BLOCKED_DUPLICATE", audit_types)


if __name__ == "__main__":
    unittest.main()
