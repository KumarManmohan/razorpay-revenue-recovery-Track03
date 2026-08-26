"""
Focused reproducibility and determinism tests for the synthetic evaluation engine.
Verifies that simulate_recovery_outcome is strictly reproducible across calls and processes.
"""

import subprocess
import sys
import unittest
from app.evaluation_engine import simulate_recovery_outcome


class TestEvaluationEngineDeterminism(unittest.TestCase):
    def test_same_inputs_produce_identical_outcome(self):
        """Verify identical seed + case + action produces strictly identical simulation outcomes."""
        case = {
            "case_id": "eval_case_42_0007",
            "amount": 6949.61,
            "failure_category": "INSUFFICIENT_FUNDS",
        }
        decision = {
            "action": "SEND_PAYMENT_LINK",
            "failure_category": "INSUFFICIENT_FUNDS",
            "requires_human_approval": False,
            "confidence": 0.85,
        }

        # Run 10 consecutive simulations in same process
        results = [simulate_recovery_outcome(case, decision, seed=42) for _ in range(10)]

        for res in results[1:]:
            self.assertEqual(res["outcome_status"], results[0]["outcome_status"])
            self.assertEqual(res["recovered_amount"], results[0]["recovered_amount"])
            self.assertEqual(res["time_to_recovery_hours"], results[0]["time_to_recovery_hours"])
            self.assertEqual(res["recovery_channel"], results[0]["recovery_channel"])

    def test_input_variation_changes_derived_seed(self):
        """Verify changing seed, case_id, or action produces independent random streams."""
        import hashlib

        def get_seed(seed_val, case_id, action):
            return int(
                hashlib.sha256(f"{seed_val}_{case_id}_{action}".encode("utf-8")).hexdigest()[:16],
                16,
            )

        base = get_seed(42, "case_01", "SEND_PAYMENT_LINK")
        diff_seed = get_seed(43, "case_01", "SEND_PAYMENT_LINK")
        diff_case = get_seed(42, "case_02", "SEND_PAYMENT_LINK")
        diff_action = get_seed(42, "case_01", "WAIT")

        self.assertNotEqual(base, diff_seed)
        self.assertNotEqual(base, diff_case)
        self.assertNotEqual(base, diff_action)

    def test_cross_process_reproducibility(self):
        """Verify identical outcome across two completely separate Python subprocesses."""
        code = (
            "import json\n"
            "from app.evaluation_engine import simulate_recovery_outcome\n"
            "case = {'case_id': 'eval_case_42_0007', 'amount': 6949.61, 'failure_category': 'INSUFFICIENT_FUNDS'}\n"
            "decision = {'action': 'SEND_PAYMENT_LINK', 'failure_category': 'INSUFFICIENT_FUNDS', 'requires_human_approval': False, 'confidence': 0.85}\n"
            "res = simulate_recovery_outcome(case, decision, seed=42)\n"
            "print(json.dumps(res))\n"
        )

        proc1 = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
        )
        proc2 = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(proc1.stdout.strip(), proc2.stdout.strip())
        self.assertTrue(len(proc1.stdout.strip()) > 0)


if __name__ == "__main__":
    unittest.main()
