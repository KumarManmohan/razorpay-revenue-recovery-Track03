import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from app.database import (
    init_db,
    create_or_get_recovery_case,
    update_execution_status,
    reconcile_recovery_payment,
    get_dashboard_stats,
    get_case_by_id,
)
from app.recovery_executor import fetch_payment_link_url


class TestKpiRecoveredRevenueReporting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_kpi.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    # 1. New failed case increases current Revenue at Risk
    def test_01_new_failed_case_increases_current_revenue_at_risk(self):
        stats_initial = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats_initial["total_revenue_at_risk"], 0.0)

        create_or_get_recovery_case({
            "order_id": "order_fail_01",
            "amount": 2500.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }, db_path=self.db_path)

        stats_after = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats_after["total_revenue_at_risk"], 2500.0)
        self.assertEqual(stats_after["recovered_revenue"], 0.0)
        self.assertEqual(stats_after["recovery_rate_percentage"], 0.0)

    # 2. Creating a Payment Link does NOT increase recovered revenue
    def test_02_creating_payment_link_does_not_increase_recovered_revenue(self):
        c, _ = create_or_get_recovery_case({
            "order_id": "order_exec_001",
            "amount": 2400.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }, db_path=self.db_path)

        update_execution_status(c["id"], {
            "status": "executed",
            "payment_link_id": "plink_exec_001",
            "payment_link_url": "https://rzp.io/rzp/exec001",
        }, db_path=self.db_path)

        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["recovered_revenue"], 0.0)

    # 3. Creating a Payment Link does NOT decrease current Revenue at Risk
    def test_03_creating_payment_link_does_not_decrease_revenue_at_risk(self):
        c, _ = create_or_get_recovery_case({
            "order_id": "order_risk_check",
            "amount": 3500.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }, db_path=self.db_path)

        stats_before_link = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats_before_link["total_revenue_at_risk"], 3500.0)

        update_execution_status(c["id"], {
            "status": "executed",
            "payment_link_id": "plink_risk_check",
            "payment_link_url": "https://rzp.io/rzp/risk001",
        }, db_path=self.db_path)

        stats_after_link = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats_after_link["total_revenue_at_risk"], 3500.0)
        self.assertEqual(stats_after_link["recovered_revenue"], 0.0)

    # 4. Successful reconciliation changes risk_status from at_risk to recovered
    def test_04_successful_reconciliation_changes_risk_status_to_recovered(self):
        c, _ = create_or_get_recovery_case({
            "order_id": "order_rec_status_check",
            "amount": 1200.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }, db_path=self.db_path)

        case_before = get_case_by_id(c["id"], db_path=self.db_path)
        self.assertEqual(case_before["risk_status"], "at_risk")
        self.assertEqual(case_before["execution_status"], "pending")

        rec_case, status = reconcile_recovery_payment(
            c["id"],
            recovered_payment_id="pay_rec_status_01",
            recovered_amount=1200.0,
            db_path=self.db_path,
        )
        self.assertEqual(status, "reconciled")
        self.assertEqual(rec_case["risk_status"], "recovered")
        self.assertEqual(rec_case["execution_status"], "recovered")

    # 5. Successful reconciliation decreases current Revenue at Risk by the recovered amount
    def test_05_reconciliation_decreases_current_revenue_at_risk(self):
        c1, _ = create_or_get_recovery_case({
            "order_id": "order_multi_risk_1",
            "amount": 2000.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }, db_path=self.db_path)

        c2, _ = create_or_get_recovery_case({
            "order_id": "order_multi_risk_2",
            "amount": 3000.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }, db_path=self.db_path)

        stats_before = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats_before["total_revenue_at_risk"], 5000.0)
        self.assertEqual(stats_before["recovered_revenue"], 0.0)

        # Reconcile c1 (₹2000)
        reconcile_recovery_payment(
            c1["id"],
            recovered_payment_id="pay_rec_multi_1",
            recovered_amount=2000.0,
            db_path=self.db_path,
        )

        stats_after = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats_after["total_revenue_at_risk"], 3000.0)  # Decreased by ₹2000
        self.assertEqual(stats_after["recovered_revenue"], 2000.0)

    # 6. Successful reconciliation increases recovered revenue by exactly the captured amount
    def test_06_reconciliation_increases_recovered_revenue_by_exact_amount(self):
        c, _ = create_or_get_recovery_case({
            "order_id": "order_rec_589",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }, db_path=self.db_path)

        reconcile_recovery_payment(
            c["id"],
            recovered_payment_id="pay_rec_589",
            recovered_amount=589.0,
            db_path=self.db_path,
        )

        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["recovered_revenue"], 589.0)
        self.assertEqual(stats["total_revenue_at_risk"], 0.0)

    # 7. Recovery rate uses historical exposure, not current unresolved risk
    def test_07_recovery_rate_uses_historical_exposure(self):
        # Case 1: ₹500 (recovered)
        c1, _ = create_or_get_recovery_case({
            "order_id": "order_rate_1",
            "amount": 500.0,
            "payment_status": "failed",
            "risk_status": "at_risk",
        }, db_path=self.db_path)
        reconcile_recovery_payment(c1["id"], recovered_payment_id="pay_rate_1", recovered_amount=500.0, db_path=self.db_path)

        # Case 2: ₹1500 (unresolved at-risk)
        create_or_get_recovery_case({
            "order_id": "order_rate_2",
            "amount": 1500.0,
            "payment_status": "failed",
            "risk_status": "at_risk",
        }, db_path=self.db_path)

        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["total_revenue_at_risk"], 1500.0)  # Current unresolved
        self.assertEqual(stats["recovered_revenue"], 500.0)
        self.assertEqual(stats["historical_exposure"], 2000.0)     # Total historical = 1500 + 500
        # 500 / 2000 * 100 = 25.0%
        self.assertEqual(stats["recovery_rate_percentage"], 25.0)

    # 8. Duplicate payment does not change recovered revenue
    def test_08_duplicate_payment_does_not_double_count(self):
        c, _ = create_or_get_recovery_case({
            "order_id": "order_dup_kpi",
            "amount": 589.0,
            "currency": "INR",
            "payment_status": "failed",
            "risk_status": "at_risk",
        }, db_path=self.db_path)

        # First capture
        reconcile_recovery_payment(
            c["id"],
            recovered_payment_id="pay_first_589",
            recovered_amount=589.0,
            db_path=self.db_path,
        )

        # Duplicate capture
        reconcile_recovery_payment(
            c["id"],
            recovered_payment_id="pay_second_589",
            recovered_amount=589.0,
            db_path=self.db_path,
        )

        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["recovered_revenue"], 589.0)  # NOT 1178.0
        self.assertEqual(stats["total_revenue_at_risk"], 0.0)
        self.assertEqual(stats["recovery_rate_percentage"], 100.0)

    # 9. Existing Payment Link discovery stores the official short_url
    @patch("app.recovery_executor.get_razorpay_client")
    def test_09_existing_link_discovery_fetches_official_short_url(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.payment_link.fetch.return_value = {
            "id": "plink_official_001",
            "short_url": "https://rzp.io/rzp/official_slug_123",
            "status": "created",
        }
        mock_get_client.return_value = mock_client

        url = fetch_payment_link_url("plink_official_001")
        self.assertEqual(url, "https://rzp.io/rzp/official_slug_123")
        mock_client.payment_link.fetch.assert_called_once_with("plink_official_001")

    # 10. No synthetic rzp.io URL is ever constructed
    def test_10_no_synthetic_url_constructed_for_invalid_input(self):
        self.assertIsNone(fetch_payment_link_url(None))
        self.assertIsNone(fetch_payment_link_url(""))
        self.assertIsNone(fetch_payment_link_url("invalid_id_not_plink"))

    # 11. Missing short_url is handled safely
    @patch("app.recovery_executor.get_razorpay_client")
    def test_11_missing_short_url_handled_safely(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.payment_link.fetch.side_effect = Exception("Razorpay API 404 Link Not Found")
        mock_get_client.return_value = mock_client

        url = fetch_payment_link_url("plink_non_existent")
        self.assertIsNone(url)


if __name__ == "__main__":
    unittest.main()
