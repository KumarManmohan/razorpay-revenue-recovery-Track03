import unittest
from unittest.mock import patch
from app.failure_classifier import (
    classify_payment_failure,
    CATEGORY_INSUFFICIENT_FUNDS,
    CATEGORY_CARD_LIMIT_EXCEEDED,
    CATEGORY_CARD_EXPIRED,
    CATEGORY_INVALID_CARD,
    CATEGORY_AUTHENTICATION_REQUIRED,
    CATEGORY_BANK_DECLINED,
    CATEGORY_TEMPORARY_GATEWAY_ERROR,
    CATEGORY_FRAUD_OR_SECURITY,
    CATEGORY_UNKNOWN,
)
from app.recovery_decision import decide_recovery_action
from app.ai_recovery_agent import ai_decide_recovery_action


class TestFailureClassificationAndStrategies(unittest.TestCase):

    def test_insufficient_funds_classification(self):
        """Test insufficient funds produces INSUFFICIENT_FUNDS category and balance-specific rationale."""
        res = classify_payment_failure(
            error_description="Insufficient funds on credit card",
            is_recurring=False,
            amount=1500.0,
        )
        self.assertEqual(res["category"], CATEGORY_INSUFFICIENT_FUNDS)
        self.assertEqual(res["action"], "SEND_PAYMENT_LINK")
        self.assertIn("insufficient", res["reason"].lower())
        self.assertNotIn("limit exceeded", res["reason"].lower())
        self.assertNotIn("expired", res["reason"].lower())

    def test_card_limit_exceeded_classification(self):
        """Test card limit exceeded produces CARD_LIMIT_EXCEEDED and limit-specific rationale."""
        res = classify_payment_failure(
            error_description="Card limit exceeded",
            is_recurring=True,
            amount=8000.0,
        )
        self.assertEqual(res["category"], CATEGORY_CARD_LIMIT_EXCEEDED)
        self.assertEqual(res["action"], "SEND_PAYMENT_LINK")
        self.assertIn("limit", res["reason"].lower())
        self.assertNotIn("insufficient funds", res["reason"].lower())

    def test_card_expired_classification(self):
        """Test card expired produces CARD_EXPIRED and expiration-specific rationale."""
        res = classify_payment_failure(
            error_description="Card expired on 08/26",
            is_recurring=True,
            amount=2000.0,
        )
        self.assertEqual(res["category"], CATEGORY_CARD_EXPIRED)
        self.assertEqual(res["action"], "SEND_PAYMENT_LINK")
        self.assertIn("expired", res["reason"].lower())
        self.assertNotIn("insufficient", res["reason"].lower())

    def test_invalid_card_details_classification(self):
        """Test invalid card details produces INVALID_CARD."""
        res = classify_payment_failure(
            error_description="Invalid card number or security CVV",
            is_recurring=False,
            amount=500.0,
        )
        self.assertEqual(res["category"], CATEGORY_INVALID_CARD)
        self.assertEqual(res["action"], "SEND_PAYMENT_LINK")
        self.assertIn("invalid", res["reason"].lower())

    def test_authentication_required_classification(self):
        """Test 3DS / OTP failure produces AUTHENTICATION_REQUIRED."""
        res = classify_payment_failure(
            error_description="Customer 3DS authentication timed out",
            is_recurring=False,
            amount=3200.0,
        )
        self.assertEqual(res["category"], CATEGORY_AUTHENTICATION_REQUIRED)
        self.assertEqual(res["action"], "SEND_PAYMENT_LINK")
        self.assertIn("authentication", res["reason"].lower())

    def test_temporary_gateway_error_strategy_is_wait(self):
        """Test gateway network glitches recommend WAIT to prevent duplicate charges."""
        res = classify_payment_failure(
            error_description="Bank gateway timeout error",
            is_recurring=True,
            amount=1200.0,
        )
        self.assertEqual(res["category"], CATEGORY_TEMPORARY_GATEWAY_ERROR)
        self.assertEqual(res["action"], "WAIT")
        self.assertIn("wait", res["reason"].lower())

    def test_fraud_or_security_strategy_is_no_action_with_approval(self):
        """Test fraud indicators halt recovery with NO_ACTION and force approval."""
        res = classify_payment_failure(
            error_description="Transaction blocked: Stolen instrument reported",
            is_recurring=False,
            amount=5000.0,
        )
        self.assertEqual(res["category"], CATEGORY_FRAUD_OR_SECURITY)
        self.assertEqual(res["action"], "NO_ACTION")
        self.assertTrue(res["requires_human_approval"])
        self.assertIn("security", res["reason"].lower())

    def test_unknown_failure_strategy_is_investigate(self):
        """Test empty/missing failure description triggers INVESTIGATE with approval."""
        res = classify_payment_failure(
            error_description=None,
            error_code=None,
            is_recurring=False,
            amount=1000.0,
        )
        self.assertEqual(res["category"], CATEGORY_UNKNOWN)
        self.assertEqual(res["action"], "INVESTIGATE")
        self.assertTrue(res["requires_human_approval"])

    def test_distinct_rationales_for_different_failures(self):
        """Verify that distinct failure reasons generate distinct, non-identical explanations."""
        case_funds = decide_recovery_action({
            "payment_id": "pay_funds_01",
            "amount": 1000.0,
            "error_description": "Insufficient funds in bank account",
            "is_recurring_revenue": False,
        })
        case_limit = decide_recovery_action({
            "payment_id": "pay_limit_01",
            "amount": 1000.0,
            "error_description": "Card limit exceeded for today",
            "is_recurring_revenue": False,
        })
        case_expired = decide_recovery_action({
            "payment_id": "pay_expired_01",
            "amount": 1000.0,
            "error_description": "Card expired",
            "is_recurring_revenue": False,
        })

        self.assertNotEqual(case_funds["reason"], case_limit["reason"])
        self.assertNotEqual(case_funds["reason"], case_expired["reason"])
        self.assertNotEqual(case_limit["reason"], case_expired["reason"])

        self.assertEqual(case_funds["failure_category"], CATEGORY_INSUFFICIENT_FUNDS)
        self.assertEqual(case_limit["failure_category"], CATEGORY_CARD_LIMIT_EXCEEDED)
        self.assertEqual(case_expired["failure_category"], CATEGORY_CARD_EXPIRED)

    @patch("app.ai_recovery_agent.settings")
    def test_ai_recovery_agent_deterministic_fallback_uses_classifier(self, mock_settings):
        """Test AI agent fallback produces category-specific reasoning when LLM is unavailable."""
        mock_settings.GEMINI_API_KEY = ""
        mock_settings.GOOGLE_API_KEY = ""
        mock_settings.OPENAI_API_KEY = ""

        risk_case = {
            "payment_id": "pay_fallback_p12",
            "amount": 4500.0,
            "currency": "INR",
            "payment_status": "failed",
            "is_recurring_revenue": True,
            "risk_status": "at_risk",
            "error_description": "Card expired on registered account",
        }
        decision = ai_decide_recovery_action(risk_case, llm_provider=None)
        self.assertEqual(decision["decision_source"], "deterministic_fallback")
        self.assertEqual(decision["failure_category"], CATEGORY_CARD_EXPIRED)
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")
        self.assertIn("expired", decision["reason"].lower())


if __name__ == "__main__":
    unittest.main()
