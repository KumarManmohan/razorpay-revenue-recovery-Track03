"""
Unit tests for Milestone B: Controlled Batch Recovery Evaluation & Simulation Engine.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.evaluation_engine import (
    ALL_FAILURE_CATEGORIES,
    evaluate_case_decision,
    generate_synthetic_evaluation_dataset,
    get_latest_evaluation_report,
    init_evaluation_db,
    run_batch_evaluation,
    simulate_recovery_outcome,
)
from app.failure_classifier import (
    CATEGORY_AUTHENTICATION_REQUIRED,
    CATEGORY_BANK_DECLINED,
    CATEGORY_CARD_EXPIRED,
    CATEGORY_CARD_LIMIT_EXCEEDED,
    CATEGORY_FRAUD_OR_SECURITY,
    CATEGORY_INSUFFICIENT_FUNDS,
    CATEGORY_INVALID_CARD,
    CATEGORY_TEMPORARY_GATEWAY_ERROR,
    CATEGORY_UNKNOWN,
)


class TestBatchRecoveryEvaluation(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.eval_db = os.path.join(self.test_dir, "test_eval.db")
        init_evaluation_db(self.eval_db)

    def tearDown(self):
        try:
            if os.path.exists(self.eval_db):
                os.remove(self.eval_db)
        except OSError:
            pass

    def test_deterministic_dataset_generation_reproducibility(self):
        """Fixed seed generates identical synthetic cases across multiple runs."""
        ds_1 = generate_synthetic_evaluation_dataset(num_cases=50, seed=42)
        ds_2 = generate_synthetic_evaluation_dataset(num_cases=50, seed=42)

        self.assertEqual(len(ds_1), 50)
        self.assertEqual(len(ds_2), 50)

        for c1, c2 in zip(ds_1, ds_2):
            self.assertEqual(c1["case_id"], c2["case_id"])
            self.assertEqual(c1["amount"], c2["amount"])
            self.assertEqual(c1["synthetic_category"], c2["synthetic_category"])
            self.assertEqual(c1["error_code"], c2["error_code"])
            self.assertEqual(c1["is_recurring_revenue"], c2["is_recurring_revenue"])

    def test_dataset_covers_all_nine_failure_categories(self):
        """Synthetic dataset covers all 9 failure categories."""
        ds = generate_synthetic_evaluation_dataset(num_cases=100, seed=42)
        categories = {c["synthetic_category"] for c in ds}

        for cat in ALL_FAILURE_CATEGORIES:
            self.assertIn(cat, categories, f"Missing failure category: {cat}")

    def test_deterministic_mode_decision_and_outcome(self):
        """Deterministic evaluation produces expected recovery actions without external calls."""
        case = {
            "case_id": "test_case_001",
            "order_id": "order_test_001",
            "payment_id": "pay_test_001",
            "amount": 1200.0,
            "currency": "INR",
            "is_recurring_revenue": False,
            "payment_attempts_count": 1,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card limit exceeded for today",
        }

        decision = evaluate_case_decision(case, mode="deterministic")
        self.assertEqual(decision["action"], "SEND_PAYMENT_LINK")
        self.assertEqual(decision["failure_category"], CATEGORY_CARD_LIMIT_EXCEEDED)
        self.assertEqual(decision["decision_source"], "deterministic_fallback")
        self.assertFalse(decision["requires_human_approval"])

        outcome = simulate_recovery_outcome(case, decision, seed=42)
        self.assertIn(outcome["outcome_status"], ["recovered_payment_link", "link_unpaid"])

    def test_fraud_security_case_is_strictly_blocked(self):
        """Fraud / security cases produce NO_ACTION and 0% recovery."""
        case = {
            "case_id": "test_fraud_001",
            "order_id": "order_fraud_001",
            "payment_id": "pay_fraud_001",
            "amount": 3500.0,
            "currency": "INR",
            "is_recurring_revenue": False,
            "payment_attempts_count": 1,
            "error_code": "CARD_BLOCKED",
            "error_description": "Transaction blocked due to stolen card report.",
        }

        decision = evaluate_case_decision(case, mode="deterministic")
        self.assertEqual(decision["action"], "NO_ACTION")
        self.assertEqual(decision["failure_category"], CATEGORY_FRAUD_OR_SECURITY)
        self.assertTrue(decision["requires_human_approval"])

        outcome = simulate_recovery_outcome(case, decision, seed=42)
        self.assertEqual(outcome["outcome_status"], "blocked_fraud_security")
        self.assertEqual(outcome["recovered_amount"], 0.0)

    def test_high_value_guardrail_in_evaluation(self):
        """Transactions >= ₹50,000 require human approval in evaluation simulation."""
        case = {
            "case_id": "test_high_val_001",
            "order_id": "order_high_val_001",
            "payment_id": "pay_high_val_001",
            "amount": 75000.0,
            "currency": "INR",
            "is_recurring_revenue": True,
            "payment_attempts_count": 1,
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Insufficient funds in account",
        }

        decision = evaluate_case_decision(case, mode="deterministic")
        self.assertTrue(decision["requires_human_approval"])

        outcome = simulate_recovery_outcome(case, decision, seed=42)
        self.assertIn(outcome["outcome_status"], [
            "recovered_human_approved",
            "link_unpaid_after_approval",
            "rejected_by_merchant",
        ])

    def test_batch_evaluation_run_kpis_and_db_isolation(self):
        """Batch evaluation runs completely within isolated evaluation database without touching operational recovery.db."""
        op_db = "data/recovery.db"
        op_db_mtime = os.path.getmtime(op_db) if os.path.exists(op_db) else None

        result = run_batch_evaluation(
            num_cases=50,
            seed=42,
            mode="deterministic",
            db_path=self.eval_db,
        )

        metrics = result["metrics"]
        self.assertEqual(metrics["total_cases"], 50)
        self.assertGreater(metrics["total_revenue_at_risk"], 0)
        self.assertGreaterEqual(metrics["recovered_revenue"], 0)
        self.assertEqual(metrics["fallback_decisions"], 50)
        self.assertEqual(metrics["llm_decisions"], 0)
        self.assertIn("recovery_rate_percentage", metrics)

        # Check category breakdown length (9 categories)
        breakdown = result["category_breakdown"]
        self.assertEqual(len(breakdown), 9)

        # Verify operational DB remained completely untouched
        if op_db_mtime is not None and os.path.exists(op_db):
            self.assertEqual(os.path.getmtime(op_db), op_db_mtime)

        # Verify query report
        report = get_latest_evaluation_report(self.eval_db)
        self.assertIsNotNone(report)
        self.assertEqual(report["metrics"]["total_cases"], 50)
        self.assertEqual(len(report["cases_sample"]), 25)

    def test_balanced_category_distribution_at_least_ten_per_category(self):
        """Synthetic dataset guarantees at least 10 cases for all 9 failure categories in 100-case dataset."""
        ds = generate_synthetic_evaluation_dataset(num_cases=100, seed=42)
        category_counts = {}
        for c in ds:
            cat = c["synthetic_category"]
            category_counts[cat] = category_counts.get(cat, 0) + 1

        self.assertEqual(len(category_counts), 9)
        for cat, count in category_counts.items():
            self.assertGreaterEqual(count, 10, f"Category {cat} has only {count} cases (< 10)")

    def test_decision_sensitive_outcome_model(self):
        """Simulated outcome strictly depends on final action chosen by the system."""
        case = {
            "case_id": "test_outcome_case_01",
            "amount": 2500.0,
            "currency": "INR",
            "is_recurring_revenue": False,
        }

        # Action: SEND_PAYMENT_LINK
        dec_link = {
            "action": "SEND_PAYMENT_LINK",
            "failure_category": CATEGORY_CARD_LIMIT_EXCEEDED,
            "confidence": 0.90,
            "requires_human_approval": False,
        }
        out_link = simulate_recovery_outcome(case, dec_link, seed=42)
        self.assertIn(out_link["outcome_status"], ["recovered_payment_link", "link_unpaid"])

        # Action: NO_ACTION (e.g. fraud or blocked)
        dec_no_action = {
            "action": "NO_ACTION",
            "failure_category": CATEGORY_FRAUD_OR_SECURITY,
            "confidence": 0.95,
            "requires_human_approval": True,
        }
        out_no_action = simulate_recovery_outcome(case, dec_no_action, seed=42)
        self.assertEqual(out_no_action["outcome_status"], "blocked_fraud_security")
        self.assertEqual(out_no_action["recovered_amount"], 0.0)

        # Action: WAIT (Gateway error)
        dec_wait = {
            "action": "WAIT",
            "failure_category": CATEGORY_TEMPORARY_GATEWAY_ERROR,
            "confidence": 0.85,
            "requires_human_approval": False,
        }
        out_wait = simulate_recovery_outcome(case, dec_wait, seed=42)
        self.assertIn(out_wait["outcome_status"], ["recovered_gateway_retry", "pending_gateway_retry"])

    def test_dual_mode_comparison_run(self):
        """Dual evaluation mode compares deterministic vs LLM and generates safety metrics."""
        result = run_batch_evaluation(
            num_cases=50,
            seed=42,
            mode="all",
            db_path=self.eval_db,
        )

        self.assertIn("comparison", result)
        self.assertIn("comparison_table", result)
        cmp = result["comparison"]
        self.assertEqual(cmp["fraud_auto_execution_violations"], 0)
        self.assertEqual(cmp["high_value_approval_bypasses"], 0)
        self.assertEqual(cmp["unsupported_actions_count"], 0)

    def test_confidence_independence(self):
        """Simulated outcome is independent of model-reported confidence to eliminate circularity."""
        case = {
            "case_id": "test_conf_indep_01",
            "amount": 1500.0,
            "currency": "INR",
            "is_recurring_revenue": False,
        }

        # Case with 0.50 confidence vs 0.99 confidence
        dec_low_conf = {
            "action": "SEND_PAYMENT_LINK",
            "failure_category": CATEGORY_INSUFFICIENT_FUNDS,
            "confidence": 0.50,
            "requires_human_approval": False,
        }
        dec_high_conf = {
            "action": "SEND_PAYMENT_LINK",
            "failure_category": CATEGORY_INSUFFICIENT_FUNDS,
            "confidence": 0.99,
            "requires_human_approval": False,
        }

        out_low = simulate_recovery_outcome(case, dec_low_conf, seed=42)
        out_high = simulate_recovery_outcome(case, dec_high_conf, seed=42)

        # Both must produce identical recovery outcomes under the same seed
        self.assertEqual(out_low["outcome_status"], out_high["outcome_status"])
        self.assertEqual(out_low["recovered_amount"], out_high["recovered_amount"])
        self.assertEqual(out_low["time_to_recovery_hours"], out_high["time_to_recovery_hours"])

    def test_gemini_vs_deterministic_outcome_fairness(self):
        """When Gemini and deterministic engines select the same action, outcomes are identical."""
        case = {
            "case_id": "test_fairness_01",
            "amount": 2200.0,
            "currency": "INR",
            "is_recurring_revenue": False,
        }

        gemini_decision = {
            "action": "SEND_PAYMENT_LINK",
            "failure_category": CATEGORY_CARD_LIMIT_EXCEEDED,
            "confidence": 0.95,
            "requires_human_approval": False,
        }
        deterministic_decision = {
            "action": "SEND_PAYMENT_LINK",
            "failure_category": CATEGORY_CARD_LIMIT_EXCEEDED,
            "confidence": 0.85,
            "requires_human_approval": False,
        }

        out_gemini = simulate_recovery_outcome(case, gemini_decision, seed=42)
        out_det = simulate_recovery_outcome(case, deterministic_decision, seed=42)

        self.assertEqual(out_gemini["outcome_status"], out_det["outcome_status"])
        self.assertEqual(out_gemini["recovered_amount"], out_det["recovered_amount"])

    def test_high_value_human_approval_independent_of_confidence(self):
        """High-value cases evaluate human approval and payer completion independent of confidence."""
        case = {
            "case_id": "test_hv_approval_01",
            "amount": 65000.0,
            "currency": "INR",
            "is_recurring_revenue": False,
        }

        dec_hv_low = {
            "action": "SEND_PAYMENT_LINK",
            "failure_category": CATEGORY_BANK_DECLINED,
            "confidence": 0.60,
            "requires_human_approval": True,
        }
        dec_hv_high = {
            "action": "SEND_PAYMENT_LINK",
            "failure_category": CATEGORY_BANK_DECLINED,
            "confidence": 0.98,
            "requires_human_approval": True,
        }

        out_low = simulate_recovery_outcome(case, dec_hv_low, seed=42)
        out_high = simulate_recovery_outcome(case, dec_hv_high, seed=42)

        self.assertEqual(out_low["outcome_status"], out_high["outcome_status"])
        self.assertEqual(out_low["recovered_amount"], out_high["recovered_amount"])

    def test_wait_and_investigate_simulation_independence(self):
        """WAIT and INVESTIGATE simulation branches are independent of confidence."""
        case = {
            "case_id": "test_wait_case_01",
            "amount": 1000.0,
            "currency": "INR",
            "is_recurring_revenue": False,
        }

        dec_wait_1 = {"action": "WAIT", "failure_category": CATEGORY_TEMPORARY_GATEWAY_ERROR, "confidence": 0.40}
        dec_wait_2 = {"action": "WAIT", "failure_category": CATEGORY_TEMPORARY_GATEWAY_ERROR, "confidence": 0.95}
        self.assertEqual(
            simulate_recovery_outcome(case, dec_wait_1, seed=42),
            simulate_recovery_outcome(case, dec_wait_2, seed=42),
        )

        dec_inv_1 = {"action": "INVESTIGATE", "failure_category": CATEGORY_UNKNOWN, "confidence": 0.30}
        dec_inv_2 = {"action": "INVESTIGATE", "failure_category": CATEGORY_UNKNOWN, "confidence": 0.90}
        self.assertEqual(
            simulate_recovery_outcome(case, dec_inv_1, seed=42),
            simulate_recovery_outcome(case, dec_inv_2, seed=42),
        )

    def test_financial_reconciliation_exactness(self):
        """Financial metrics in batch evaluation strictly reconcile to total exposure."""
        res = run_batch_evaluation(num_cases=50, seed=42, mode="deterministic", db_path=self.eval_db)
        metrics = res["metrics"]

        tot = metrics["total_revenue_at_risk"]
        rec = metrics["recovered_revenue"]
        unrec = metrics["unrecovered_revenue"]
        auto = metrics["auto_recovered_revenue"]
        human = metrics["human_approved_revenue"]
        fraud = metrics["blocked_fraud_revenue"]
        retry_exh = metrics["blocked_retry_exhausted_revenue"]
        pending = metrics["pending_or_escalated_revenue"]

        self.assertAlmostEqual(tot, rec + unrec, places=2)
        self.assertAlmostEqual(rec, auto + human, places=2)
        self.assertAlmostEqual(unrec, fraud + retry_exh + pending, places=2)

    def test_fraud_and_retry_exhaustion_separation_100_cases(self):
        """Canonical 100-case seed-42 evaluation cleanly separates fraud blocks and retry exhaustion stops."""
        res = run_batch_evaluation(num_cases=100, seed=42, mode="deterministic", db_path=self.eval_db)
        metrics = res["metrics"]

        # Fraud metrics
        self.assertEqual(metrics["fraud_blocks"], 11)
        self.assertAlmostEqual(metrics["blocked_fraud_revenue"], 176576.76, places=2)

        # Retry exhaustion metrics
        self.assertEqual(metrics["retry_exhausted_blocks"], 17)
        self.assertAlmostEqual(metrics["blocked_retry_exhausted_revenue"], 417628.21, places=2)

        # Combined halted summary
        self.assertEqual(metrics["total_halted_blocks"], 28)
        self.assertAlmostEqual(metrics["total_halted_revenue"], 594204.97, places=2)


if __name__ == "__main__":
    unittest.main()

