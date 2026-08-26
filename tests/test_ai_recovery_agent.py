import json
import unittest
from unittest.mock import MagicMock, patch
from app.ai_recovery_agent import (
    LLMProvider,
    OpenAIProvider,
    ai_decide_recovery_action,
    build_ai_prompt,
)
from app.config import settings


class MockLLMProvider(LLMProvider):
    """Custom mock provider for testing LLM outputs and prompt inspection."""
    def __init__(self, response_text: str = None, raise_error: bool = False):
        self.response_text = response_text
        self.raise_error = raise_error
        self.last_prompt = None

    def generate_recommendation(self, prompt: str):
        self.last_prompt = prompt
        if self.raise_error:
            raise ConnectionError("Simulated LLM API connection timeout.")
        return self.response_text


class TestAIRecoveryAgent(unittest.TestCase):

    def test_mock_successful_llm_response_sets_decision_source_llm(self):
        """Mock successful LLM response produces decision_source == 'llm'."""
        mock_response = json.dumps({
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.95,
            "reason": "Card limit exceeded on corporate card. Payment link recommended for alternate card.",
        })
        provider = MockLLMProvider(response_text=mock_response)

        risk_case = {
            "payment_id": "pay_llm_001",
            "amount": 1200.0,
            "currency": "INR",
            "payment_status": "failed",
            "is_recurring_revenue": False,
            "risk_status": "at_risk",
            "failure_category": "CARD_LIMIT_EXCEEDED",
            "error_description": "Card limit exceeded",
        }

        decision = ai_decide_recovery_action(risk_case, llm_provider=provider)
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")
        self.assertEqual(decision["confidence"], 0.95)
        self.assertEqual(decision["decision_source"], "llm")
        self.assertFalse(decision["requires_human_approval"])

    def test_llm_chooses_send_payment_link_for_insufficient_funds(self):
        """LLM chooses SEND_PAYMENT_LINK for insufficient funds."""
        mock_response = json.dumps({
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.90,
            "reason": "Account had insufficient funds. Payment link sent to allow alternate account payment.",
        })
        provider = MockLLMProvider(response_text=mock_response)

        risk_case = {
            "payment_id": "pay_funds_002",
            "amount": 850.0,
            "currency": "INR",
            "failure_category": "INSUFFICIENT_FUNDS",
            "error_description": "Insufficient funds in bank account.",
        }

        decision = ai_decide_recovery_action(risk_case, llm_provider=provider)
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")
        self.assertEqual(decision["decision_source"], "llm")

    def test_llm_chooses_wait_for_temporary_gateway_error(self):
        """LLM chooses WAIT for temporary gateway error, preventing duplicate charges."""
        mock_response = json.dumps({
            "action": "WAIT",
            "confidence": 0.88,
            "reason": "Bank server timeout detected. Automated wait in place.",
        })
        provider = MockLLMProvider(response_text=mock_response)

        risk_case = {
            "payment_id": "pay_gtw_003",
            "amount": 500.0,
            "currency": "INR",
            "failure_category": "TEMPORARY_GATEWAY_ERROR",
            "error_description": "Gateway timeout communicating with bank.",
        }

        decision = ai_decide_recovery_action(risk_case, llm_provider=provider)
        self.assertEqual(decision["action"], "WAIT")
        self.assertEqual(decision["decision_source"], "llm")

    def test_llm_chooses_no_action_for_fraud(self):
        """LLM chooses NO_ACTION and requires human approval for fraud/security."""
        mock_response = json.dumps({
            "action": "NO_ACTION",
            "confidence": 0.98,
            "reason": "Stolen card reported by issuer. Automated recovery locked for compliance.",
        })
        provider = MockLLMProvider(response_text=mock_response)

        risk_case = {
            "payment_id": "pay_fraud_004",
            "amount": 2500.0,
            "currency": "INR",
            "failure_category": "FRAUD_OR_SECURITY",
            "error_code": "CARD_BLOCKED",
            "error_description": "Stolen card report received.",
        }

        decision = ai_decide_recovery_action(risk_case, llm_provider=provider)
        self.assertEqual(decision["action"], "NO_ACTION")
        self.assertTrue(decision["requires_human_approval"])
        self.assertEqual(decision["decision_source"], "llm")

    def test_high_value_llm_recommendation_still_requires_human_approval(self):
        """High-value transactions (>= ₹50,000) strictly preserve human approval requirement."""
        mock_response = json.dumps({
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.92,
            "reason": "High value payment retry recommended.",
            "requires_human_approval": False,  # Even if LLM says False, server forces True
        })
        provider = MockLLMProvider(response_text=mock_response)

        risk_case = {
            "payment_id": "pay_high_val_005",
            "amount": 75000.0,
            "currency": "INR",
            "failure_category": "CARD_LIMIT_EXCEEDED",
        }

        decision = ai_decide_recovery_action(risk_case, llm_provider=provider)
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")
        self.assertTrue(decision["requires_human_approval"])
        self.assertEqual(decision["decision_source"], "llm")

    def test_malformed_llm_output_falls_back_to_deterministic(self):
        """Malformed or non-JSON output safely falls back to deterministic engine."""
        provider = MockLLMProvider(response_text="Error: Could not complete inference as JSON.")

        risk_case = {
            "payment_id": "pay_malformed_006",
            "amount": 750.0,
            "currency": "INR",
            "error_description": "Insufficient balance",
        }

        decision = ai_decide_recovery_action(risk_case, llm_provider=provider)
        self.assertEqual(decision["decision_source"], "deterministic_fallback")
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")

    def test_provider_unavailable_or_error_falls_back_to_deterministic(self):
        """Provider network failure or connection error falls back cleanly."""
        provider = MockLLMProvider(raise_error=True)

        risk_case = {
            "payment_id": "pay_unavail_007",
            "amount": 900.0,
            "currency": "INR",
            "error_description": "Card expired",
        }

        decision = ai_decide_recovery_action(risk_case, llm_provider=provider)
        self.assertEqual(decision["decision_source"], "deterministic_fallback")
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")

    def test_unsupported_action_falls_back_to_deterministic(self):
        """LLM proposing an unsupported/hallucinated action triggers fallback."""
        mock_response = json.dumps({
            "action": "AUTO_CHARGE_BACKUP_CARD_NOW",  # Unsupported action!
            "confidence": 0.99,
            "reason": "Attempting arbitrary direct billing.",
        })
        provider = MockLLMProvider(response_text=mock_response)

        risk_case = {
            "payment_id": "pay_unsupported_008",
            "amount": 850.0,
            "currency": "INR",
            "error_description": "Card limit exceeded",
        }

        decision = ai_decide_recovery_action(risk_case, llm_provider=provider)
        self.assertEqual(decision["decision_source"], "deterministic_fallback")
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")

    def test_server_secrets_are_never_included_in_llm_prompt(self):
        """Sanitized prompt builder must never leak server secrets or credentials."""
        provider = MockLLMProvider(response_text=json.dumps({"action": "SEND_PAYMENT_LINK", "confidence": 0.9, "reason": "ok"}))

        risk_case = {
            "payment_id": "pay_leak_check_009",
            "amount": 500.0,
            "currency": "INR",
            "error_description": "Card limit exceeded",
            # Inject simulated sensitive properties
            "razorpay_key_secret": "rzp_secret_super_confidential_123",
            "webhook_secret": "webhook_secret_do_not_leak_456",
            "db_connection_url": "sqlite:///data/recovery.db",
        }

        prompt = build_ai_prompt(risk_case)
        self.assertNotIn("rzp_secret_super_confidential_123", prompt)
        self.assertNotIn("webhook_secret_do_not_leak_456", prompt)
        self.assertNotIn("sqlite:///data/recovery.db", prompt)
        self.assertIn("pay_leak_check_009", prompt)
        self.assertIn("500.0", prompt)

    @patch("requests.post")
    def test_gemini_provider_success(self, mock_post):
        """Test GeminiProvider handles standard JSON generation format correctly."""
        from app.ai_recovery_agent import GeminiProvider

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "action": "SEND_PAYMENT_LINK",
                                    "confidence": 0.96,
                                    "reason": "Issuing bank declined. Payment link recommended.",
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_resp

        provider = GeminiProvider(api_key="test_dummy_gemini_key", model="gemini-2.5-flash")
        result = provider.generate_recommendation("Evaluate payment failure")
        self.assertIsNotNone(result)
        parsed = json.loads(result)
        self.assertEqual(parsed["action"], "SEND_PAYMENT_LINK")
        self.assertEqual(parsed["confidence"], 0.96)


if __name__ == "__main__":
    unittest.main()
