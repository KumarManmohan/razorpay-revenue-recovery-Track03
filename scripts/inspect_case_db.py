import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from app.database import get_dashboard_stats

conn = sqlite3.connect("data/recovery.db")
conn.row_factory = sqlite3.Row

print("==================================================")
print("1. RECOVERY CASE (case_order_TTJcCYBHmCjzW7)")
print("==================================================")
row = conn.execute("SELECT * FROM recovery_cases WHERE id = 'case_order_TTJcCYBHmCjzW7'").fetchone()
if row:
    print(json.dumps(dict(row), indent=2))
else:
    print("Case not found! Listing all cases with TTJ:")
    for r in conn.execute("SELECT * FROM recovery_cases WHERE id LIKE '%TTJ%' OR order_id LIKE '%TTJ%'").fetchall():
        print(json.dumps(dict(r), indent=2))

print("\n==================================================")
print("2. PAYMENT ATTEMPTS FOR THIS CASE")
print("==================================================")
for a in conn.execute("SELECT * FROM payment_attempts WHERE case_id = 'case_order_TTJcCYBHmCjzW7' ORDER BY created_at ASC").fetchall():
    print(json.dumps(dict(a), indent=2))

print("\n==================================================")
print("3. AUDIT EVENTS FOR THIS CASE")
print("==================================================")
for ev in conn.execute("SELECT * FROM audit_events WHERE case_id = 'case_order_TTJcCYBHmCjzW7' ORDER BY id ASC").fetchall():
    print(json.dumps(dict(ev), indent=2))

print("\n==================================================")
print("4. OPERATIONAL KPIS")
print("==================================================")
print(json.dumps(get_dashboard_stats(), indent=2))
