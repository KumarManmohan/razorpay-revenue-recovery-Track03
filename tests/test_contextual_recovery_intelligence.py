"""
Unit tests for Milestone C: Contextual Recovery Intelligence.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.ai_recovery_agent import (
    build_ai_prompt,
    build_sanitized_recovery_context,
    enforce_ai_guardrails,
    mask_customer_identifier,
)
from app.contextual_evaluator import (
    generate_contextual_benchmark_dataset,
    get_latest_contextual_evaluation,
    run_contextual_evaluation,
)
from app.recovery_decision import (
    ALLOWED_ACTIONS,
    HIGH_VALUE_THRESHOLD,
    decide_recovery_action,
)


class TestContextualRecoveryIntelligence(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.eval_db = os.path.join(self.test_dir, "test_ctx_eval.db")

    def tearDown(self):
        try:
            if os.path.exists(self.eval_db):
                os.remove(self.eval_db)
        except OSError:
            pass

    def test_customer_identifier_masking(self):
        """Customer email and identifier are masked to protect customer PII."""
        self.assertEqual(mask_customer_identifier("aravind.sub@example.com"), "a***b@example.com")
        self.assertEqual(mask_customer_identifier("cust_123456789"), "cus***89")
        self.assertEqual(mask_customer_identifier(None), "cust_anonymous")

    def test_sanitized_context_builder_excludes_secrets_and_pii(self):
        """Context builder excludes all API secrets and includes only sanitized business signals."""
        raw_case = {
            "payment_id": "pay_test_ctx_01",
            "amount": 7500.0,
            "currency": "INR",
            "is_recurring_revenue": True,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card limit exceeded for today.",
            "customer_id": "merchant.vip@enterprise.com",
            "payment_attempts_count": 2,
            "previous_failed_attempts_count": 1,
            "previous_successful_payments_count": 12,
            "time_since_last_successful_payment_days": 30.0,
            "time_since_last_failed_attempt_hours": 2.5,
            "has_active_recovery_link": True,
            "prior_recovery_links_count": 1,
            "recovery_link_previously_ignored": False,
            "customer_tenure_months": 14,
            # Leaked fields that MUST NOT appear in sanitized context
            "razorpay_key_secret": "rzp_secret_dummy_12345",
            "gemini_api_key": "AIzaSyDummyKey67890",
            "webhook_secret": "whsec_test_secret",
            "database_password": "supersecretpassword",
        }

        ctx = build_sanitized_recovery_context(raw_case)
        ctx_str = json.dumps(ctx)

        # Assert no sensitive secrets in context
        self.assertNotIn("rzp_secret_dummy_12345", ctx_str)
        self.assertNotIn("AIzaSyDummyKey67890", ctx_str)
        self.assertNotIn("whsec_test_secret", ctx_str)
        self.assertNotIn("supersecretpassword", ctx_str)
        self.assertNotIn("merchant.vip@enterprise.com", ctx_str)

        # Assert contextual business signals are properly included
        self.assertEqual(ctx["transaction"]["amount"], 7500.0)
        self.assertTrue(ctx["transaction"]["is_recurring_revenue"])
        self.assertEqual(ctx["customer_profile"]["previous_successful_payments_count"], 12)
        self.assertEqual(ctx["customer_profile"]["tenure_months"], 14)
        self.assertEqual(ctx["recovery_history"]["current_case_attempt_count"], 2)
        self.assertEqual(ctx["recovery_history"]["previous_failed_attempts_count"], 1)
        self.assertEqual(ctx["recovery_history"]["time_since_last_failed_attempt_hours"], 2.5)

    def test_prompt_includes_context_instructions(self):
        """Prompt instructs the AI model to analyze context and return valid bounded action."""
        case = {
            "amount": 2400.0,
            "currency": "INR",
            "is_recurring_revenue": False,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card limit reached",
            "payment_attempts_count": 1,
        }
        prompt = build_ai_prompt(case)
        self.assertIn("Context Payload:", prompt)
        self.assertIn("SEND_PAYMENT_LINK", prompt)
        self.assertIn("customer recovery history", prompt)

    def test_contextual_benchmark_dataset_completeness(self):
        """Contextual benchmark dataset contains complete, distinct scenarios."""
        ds = generate_contextual_benchmark_dataset()
        self.assertGreaterEqual(len(ds), 16)

        case_ids = [c["case_id"] for c in ds]
        self.assertEqual(len(case_ids), len(set(case_ids)), "Duplicate case IDs found in contextual dataset")

        for c in ds:
            self.assertIn("case_id", c)
            self.assertIn("scenario_name", c)
            self.assertIn("is_recurring_revenue", c)
            self.assertIn("payment_attempts_count", c)
            self.assertIn("context_hypothesis", c)

    def test_high_value_guardrail_authoritative_over_context(self):
        """Even with rich positive context, transactions >= ₹50,000 strictly enforce human approval."""
        high_val_case = {
            "amount": 75000.0,
            "currency": "INR",
            "is_recurring_revenue": True,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card limit exceeded on corporate card",
            "payment_attempts_count": 1,
            "previous_successful_payments_count": 24,
            "customer_tenure_months": 24,
        }

        # Simulated AI recommendation without approval flag
        raw_ai = {
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.95,
            "reason": "Highly trusted VIP corporate client with 24 months flawless track record.",
            "requires_human_approval": False,
        }

        guarded = enforce_ai_guardrails(raw_ai, high_val_case)
        self.assertTrue(guarded["requires_human_approval"], "High-value guardrail failed to enforce human approval")
        self.assertEqual(guarded["action"], "SEND_PAYMENT_LINK")

    def test_fraud_guardrail_authoritative_over_context(self):
        """Fraud / security alerts strictly force NO_ACTION and human approval regardless of tenure."""
        fraud_case = {
            "amount": 18000.0,
            "currency": "INR",
            "is_recurring_revenue": False,
            "error_code": "CARD_BLOCKED",
            "error_description": "Transaction blocked: Stolen card reported.",
            "previous_successful_payments_count": 5,
        }

        raw_ai = {
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.80,
            "reason": "Customer had prior successful transactions.",
            "requires_human_approval": False,
        }

        guarded = enforce_ai_guardrails(raw_ai, fraud_case)
        self.assertEqual(guarded["action"], "NO_ACTION")
        self.assertTrue(guarded["requires_human_approval"])

    def test_contextual_evaluation_run_and_db_isolation(self):
        """Contextual evaluation runs in isolated evaluation DB without touching operational recovery.db."""
        op_db = "data/recovery.db"
        op_mtime = os.path.getmtime(op_db) if os.path.exists(op_db) else None

        result = run_contextual_evaluation(db_path=self.eval_db)
        summary = result["summary"]

        self.assertGreaterEqual(summary["total_contextual_cases"], 16)
        self.assertIn("policy_agreement_percentage", summary)
        self.assertIn("fraud_blocks_enforced", summary)
        self.assertIn("human_approvals_mandated", summary)

        # Check DB query report
        report = get_latest_contextual_evaluation(self.eval_db)
        self.assertIsNotNone(report)
        self.assertGreaterEqual(report["summary"]["total_contextual_cases"], 16)
        self.assertGreaterEqual(len(report["cases"]), 16)

        # Operational DB was not modified
        if op_mtime is not None and os.path.exists(op_db):
            self.assertEqual(os.path.getmtime(op_db), op_mtime)


if __name__ == "__main__":
    unittest.main()
