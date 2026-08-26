import os
import sqlite3
import unittest
from app.demo_dataset import seed_demo_dataset, reset_demo_dataset, DEMO_CASES
from app.database import get_all_cases, get_dashboard_stats, create_or_get_recovery_case, init_db


class TestDemoDataset(unittest.TestCase):
    def setUp(self):
        self.test_db = "tests/test_demo_db.sqlite"
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        init_db(self.test_db)

    def tearDown(self):
        if os.path.exists(self.test_db):
            os.remove(self.test_db)


    def test_seed_demo_dataset_creates_all_scenarios(self):
        """Verify seed_demo_dataset inserts all 11 demo cases and audit events."""
        res = seed_demo_dataset(db_path=self.test_db)
        self.assertEqual(res["cases_seeded"], len(DEMO_CASES))
        self.assertGreater(res["events_seeded"], 10)

        cases = get_all_cases(db_path=self.test_db)

        self.assertEqual(len(cases), len(DEMO_CASES))

    def test_demo_dataset_covers_all_failure_categories(self):
        """Verify that all normalized failure categories are represented in the seeded dataset."""
        seed_demo_dataset(db_path=self.test_db)
        cases = get_all_cases(db_path=self.test_db)


        categories_present = {c["failure_category"] for c in cases if c.get("failure_category")}
        expected_categories = {
            "INSUFFICIENT_FUNDS",
            "CARD_LIMIT_EXCEEDED",
            "CARD_EXPIRED",
            "AUTHENTICATION_REQUIRED",
            "INVALID_CARD",
            "BANK_DECLINED",
            "TEMPORARY_GATEWAY_ERROR",
            "FRAUD_OR_SECURITY",
            "UNKNOWN",
        }
        self.assertTrue(expected_categories.issubset(categories_present))

    def test_demo_dataset_has_recovered_case(self):
        """Verify that at least one demo case is in 'recovered' state with valid payment metadata."""
        seed_demo_dataset(db_path=self.test_db)
        cases = get_all_cases(db_path=self.test_db)


        recovered = [c for c in cases if c["execution_status"] == "recovered"]
        self.assertGreaterEqual(len(recovered), 1)
        rec = recovered[0]
        self.assertIsNotNone(rec.get("recovered_amount"))
        self.assertGreater(rec["recovered_amount"], 0)
        self.assertIsNotNone(rec.get("recovered_payment_id"))
        self.assertIsNotNone(rec.get("recovered_at"))

    def test_demo_dataset_has_approval_required_cases(self):
        """Verify high-value and security cases are marked as requiring human approval."""
        seed_demo_dataset(db_path=self.test_db)
        cases = get_all_cases(db_path=self.test_db)


        approval_cases = [c for c in cases if c["execution_status"] == "approval_required"]
        self.assertGreaterEqual(len(approval_cases), 2)

    def test_reset_demo_dataset_preserves_real_cases(self):
        """Verify reset_demo_dataset only purges demo cases (id LIKE 'case_demo_%')."""
        # 1. Create a non-demo case
        create_or_get_recovery_case(
            {
                "payment_id": "pay_real_production_test_999",
                "amount": 5000.0,
                "payment_status": "failed",
                "risk_status": "at_risk",
                "error_description": "Card limit exceeded",
            },
            db_path=self.test_db,
        )

        # 2. Seed demo cases
        seed_demo_dataset(db_path=self.test_db, reset_first=False)
        cases_before = get_all_cases(db_path=self.test_db)
        self.assertEqual(len(cases_before), len(DEMO_CASES) + 1)

        # 3. Reset demo dataset
        deleted_count = reset_demo_dataset(db_path=self.test_db)
        self.assertEqual(deleted_count, len(DEMO_CASES))

        # 4. Verify only the non-demo case remains
        cases_after = get_all_cases(db_path=self.test_db)
        self.assertEqual(len(cases_after), 1)
        self.assertEqual(cases_after[0]["payment_id"], "pay_real_production_test_999")


if __name__ == "__main__":
    unittest.main()

