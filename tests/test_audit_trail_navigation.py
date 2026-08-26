import os
import tempfile
import unittest
from app.main import list_system_audit_events
from app.database import init_db, add_audit_event, get_all_audit_events, create_or_get_recovery_case


class TestAuditTrailNavigation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_nav_audit.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_all_audit_events_function(self):
        """Test retrieving chronological audit events from SQLite."""
        case_id = "case_nav_test_001"
        add_audit_event(case_id, "PAYMENT_FAILED", "Initial failure", {"amount": 1000}, db_path=self.db_path)
        add_audit_event(case_id, "RECOVERY_DECIDED", "Decision made", {"action": "WAIT"}, db_path=self.db_path)

        events = get_all_audit_events(db_path=self.db_path)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "RECOVERY_DECIDED")  # Ordered DESC
        self.assertEqual(events[1]["event_type"], "PAYMENT_FAILED")

    def test_get_all_audit_events_filter_by_case(self):
        """Test filtering audit events by case_id."""
        add_audit_event("case_A", "PAYMENT_FAILED", "Failure A", db_path=self.db_path)
        add_audit_event("case_B", "PAYMENT_FAILED", "Failure B", db_path=self.db_path)

        events_a = get_all_audit_events(case_id="case_A", db_path=self.db_path)
        self.assertEqual(len(events_a), 1)
        self.assertEqual(events_a[0]["case_id"], "case_A")

    def test_list_system_audit_events_endpoint(self):
        """Test list_system_audit_events endpoint return format."""
        add_audit_event("case_endpoint_test", "TEST_EVENT", "Testing endpoint return")
        data = list_system_audit_events(limit=50)
        self.assertEqual(data["status"], "success")
        self.assertIn("events", data)
        self.assertIsInstance(data["events"], list)
        self.assertGreaterEqual(data["count"], 1)


if __name__ == "__main__":
    unittest.main()
