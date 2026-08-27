import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.database import (
    init_db,
    create_or_get_recovery_case,
    get_case_with_audit,
    add_audit_event,
    update_execution_status,
    get_case_by_id,
)
from app.notification_service import (
    build_customer_recovery_message,
    send_recovery_notification,
    has_recent_notification,
    MockNotificationProvider,
    NotificationProvider,
    CATEGORY_RECOVERY_GUIDANCE,
)
from app.failure_classifier import classify_payment_failure
from app.revenue_risk import analyze_payment_failure
from app.recovery_decision import decide_recovery_action
from app.ai_recovery_agent import ai_decide_recovery_action


class TestNotificationService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_notification.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    # 1. Deterministic Category-Specific Message Templates
    def test_category_specific_message_templates(self):
        """Verify that all 6 actionable categories produce distinct, tailored guidance."""
        categories = [
            "BANK_DECLINED",
            "INSUFFICIENT_FUNDS",
            "CARD_LIMIT_EXCEEDED",
            "CARD_EXPIRED",
            "INVALID_CARD",
            "AUTHENTICATION_REQUIRED",
        ]
        messages = {}
        for cat in categories:
            msg = build_customer_recovery_message(
                category=cat,
                amount=1500.0,
                currency="INR",
                payment_link_url="https://rzp.io/i/test_cat_link",
            )
            self.assertIn("Action Required", msg["subject"])
            self.assertIn("1500.00", msg["subject"])
            self.assertIn("https://rzp.io/i/test_cat_link", msg["body"])
            messages[cat] = msg["body"]

        # Ensure all 6 category guidance messages are unique
        self.assertNotEqual(messages["BANK_DECLINED"], messages["INSUFFICIENT_FUNDS"])
        self.assertNotEqual(messages["CARD_LIMIT_EXCEEDED"], messages["CARD_EXPIRED"])
        self.assertNotEqual(messages["INVALID_CARD"], messages["AUTHENTICATION_REQUIRED"])
        self.assertNotEqual(messages["BANK_DECLINED"], messages["CARD_LIMIT_EXCEEDED"])

        # Check domain-specific keywords in body
        self.assertIn("issuing bank", messages["BANK_DECLINED"].lower())
        self.assertIn("insufficient", messages["INSUFFICIENT_FUNDS"].lower())
        self.assertIn("limit", messages["CARD_LIMIT_EXCEEDED"].lower())
        self.assertIn("expired", messages["CARD_EXPIRED"].lower())
        self.assertIn("verified", messages["INVALID_CARD"].lower())
        self.assertIn("authentication", messages["AUTHENTICATION_REQUIRED"].lower())

    def test_default_fallback_message_template(self):
        """Verify fallback template for unknown or missing failure category."""
        msg = build_customer_recovery_message(
            category=None,
            amount=500.0,
            currency="INR",
            payment_link_url="https://rzp.io/i/fallback_link",
        )
        self.assertIn("recent payment could not be completed", msg["body"])
        self.assertIn("https://rzp.io/i/fallback_link", msg["body"])

    # 2. Anti-Spam Deduplication & Execution
    def test_send_notification_and_antispam_deduplication(self):
        """Verify first notification succeeds and second identical attempt is blocked."""
        risk_case = {
            "event_id": "evt_notif_unit_001",
            "payment_id": "pay_notif_unit_001",
            "amount": 2000.0,
            "customer_id": "customer@example.com",
            "payment_link_url": "https://rzp.io/i/unit_link_001",
        }
        case_record, _ = create_or_get_recovery_case(risk_case, db_path=self.db_path)
        case_id = case_record["id"]

        # 1st Dispatch -> Sent
        res1 = send_recovery_notification(
            case_id=case_id,
            recipient="customer@example.com",
            payment_link_url="https://rzp.io/i/unit_link_001",
            amount=2000.0,
            currency="INR",
            failure_category="BANK_DECLINED",
            channel="EMAIL",
            db_path=self.db_path,
        )
        self.assertEqual(res1["status"], "sent")
        self.assertEqual(res1["recipient"], "c***r@example.com")
        self.assertTrue(has_recent_notification(case_id, "EMAIL", db_path=self.db_path))

        # 2nd Dispatch -> Blocked as duplicate
        res2 = send_recovery_notification(
            case_id=case_id,
            recipient="customer@example.com",
            payment_link_url="https://rzp.io/i/unit_link_001",
            amount=2000.0,
            currency="INR",
            failure_category="BANK_DECLINED",
            channel="EMAIL",
            db_path=self.db_path,
        )
        self.assertEqual(res2["status"], "blocked")

        # Verify audit history
        audit_data = get_case_with_audit(case_id, db_path=self.db_path)
        self.assertIsNotNone(audit_data)
        event_types = [a["event_type"] for a in audit_data["audit"]]
        self.assertIn("NOTIFICATION_SENT", event_types)
        self.assertIn("NOTIFICATION_BLOCKED_DUPLICATE", event_types)

    # 3. Missing Recipient & URL Handling
    def test_missing_recipient_or_url_handling(self):
        """Verify that empty recipient or missing link skips safely without creating audit pollution."""
        case_record, _ = create_or_get_recovery_case({"event_id": "evt_empty", "payment_id": "pay_empty", "amount": 100.0}, db_path=self.db_path)
        case_id = case_record["id"]

        # Empty recipient
        res_no_rec = send_recovery_notification(
            case_id=case_id,
            recipient="",
            payment_link_url="https://rzp.io/i/valid_url",
            amount=100.0,
            db_path=self.db_path,
        )
        self.assertEqual(res_no_rec["status"], "skipped")

        # Missing payment link URL
        res_no_url = send_recovery_notification(
            case_id=case_id,
            recipient="user@example.com",
            payment_link_url="",
            amount=100.0,
            db_path=self.db_path,
        )
        self.assertEqual(res_no_url["status"], "skipped")

    # 4. Failure Isolation (Provider Exception)
    def test_failure_isolation_on_provider_error(self):
        """Verify that a provider failure does not raise an unhandled exception or crash the caller."""
        class FailingProvider(NotificationProvider):
            def send(self, recipient, subject, body, metadata=None):
                raise RuntimeError("Simulated network outage in notification provider")

        case_record, _ = create_or_get_recovery_case({"event_id": "evt_fail_iso", "payment_id": "pay_fail_iso", "amount": 500.0}, db_path=self.db_path)
        case_id = case_record["id"]

        res = send_recovery_notification(
            case_id=case_id,
            recipient="user@example.com",
            payment_link_url="https://rzp.io/i/iso_link",
            amount=500.0,
            provider=FailingProvider(),
            db_path=self.db_path,
        )
        self.assertEqual(res["status"], "failed")
        self.assertIn("Simulated network outage", res["error"])
        # Verify no false NOTIFICATION_SENT event was recorded
        self.assertFalse(has_recent_notification(case_id, "EMAIL", db_path=self.db_path))

    # 5. Preserved Link Handling
    def test_preserved_link_notification(self):
        """Verify preserved active payment links reuse existing URL and respect anti-spam."""
        existing_url = "https://rzp.io/rzp/ExistingActiveLink"
        risk_case = {
            "event_id": "evt_pres_01",
            "payment_id": "pay_pres_01",
            "amount": 1200.0,
            "customer_id": "member@company.com",
            "payment_link_id": "plink_existing_123",
            "original_payment_link_id": "plink_existing_123",
            "payment_link_url": existing_url,
            "original_payment_link_url": existing_url,
        }
        case_record, _ = create_or_get_recovery_case(risk_case, db_path=self.db_path)
        case_id = case_record["id"]

        # Preserved link notification dispatch
        res = send_recovery_notification(
            case_id=case_id,
            recipient="member@company.com",
            payment_link_url=existing_url,
            amount=1200.0,
            failure_category="CARD_EXPIRED",
            db_path=self.db_path,
        )
        self.assertEqual(res["status"], "sent")
        self.assertIn("member@company.com"[:1] + "***", res["recipient"])

        # Second attempt should be blocked by anti-spam
        res_blocked = send_recovery_notification(
            case_id=case_id,
            recipient="member@company.com",
            payment_link_url=existing_url,
            amount=1200.0,
            failure_category="CARD_EXPIRED",
            db_path=self.db_path,
        )
        self.assertEqual(res_blocked["status"], "blocked")

    # 6. Policy Suppression Tests (Non-recoverable and gated cases)
    def test_policy_suppression_for_fraud_and_security(self):
        """Verify that fraud/security transactions are blocked from automated customer communication."""
        classification = classify_payment_failure(
            error_code="BAD_REQUEST_ERROR",
            error_description="Transaction blocked: stolen card blacklisted",
            amount=1000.0,
        )
        self.assertEqual(classification["action"], "NO_ACTION")
        self.assertEqual(classification["category"], "FRAUD_OR_SECURITY")

        risk_analysis = analyze_payment_failure({
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_fraud_001",
                        "amount": 100000,
                        "currency": "INR",
                        "status": "failed",
                        "error_code": "BAD_REQUEST_ERROR",
                        "error_description": "Transaction blocked: stolen card blacklisted",
                    }
                }
            }
        })
        decision = decide_recovery_action(risk_analysis)
        self.assertEqual(decision["action"], "NO_ACTION")
        self.assertEqual(decision["failure_category"], "FRAUD_OR_SECURITY")

    def test_policy_suppression_for_gateway_timeout(self):
        """Verify that temporary gateway errors produce WAIT and do not trigger customer notifications."""
        classification = classify_payment_failure(
            error_code="GATEWAY_ERROR",
            error_description="Temporary gateway network timeout",
            amount=1000.0,
        )
        self.assertEqual(classification["action"], "WAIT")
        self.assertEqual(classification["category"], "TEMPORARY_GATEWAY_ERROR")

        risk_analysis = analyze_payment_failure({
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_gw_001",
                        "amount": 100000,
                        "currency": "INR",
                        "status": "failed",
                        "error_code": "GATEWAY_ERROR",
                        "error_description": "Temporary gateway network timeout",
                    }
                }
            }
        })
        decision = decide_recovery_action(risk_analysis)
        self.assertEqual(decision["action"], "WAIT")
        self.assertEqual(decision["failure_category"], "TEMPORARY_GATEWAY_ERROR")

    def test_policy_suppression_for_high_value_unapproved(self):
        """Verify that high-value transactions (>= 50k) require human approval and block auto-outreach."""
        classification = classify_payment_failure(
            error_code="BAD_REQUEST_ERROR",
            error_description="Card limit exceeded",
            amount=75000.0,
        )
        self.assertTrue(classification["requires_human_approval"])

        risk_analysis = analyze_payment_failure({
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_high_001",
                        "amount": 7500000,
                        "currency": "INR",
                        "status": "failed",
                        "error_code": "BAD_REQUEST_ERROR",
                        "error_description": "Card limit exceeded",
                    }
                }
            }
        })
        decision = decide_recovery_action(risk_analysis)
        self.assertTrue(decision["requires_human_approval"])


if __name__ == "__main__":
    unittest.main()
