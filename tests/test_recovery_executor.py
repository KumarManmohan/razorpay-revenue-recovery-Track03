import unittest
from unittest.mock import MagicMock, patch
from app.recovery_executor import execute_recovery_action, _sanitize_error_message
from app.config import settings

class TestRecoveryExecutor(unittest.TestCase):

    def test_requires_human_approval_blocks_execution(self):
        """Test execution is strictly blocked when requires_human_approval is True."""
        decision = {
            "action": "SEND_PAYMENT_LINK",
            "requires_human_approval": True,
            "risk_case_id": "pay_high_val_123",
            "amount": 75000.0,
            "currency": "INR",
        }
        result = execute_recovery_action(decision)
        self.assertEqual(result["status"], "approval_required")
        self.assertTrue(result["requires_human_approval"])
        self.assertIn("blocked", result["message"].lower())

    def test_unsupported_action_rejected(self):
        """Test actions other than SEND_PAYMENT_LINK are safely rejected in Phase 6."""
        for action in ["SEND_INVOICE", "WAIT", "NO_ACTION", "INVESTIGATE", "UNKNOWN"]:
            decision = {
                "action": action,
                "requires_human_approval": False,
                "risk_case_id": "pay_test_002",
                "amount": 500.0,
                "currency": "INR",
            }
            result = execute_recovery_action(decision)
            self.assertEqual(result["status"], "rejected")
            self.assertIn("not supported", result["message"].lower())

    def test_missing_or_invalid_amount_rejected(self):
        """Test missing, zero, or negative amounts are rejected."""
        for invalid_amount in [None, 0, -100, "invalid"]:
            decision = {
                "action": "SEND_PAYMENT_LINK",
                "requires_human_approval": False,
                "risk_case_id": "pay_test_003",
                "amount": invalid_amount,
                "currency": "INR",
            }
            result = execute_recovery_action(decision)
            self.assertEqual(result["status"], "rejected")
            self.assertIn("amount is required", result["message"].lower())

    def test_invalid_currency_rejected(self):
        """Test invalid currency strings are rejected."""
        decision = {
            "action": "SEND_PAYMENT_LINK",
            "requires_human_approval": False,
            "risk_case_id": "pay_test_004",
            "amount": 500.0,
            "currency": "INVALID_CURRENCY",
        }
        result = execute_recovery_action(decision)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("unsupported", result["message"].lower())

    @patch("app.recovery_executor.get_razorpay_client")
    def test_valid_send_payment_link_execution(self, mock_get_client):
        """Test valid decision correctly invokes Razorpay SDK and returns link details."""
        mock_client = MagicMock()
        mock_client.payment_link.create.return_value = {
            "id": "plink_test_mock_12345",
            "short_url": "https://rzp.io/i/mocktest",
            "status": "created",
            "reference_id": "rec_pay_test_005_abcdef",
            "created_at": 1700000000,
        }
        mock_get_client.return_value = mock_client

        decision = {
            "action": "SEND_PAYMENT_LINK",
            "requires_human_approval": False,
            "risk_case_id": "pay_test_005",
            "amount": 750.0,
            "currency": "INR",
        }

        result = execute_recovery_action(decision)
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["payment_link_id"], "plink_test_mock_12345")
        self.assertEqual(result["payment_link_url"], "https://rzp.io/i/mocktest")
        self.assertEqual(result["amount"], 750.0)
        self.assertEqual(result["currency"], "INR")

        # Verify amount in subunits (paise): ₹750 -> 75000 paise
        called_args = mock_client.payment_link.create.call_args[0][0]
        self.assertEqual(called_args["amount"], 75000)
        self.assertEqual(called_args["currency"], "INR")
        self.assertFalse(called_args["notify"]["email"])
        self.assertFalse(called_args["notify"]["sms"])

    @patch("app.recovery_executor.get_razorpay_client")
    def test_razorpay_api_failure_handled_gracefully(self, mock_get_client):
        """Test API errors do not crash the server and return structured failure."""
        mock_client = MagicMock()
        mock_client.payment_link.create.side_effect = Exception("Razorpay API network timeout")
        mock_get_client.return_value = mock_client

        decision = {
            "action": "SEND_PAYMENT_LINK",
            "requires_human_approval": False,
            "risk_case_id": "pay_test_006",
            "amount": 500.0,
            "currency": "INR",
        }

        result = execute_recovery_action(decision)
        self.assertEqual(result["status"], "failed")
        self.assertIn("Failed to create", result["message"])
        self.assertIn("timeout", result["error"])

    def test_secret_never_leaks_in_error_sanitization(self):
        """Test secret sanitizer replaces any occurrence of the secret."""
        with patch.object(settings, "RAZORPAY_KEY_SECRET", "N4MKq7ibC5O04OiiE8ZGYVKD"):
            dirty_error = "API error auth failed with key N4MKq7ibC5O04OiiE8ZGYVKD on endpoint"
            cleaned = _sanitize_error_message(dirty_error)
            self.assertNotIn("N4MKq7ibC5O04OiiE8ZGYVKD", cleaned)
            self.assertIn("[REDACTED_SECRET]", cleaned)


if __name__ == "__main__":
    unittest.main()
