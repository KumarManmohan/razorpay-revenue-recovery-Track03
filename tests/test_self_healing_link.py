import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
import sqlite3

from app.main import get_single_recovery_case
from app.database import (
    init_db,
    create_or_get_recovery_case,
    get_case_by_id,
    update_case_payment_link_url,
)

class TestSelfHealingPaymentLinkUrl(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        init_db(self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    @patch("app.main.get_case_with_audit")
    @patch("app.main.fetch_payment_link_url")
    @patch("app.main.update_case_payment_link_url")
    def test_case_a_missing_url_resolves_and_persists(self, mock_update, mock_fetch, mock_get_case):
        # Case A: payment_link_id exists, payment_link_url missing -> resolves & persists
        mock_get_case.return_value = {
            "case": {
                "id": "case_test_heal_001",
                "payment_link_id": "plink_test_123",
                "payment_link_url": None,
                "execution_status": "executed",
                "amount": 1000.0,
                "risk_status": "at_risk",
            },
            "audit": [],
            "attempts": [],
        }
        mock_fetch.return_value = "https://rzp.io/rzp/healed123"

        data = get_single_recovery_case("case_test_heal_001")
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["case"]["payment_link_url"], "https://rzp.io/rzp/healed123")
        self.assertEqual(data["case"]["execution_status"], "executed")
        self.assertEqual(data["case"]["amount"], 1000.0)

        mock_fetch.assert_called_once_with("plink_test_123")
        mock_update.assert_called_once_with("case_test_heal_001", "https://rzp.io/rzp/healed123")

    @patch("app.main.get_case_with_audit")
    @patch("app.main.fetch_payment_link_url")
    @patch("app.main.update_case_payment_link_url")
    def test_case_b_existing_url_no_fetch(self, mock_update, mock_fetch, mock_get_case):
        # Case B: payment_link_id exists, payment_link_url already present -> no fetch
        mock_get_case.return_value = {
            "case": {
                "id": "case_test_heal_002",
                "payment_link_id": "plink_test_456",
                "payment_link_url": "https://rzp.io/rzp/already_present",
                "execution_status": "executed",
                "amount": 500.0,
            },
            "audit": [],
            "attempts": [],
        }

        data = get_single_recovery_case("case_test_heal_002")
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["case"]["payment_link_url"], "https://rzp.io/rzp/already_present")
        mock_fetch.assert_not_called()
        mock_update.assert_not_called()

    @patch("app.main.get_case_with_audit")
    @patch("app.main.fetch_payment_link_url")
    @patch("app.main.update_case_payment_link_url")
    def test_case_c_fetch_failure_safe_fallback(self, mock_update, mock_fetch, mock_get_case):
        # Case C: payment_link_id exists, payment_link_url missing, fetch fails (None) -> returns null safely
        mock_get_case.return_value = {
            "case": {
                "id": "case_test_heal_003",
                "payment_link_id": "plink_test_789",
                "payment_link_url": None,
                "execution_status": "executed",
                "amount": 750.0,
            },
            "audit": [],
            "attempts": [],
        }
        mock_fetch.return_value = None

        data = get_single_recovery_case("case_test_heal_003")
        self.assertEqual(data["status"], "success")
        self.assertIsNone(data["case"]["payment_link_url"])
        self.assertEqual(data["case"]["execution_status"], "executed")
        mock_fetch.assert_called_once_with("plink_test_789")
        mock_update.assert_not_called()

    def test_update_case_payment_link_url_preserves_other_fields(self):
        # Verify database helper strictly updates ONLY payment_link_url
        from app.database import update_recovery_decision
        case_payload = {
            "payment_id": "pay_test_heal_99",
            "order_id": "order_test_heal_99",
            "amount": 1234.0,
            "currency": "INR",
            "risk_status": "at_risk",
        }
        case_rec, _ = create_or_get_recovery_case(case_payload, db_path=self.db_path)
        case_id = case_rec["id"]
        update_recovery_decision(case_id, {
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.95,
            "reason": "Test decision reason",
            "decision_source": "ai_agent",
            "requires_human_approval": False,
        }, db_path=self.db_path)

        updated = update_case_payment_link_url(case_id, "https://rzp.io/rzp/verified_url", db_path=self.db_path)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["payment_link_url"], "https://rzp.io/rzp/verified_url")
        self.assertEqual(updated["amount"], 1234.0)
        self.assertEqual(updated["execution_status"], "pending")
        self.assertEqual(updated["risk_status"], "at_risk")
        self.assertEqual(updated["decision_action"], "SEND_PAYMENT_LINK")
        self.assertEqual(updated["decision_reason"], "Test decision reason")

if __name__ == "__main__":
    unittest.main()
