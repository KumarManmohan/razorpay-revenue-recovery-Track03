"""
Test Suite for Milestone 15A Contextual Intelligence Benchmark Suite
Tests:
1. Structured AI output parsing
2. Urgency/priority bounds
3. Invalid priority fallback
4. Invalid escalation fallback
5. Unsupported action rejection
6. Fraud guardrail & NO_ACTION enforcement
7. High-value approval & priority HIGH enforcement
8. Contextual-factor validation & coverage metric
9. Evaluation database isolation (never writes to data/recovery.db)
10. Deterministic baseline heuristic reproducibility
11. Explanation quality rubric scoring behavior (0-5)
12. Existing recovery behavior and cases unchanged
"""

import json
import os
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from app.ai_recovery_agent import (
    enforce_ai_guardrails,
    ai_decide_recovery_action,
    GeminiProvider,
    LLMProvider,
)
from app.contextual_evaluator import (
    generate_contextual_benchmark_dataset,
    evaluate_deterministic_contextual_heuristic,
    score_explanation_quality,
    evaluate_context_factor_utilization,
    run_contextual_evaluation,
    get_latest_contextual_evaluation,
)
from app.recovery_decision import decide_recovery_action, HIGH_VALUE_THRESHOLD


import gc
import uuid

class TestContextualIntelligenceBenchmark(unittest.TestCase):

    def setUp(self):
        self.test_eval_db = f"data/test_eval_{uuid.uuid4().hex[:8]}.db"

    def tearDown(self):
        gc.collect()
        if os.path.exists(self.test_eval_db):
            try:
                os.remove(self.test_eval_db)
            except Exception:
                pass

    # 1. Structured AI output parsing
    def test_structured_ai_output_parsing(self):
        raw_ai = {
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.92,
            "urgency_score": 4,
            "priority": "HIGH",
            "escalation_recommended": False,
            "contextual_factors_used": ["customer_tenure", "previous_successful_payments"],
            "reason": "14-month loyal subscriber experienced card limit decline. Reissuing link to protect MRR.",
        }
        risk_case = {
            "payment_id": "pay_test_001",
            "amount": 2500.0,
            "currency": "INR",
            "failure_category": "CARD_LIMIT_EXCEEDED",
            "error_description": "Card limit exceeded.",
        }
        res = enforce_ai_guardrails(raw_ai, risk_case)
        self.assertEqual(res["action"], "SEND_PAYMENT_LINK")
        self.assertEqual(res["confidence"], 0.92)
        self.assertEqual(res["urgency_score"], 4)
        self.assertEqual(res["priority"], "HIGH")
        self.assertFalse(res["escalation_recommended"])
        self.assertIn("customer_tenure", res["contextual_factors_used"])
        self.assertIn("previous_successful_payments", res["contextual_factors_used"])

    # 2. Urgency/priority bounds
    def test_urgency_priority_bounds(self):
        # Clamping out-of-range urgency
        raw_ai_high = {
            "action": "SEND_PAYMENT_LINK",
            "urgency_score": 99,
            "priority": "HIGH",
            "reason": "High urgency link.",
        }
        risk_case = {"amount": 1000.0}
        res_high = enforce_ai_guardrails(raw_ai_high, risk_case)
        self.assertEqual(res_high["urgency_score"], 5)

        raw_ai_low = {
            "action": "SEND_PAYMENT_LINK",
            "urgency_score": -10,
            "priority": "LOW",
            "reason": "Low urgency link.",
        }
        res_low = enforce_ai_guardrails(raw_ai_low, risk_case)
        self.assertEqual(res_low["urgency_score"], 1)

    # 3. Invalid priority fallback
    def test_invalid_priority_fallback(self):
        raw_ai = {
            "action": "SEND_PAYMENT_LINK",
            "urgency_score": 5,
            "priority": "SUPER_CRITICAL_INVALID",
            "reason": "Invalid priority string.",
        }
        risk_case = {"amount": 1000.0}
        res = enforce_ai_guardrails(raw_ai, risk_case)
        # Urgency 5 infers HIGH priority
        self.assertEqual(res["priority"], "HIGH")

        raw_ai_low = {
            "action": "WAIT",
            "urgency_score": 2,
            "priority": "UNKNOWN_PRIO",
            "reason": "Invalid low priority.",
        }
        res_low = enforce_ai_guardrails(raw_ai_low, risk_case)
        self.assertEqual(res_low["priority"], "LOW")

    # 4. Invalid escalation fallback
    def test_invalid_escalation_fallback(self):
        raw_ai = {
            "action": "SEND_PAYMENT_LINK",
            "escalation_recommended": "truthy_string",
            "reason": "Non-boolean string provided.",
        }
        risk_case = {"amount": 1000.0}
        res = enforce_ai_guardrails(raw_ai, risk_case)
        self.assertTrue(isinstance(res["escalation_recommended"], bool))

    # 5. Unsupported action rejection
    def test_unsupported_action_rejection(self):
        raw_ai = {
            "action": "REFUND_AND_FORGIVE",  # Unsupported
            "reason": "Invalid action attempt.",
        }
        risk_case = {"amount": 1000.0}
        with self.assertRaises(ValueError):
            enforce_ai_guardrails(raw_ai, risk_case)

    # 6. Fraud guardrail & NO_ACTION enforcement
    def test_fraud_guardrail_enforcement(self):
        raw_ai = {
            "action": "SEND_PAYMENT_LINK",  # Model tried to issue link for stolen card
            "priority": "LOW",
            "escalation_recommended": False,
            "reason": "Attempting recovery on reported card.",
        }
        risk_case = {
            "amount": 5000.0,
            "failure_category": "FRAUD_OR_SECURITY",
            "error_description": "Card has been reported stolen.",
        }
        res = enforce_ai_guardrails(raw_ai, risk_case)
        self.assertEqual(res["action"], "NO_ACTION")
        self.assertTrue(res["requires_human_approval"])
        self.assertEqual(res["priority"], "HIGH")
        self.assertTrue(res["escalation_recommended"])
        self.assertIn("fraud_indicator", res["contextual_factors_used"])

    # 7. High-value approval & priority HIGH enforcement
    def test_high_value_approval_enforcement(self):
        raw_ai = {
            "action": "SEND_PAYMENT_LINK",
            "priority": "LOW",
            "requires_human_approval": False,
            "reason": "High value customer checkout.",
        }
        risk_case = {
            "amount": 75000.0,  # >= ₹50,000 threshold
            "failure_category": "CARD_LIMIT_EXCEEDED",
            "error_description": "Card limit exceeded.",
        }
        res = enforce_ai_guardrails(raw_ai, risk_case)
        self.assertEqual(res["action"], "SEND_PAYMENT_LINK")
        self.assertTrue(res["requires_human_approval"])
        self.assertEqual(res["priority"], "HIGH")
        self.assertIn("high_value_transaction", res["contextual_factors_used"])

    # 8. Contextual-factor validation & coverage metric
    def test_contextual_factor_coverage_metric(self):
        model_factors = ["customer_tenure", "previous_successful_payments", "extra_factor"]
        required = ["customer_tenure", "previous_successful_payments", "failed_attempt_count"]
        metrics = evaluate_context_factor_utilization(model_factors, required)
        self.assertEqual(metrics["required_count"], 3)
        self.assertEqual(metrics["matched_count"], 2)
        self.assertEqual(metrics["coverage_percentage"], 66.7)
        self.assertIn("failed_attempt_count", metrics["missing_factors"])

    # 9. Evaluation database isolation (never writes to data/recovery.db)
    def test_evaluation_database_isolation(self):
        res = run_contextual_evaluation(db_path=self.test_eval_db)
        self.assertTrue(os.path.exists(self.test_eval_db))
        self.assertIn("summary", res)
        self.assertEqual(res["summary"]["total_contextual_cases"], 16)

        # Verify tables created in isolated evaluation DB
        with sqlite3.connect(self.test_eval_db) as conn:
            cnt = conn.execute("SELECT count(*) FROM contextual_evaluations").fetchone()[0]
            self.assertEqual(cnt, 1)
            cases_cnt = conn.execute("SELECT count(*) FROM contextual_cases").fetchone()[0]
            self.assertEqual(cases_cnt, 16)

        # Verify operational recovery.db does not contain synthetic cases
        if os.path.exists("data/recovery.db"):
            with sqlite3.connect("data/recovery.db") as conn:
                r_cnt = conn.execute(
                    "SELECT count(*) FROM recovery_cases WHERE id LIKE 'ctx_%'"
                ).fetchone()[0]
                self.assertEqual(r_cnt, 0, "Evaluation cases must never be inserted into operational recovery.db!")

    # 10. Deterministic baseline heuristic reproducibility
    def test_deterministic_baseline_heuristic_reproducibility(self):
        loyal_case = {
            "amount": 7500.0,
            "is_recurring_revenue": True,
            "customer_tenure_months": 24,
            "previous_successful_payments_count": 18,
            "payment_attempts_count": 1,
            "recovery_link_previously_ignored": False,
            "error_description": "Card limit exceeded.",
        }
        base_res = evaluate_deterministic_contextual_heuristic(loyal_case)
        self.assertEqual(base_res["priority"], "HIGH")
        self.assertFalse(base_res["escalation_recommended"])
        self.assertIn("customer_tenure", base_res["contextual_factors_used"])

        chronic_case = {
            "amount": 7500.0,
            "is_recurring_revenue": True,
            "customer_tenure_months": 2,
            "payment_attempts_count": 4,
            "recovery_link_previously_ignored": True,
            "error_description": "Card limit reached.",
        }
        chronic_res = evaluate_deterministic_contextual_heuristic(chronic_case)
        self.assertTrue(chronic_res["escalation_recommended"])

    # 11. Explanation quality rubric scoring behavior (0-5)
    def test_explanation_rubric_scoring(self):
        good_explanation = (
            "The 24-month subscriber experienced a transient card limit decline during monthly renewal. "
            "Reissuing an automated payment link protects recurring MRR without premature operational escalation."
        )
        case = {
            "error_description": "Card limit exceeded for transaction.",
            "customer_tenure_months": 24,
        }
        score_data = score_explanation_quality(good_explanation, case, "SEND_PAYMENT_LINK")
        self.assertGreaterEqual(score_data["total_score"], 4)
        self.assertTrue(score_data["failure_identified"])
        self.assertTrue(score_data["history_referenced"])
        self.assertTrue(score_data["action_rationale"])
        self.assertTrue(score_data["grounded_and_specific"])

        poor_explanation = "Send link."
        poor_score = score_explanation_quality(poor_explanation, case, "SEND_PAYMENT_LINK")
        self.assertLessEqual(poor_score["total_score"], 2)

    # 12. Existing recovery behavior and protected cases unchanged
    def test_existing_recovery_behavior_unchanged(self):
        standard_case = {
            "payment_id": "pay_std_001",
            "amount": 850.0,
            "currency": "INR",
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Insufficient funds in customer account.",
            "is_recurring_revenue": False,
        }
        decision = decide_recovery_action(standard_case)
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")
        self.assertFalse(decision["requires_human_approval"])


if __name__ == "__main__":
    unittest.main()
