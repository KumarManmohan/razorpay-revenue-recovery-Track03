import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
import json
from app.database import get_dashboard_stats

def main():
    conn = sqlite3.connect("data/recovery.db")
    conn.row_factory = sqlite3.Row

    stats = get_dashboard_stats()

    at_risk = conn.execute("SELECT COALESCE(SUM(amount), 0.0) AS total FROM recovery_cases WHERE risk_status = 'at_risk'").fetchone()['total']
    recovered_executed = conn.execute("SELECT COALESCE(SUM(COALESCE(recovered_amount, amount)), 0.0) AS total FROM recovery_cases WHERE execution_status IN ('recovered', 'executed')").fetchone()['total']
    recovered_only = conn.execute("SELECT COALESCE(SUM(recovered_amount), 0.0) AS total FROM recovery_cases WHERE execution_status = 'recovered'").fetchone()['total']
    executed_only = conn.execute("SELECT COALESCE(SUM(amount), 0.0) AS total FROM recovery_cases WHERE execution_status = 'executed'").fetchone()['total']

    print("=== 1. CURRENT DASHBOARD KPI METRICS ===")
    print(json.dumps(stats, indent=2))

    print("\n=== 2. FINANCIAL DISSECTION ===")
    print(f"Total Revenue at Risk: Rs. {at_risk:.2f}")
    print(f"Reported Recovered Revenue (recovered + executed): Rs. {recovered_executed:.2f}")
    print(f"Genuine Recovered Revenue (status == 'recovered'): Rs. {recovered_only:.2f}")
    print(f"Prematurely Counted Revenue (status == 'executed'): Rs. {executed_only:.2f}")
    print(f"Reported Recovery Rate: {stats['recovery_rate_percentage']}%")
    print(f"Genuine Recovery Rate: {round((recovered_only / at_risk) * 100.0, 1)}%")

    print("\n=== 3. CASES CURRENTLY IN 'executed' (Payment Link Created, Not Yet Paid) ===")
    executed_cases = conn.execute("SELECT id, amount, execution_status, payment_link_id, payment_link_url, recovered_amount, recovered_payment_id, created_at FROM recovery_cases WHERE execution_status = 'executed'").fetchall()
    for row in executed_cases:
        print(dict(row))

    print("\n=== 4. CASES CURRENTLY IN 'recovered' (Genuinely Paid & Reconciled) ===")
    recovered_cases = conn.execute("SELECT id, amount, execution_status, payment_link_id, recovered_amount, recovered_payment_id, recovered_at FROM recovery_cases WHERE execution_status = 'recovered'").fetchall()
    for row in recovered_cases:
        print(dict(row))

if __name__ == "__main__":
    main()
