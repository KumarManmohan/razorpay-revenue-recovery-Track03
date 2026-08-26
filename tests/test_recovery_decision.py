import unittest
from app.revenue_risk import analyze_payment_failure
from app.recovery_decision import decide_recovery_action, ALLOWED_ACTIONS

class TestRecoveryDecisionEngine(unittest.TestCase):

    def test_recurring_payment_failure(self):
        """Test recurring subscription failure recommends SEND_PAYMENT_LINK."""
        risk_case = {
            "payment_id": "pay_sub_001",
            "amount": 999.0,
            "currency": "INR",
            "payment_status": "failed",
            "is_recurring_revenue": True,
            "risk_status": "at_risk",
            "error_description": "Card limit exceeded",
        }
        decision = decide_recovery_action(risk_case)
        self.assertIn(decision["action"], ALLOWED_ACTIONS)
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")
        self.assertGreaterEqual(decision["confidence"], 0.80)
        self.assertFalse(decision["requires_human_approval"])
        self.assertIn("recurring", decision["reason"].lower())

    def test_non_recurring_payment_failure(self):
        """Test one-time failed payment recommends SEND_PAYMENT_LINK."""
        risk_case = {
            "payment_id": "pay_onetime_002",
            "amount": 499.0,
            "currency": "INR",
            "payment_status": "failed",
            "is_recurring_revenue": False,
            "risk_status": "at_risk",
            "error_description": "Payment was dropped by user",
        }
        decision = decide_recovery_action(risk_case)
        self.assertIn(decision["action"], ALLOWED_ACTIONS)
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")
        self.assertFalse(decision["requires_human_approval"])

    def test_missing_failure_information(self):
        """Test risk case with missing amount or indeterminate risk triggers INVESTIGATE."""
        risk_case = {
            "payment_id": "pay_missing_003",
            "amount": None,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "needs_investigation",
        }
        decision = decide_recovery_action(risk_case)
        self.assertEqual(decision["action"], "INVESTIGATE")
        self.assertTrue(decision["requires_human_approval"])

    def test_fraud_or_security_failure_reason(self):
        """Test fraud / blocked card halts recovery with NO_ACTION."""
        risk_case = {
            "payment_id": "pay_fraud_004",
            "amount": 2500.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
            "error_code": "CARD_BLOCKED",
            "error_description": "Stolen card reported or fraud alert",
        }
        decision = decide_recovery_action(risk_case)
        self.assertEqual(decision["action"], "NO_ACTION")
        self.assertTrue(decision["requires_human_approval"])
        self.assertIn("fraud", decision["reason"].lower())

    def test_invalid_or_empty_input(self):
        """Test empty/invalid dictionary input triggers INVESTIGATE."""
        decision_empty = decide_recovery_action({})
        self.assertEqual(decision_empty["action"], "INVESTIGATE")
        self.assertTrue(decision_empty["requires_human_approval"])

        decision_none = decide_recovery_action(None)
        self.assertEqual(decision_none["action"], "INVESTIGATE")
        self.assertTrue(decision_none["requires_human_approval"])

    def test_high_value_transaction_policy_guardrail(self):
        """Test high-value transaction (>= ₹50,000) mandates human approval."""
        risk_case = {
            "payment_id": "pay_high_val_005",
            "amount": 75000.0,
            "currency": "INR",
            "payment_status": "failed",
            "is_recurring_revenue": False,
            "risk_status": "at_risk",
            "error_description": "Card limit exceeded",
        }
        decision = decide_recovery_action(risk_case)
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")
        self.assertTrue(decision["requires_human_approval"])


    def test_non_failed_payment(self):
        """Test non-failed payment (captured/authorized) results in NO_ACTION."""
        risk_case = {
            "payment_id": "pay_success_006",
            "amount": 500.0,
            "currency": "INR",
            "payment_status": "captured",
            "risk_status": "not_at_risk",
        }
        decision = decide_recovery_action(risk_case)
        self.assertEqual(decision["action"], "NO_ACTION")
        self.assertFalse(decision["requires_human_approval"])


if __name__ == "__main__":
    unittest.main()
