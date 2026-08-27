import os
import sqlite3
import tempfile
import unittest
from app.config import settings
from app.database import (
    init_db,
    create_or_get_recovery_case,
    update_recovery_decision,
    update_execution_status,
    reconcile_recovery_payment,
    add_audit_event,
    get_case_by_id,
    get_all_cases,
    get_case_with_audit,
)


class TestDatabaseAndAuditTrail(unittest.TestCase):

    def setUp(self):
        # Create an isolated temporary SQLite database for each test
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_recovery.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_initialization(self):
        """Test database and tables initialize without errors."""
        conn = sqlite3.connect(self.db_path)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [t[0] for t in tables]
        conn.close()

        self.assertIn("recovery_cases", table_names)
        self.assertIn("audit_events", table_names)

    def test_insert_and_retrieve_recovery_case(self):
        """Test inserting a structured risk case and retrieving it."""
        risk_case = {
            "event_id": "evt_test_db_001",
            "payment_id": "pay_test_db_001",
            "order_id": "order_001",
            "subscription_id": "sub_001",
            "amount": 750.0,
            "currency": "INR",
            "payment_status": "failed",
            "is_recurring_revenue": True,
            "risk_status": "at_risk",
            "risk_reason": "Payment failed: Card limit exceeded",
            "error_code": "CARD_LIMIT",
            "error_description": "Card limit exceeded",
        }

        case_record, is_new = create_or_get_recovery_case(risk_case, db_path=self.db_path)
        self.assertTrue(is_new)
        self.assertEqual(case_record["payment_id"], "pay_test_db_001")
        self.assertEqual(case_record["amount"], 750.0)
        self.assertEqual(case_record["is_recurring_revenue"], 1)

        retrieved = get_case_by_id("pay_test_db_001", db_path=self.db_path)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["id"], case_record["id"])

    def test_update_recovery_decision(self):
        """Test updating a case with an AI / deterministic recovery decision."""
        risk_case = {
            "event_id": "evt_test_db_002",
            "payment_id": "pay_test_db_002",
            "amount": 500.0,
            "currency": "INR",
            "payment_status": "failed",
        }
        case_record, _ = create_or_get_recovery_case(risk_case, db_path=self.db_path)

        decision = {
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.88,
            "reason": "Payment link recommended.",
            "requires_human_approval": False,
            "decision_source": "ai_agent",
        }
        updated = update_recovery_decision(case_record["id"], decision, db_path=self.db_path)
        self.assertEqual(updated["decision_action"], "SEND_PAYMENT_LINK")
        self.assertEqual(updated["decision_confidence"], 0.88)
        self.assertEqual(updated["decision_source"], "ai_agent")
        self.assertEqual(updated["requires_human_approval"], 0)

    def test_update_execution_status(self):
        """Test updating execution status and payment link info."""
        risk_case = {
            "event_id": "evt_test_db_003",
            "payment_id": "pay_test_db_003",
            "amount": 1200.0,
        }
        case_record, _ = create_or_get_recovery_case(risk_case, db_path=self.db_path)

        exec_result = {
            "status": "executed",
            "payment_link_id": "plink_test_db_999",
            "payment_link_url": "https://rzp.io/i/testdb",
        }
        updated = update_execution_status(case_record["id"], exec_result, db_path=self.db_path)
        self.assertEqual(updated["execution_status"], "executed")
        self.assertEqual(updated["payment_link_id"], "plink_test_db_999")
        self.assertEqual(updated["payment_link_url"], "https://rzp.io/i/testdb")

    def test_audit_event_insertion_and_retrieval(self):
        """Test logging audit events and retrieving full history in order."""
        case_id = "case_audit_test_004"
        risk_case = {
            "event_id": "evt_audit_004",
            "payment_id": "pay_audit_004",
            "amount": 450.0,
        }
        case_record, _ = create_or_get_recovery_case(risk_case, db_path=self.db_path)
        actual_case_id = case_record["id"]

        add_audit_event(actual_case_id, "PAYMENT_FAILED", "Initial payment failed.", {"code": "ERR1"}, db_path=self.db_path)
        add_audit_event(actual_case_id, "RISK_ANALYZED", "Risk is at_risk.", db_path=self.db_path)
        add_audit_event(actual_case_id, "RECOVERY_DECIDED", "Decision: SEND_PAYMENT_LINK", db_path=self.db_path)
        add_audit_event(actual_case_id, "PAYMENT_LINK_CREATED", "Link created.", {"link_id": "plink_123"}, db_path=self.db_path)

        full_case = get_case_with_audit(actual_case_id, db_path=self.db_path)
        self.assertIsNotNone(full_case)
        audit_events = full_case["audit"]
        self.assertEqual(len(audit_events), 4)
        self.assertEqual(audit_events[0]["event_type"], "PAYMENT_FAILED")
        self.assertEqual(audit_events[1]["event_type"], "RISK_ANALYZED")
        self.assertEqual(audit_events[2]["event_type"], "RECOVERY_DECIDED")
        self.assertEqual(audit_events[3]["event_type"], "PAYMENT_LINK_CREATED")
        self.assertEqual(audit_events[3]["metadata"]["link_id"], "plink_123")

    def test_webhook_idempotency_duplicate_rejection(self):
        """Test that submitting the same event_id does not create duplicate cases."""
        risk_case = {
            "event_id": "evt_duplicate_test_005",
            "payment_id": "pay_duplicate_test_005",
            "amount": 900.0,
        }
        case1, is_new1 = create_or_get_recovery_case(risk_case, db_path=self.db_path)
        self.assertTrue(is_new1)

        # Re-submitting the exact same event
        case2, is_new2 = create_or_get_recovery_case(risk_case, db_path=self.db_path)
        self.assertFalse(is_new2)
        self.assertEqual(case1["id"], case2["id"])

        # Verify only 1 row exists
        all_cases = get_all_cases(db_path=self.db_path)
        self.assertEqual(len(all_cases), 1)

    def test_secrets_never_stored_in_database(self):
        """Test that secret credentials are never stored in SQLite tables."""
        risk_case = {
            "event_id": "evt_secret_test_006",
            "payment_id": "pay_secret_test_006",
            "amount": 500.0,
            "risk_reason": "Test case for secrets check",
        }
        case_record, _ = create_or_get_recovery_case(risk_case, db_path=self.db_path)
        add_audit_event(case_record["id"], "PAYMENT_FAILED", "Testing secrets", {"test": "ok"}, db_path=self.db_path)

        conn = sqlite3.connect(self.db_path)
        all_text = ""
        for row in conn.execute("SELECT * FROM recovery_cases").fetchall():
            all_text += " ".join(str(val) for val in row)
        for row in conn.execute("SELECT * FROM audit_events").fetchall():
            all_text += " ".join(str(val) for val in row)
        conn.close()

        if settings.RAZORPAY_KEY_SECRET:
            self.assertNotIn(settings.RAZORPAY_KEY_SECRET, all_text)
        if settings.RAZORPAY_WEBHOOK_SECRET:
            self.assertNotIn(settings.RAZORPAY_WEBHOOK_SECRET, all_text)
        if settings.OPENAI_API_KEY:
            self.assertNotIn(settings.OPENAI_API_KEY, all_text)

    def test_dashboard_stats_empty_database(self):
        """Test dashboard statistics calculation with an empty database."""
        from app.database import get_dashboard_stats
        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["total_cases"], 0)
        self.assertEqual(stats["total_revenue_at_risk"], 0.0)
        self.assertEqual(stats["recovered_revenue"], 0.0)
        self.assertEqual(stats["pending_approvals"], 0)
        self.assertEqual(stats["recovery_rate_percentage"], 0.0)

    def test_dashboard_stats_calculation(self):
        """Test KPI metric calculation for total at risk, recovered revenue, pending approvals, and recovery rate."""
        from app.database import get_dashboard_stats
        
        # Case 1: At risk, pending approval (₹50,000)
        c1, _ = create_or_get_recovery_case({
            "event_id": "evt_stat_01",
            "payment_id": "pay_stat_01",
            "amount": 50000.0,
            "risk_status": "at_risk",
        }, db_path=self.db_path)
        update_recovery_decision(c1["id"], {
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.85,
            "requires_human_approval": True,
        }, db_path=self.db_path)

        # Case 2: At risk, executed & recovered (₹25,000)
        c2, _ = create_or_get_recovery_case({
            "event_id": "evt_stat_02",
            "payment_id": "pay_stat_02",
            "amount": 25000.0,
            "risk_status": "at_risk",
        }, db_path=self.db_path)
        update_execution_status(c2["id"], {
            "status": "executed",
            "payment_link_id": "plink_stat_02",
            "payment_link_url": "https://rzp.io/i/stat02",
        }, db_path=self.db_path)
        reconcile_recovery_payment(
            c2["id"],
            recovered_payment_id="pay_rec_stat_02",
            recovered_amount=25000.0,
            db_path=self.db_path,
        )

        # Case 3: At risk, executed but NOT YET paid (₹25,000) -> should contribute ₹0 to recovered revenue
        c3, _ = create_or_get_recovery_case({
            "event_id": "evt_stat_03",
            "payment_id": "pay_stat_03",
            "amount": 25000.0,
            "risk_status": "at_risk",
        }, db_path=self.db_path)
        update_execution_status(c3["id"], {
            "status": "executed",
            "payment_link_id": "plink_stat_03",
            "payment_link_url": "https://rzp.io/i/stat03",
        }, db_path=self.db_path)

        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["total_cases"], 3)
        self.assertEqual(stats["total_revenue_at_risk"], 75000.0)  # Current unresolved (c1: 50k + c3: 25k)
        self.assertEqual(stats["recovered_revenue"], 25000.0)      # Genuine recovered (c2: 25k)
        self.assertEqual(stats["historical_exposure"], 100000.0)    # Total historical (75k + 25k)
        self.assertEqual(stats["pending_approvals"], 1)
        # 25,000 / 100,000 * 100 = 25.0%
        self.assertEqual(stats["recovery_rate_percentage"], 25.0)

    def test_dashboard_stats_pending_approvals_excludes_terminal_states(self):
        """Verify pending_approvals excludes exhausted, recovered, rejected, and executed cases."""
        from app.database import get_dashboard_stats, exhaust_recovery_case, reject_case

        # 1. Genuinely pending high-value case -> should be counted (1)
        c1, _ = create_or_get_recovery_case({
            "event_id": "evt_pend_01",
            "payment_id": "pay_pend_01",
            "amount": 60000.0,
            "risk_status": "at_risk",
        }, db_path=self.db_path)
        update_recovery_decision(c1["id"], {
            "action": "SEND_PAYMENT_LINK",
            "requires_human_approval": True,
        }, db_path=self.db_path)

        # 2. Case with requires_human_approval=True but exhausted -> must NOT be counted
        c2, _ = create_or_get_recovery_case({
            "event_id": "evt_pend_02",
            "payment_id": "pay_pend_02",
            "amount": 750.0,
            "risk_status": "at_risk",
        }, db_path=self.db_path)
        update_recovery_decision(c2["id"], {
            "action": "NO_ACTION",
            "requires_human_approval": True,
        }, db_path=self.db_path)
        exhaust_recovery_case(c2["id"], reason="3 attempts failed", db_path=self.db_path)

        # 3. Case with requires_human_approval=True but recovered -> must NOT be counted
        c3, _ = create_or_get_recovery_case({
            "event_id": "evt_pend_03",
            "payment_id": "pay_pend_03",
            "amount": 50000.0,
            "risk_status": "at_risk",
        }, db_path=self.db_path)
        update_recovery_decision(c3["id"], {
            "action": "SEND_PAYMENT_LINK",
            "requires_human_approval": True,
        }, db_path=self.db_path)
        reconcile_recovery_payment(c3["id"], recovered_payment_id="pay_rec_03", recovered_amount=50000.0, db_path=self.db_path)

        # 4. Case with requires_human_approval=True but rejected -> must NOT be counted
        c4, _ = create_or_get_recovery_case({
            "event_id": "evt_pend_04",
            "payment_id": "pay_pend_04",
            "amount": 55000.0,
            "risk_status": "at_risk",
        }, db_path=self.db_path)
        update_recovery_decision(c4["id"], {
            "action": "SEND_PAYMENT_LINK",
            "requires_human_approval": True,
        }, db_path=self.db_path)
        reject_case(c4["id"], approver="admin", reason="Fraud suspicion", db_path=self.db_path)

        stats = get_dashboard_stats(db_path=self.db_path)
        self.assertEqual(stats["pending_approvals"], 1)


if __name__ == "__main__":
    unittest.main()

