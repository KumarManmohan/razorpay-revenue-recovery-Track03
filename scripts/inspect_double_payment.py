import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from app.config import settings
from app.razorpay_client import get_razorpay_client
from app.database import get_dashboard_stats

def main():
    conn = sqlite3.connect("data/recovery.db")
    conn.row_factory = sqlite3.Row

    print("==================================================")
    print("1. RECOVERY CASES MATCHING TTJ")
    print("==================================================")
    cases = conn.execute("SELECT * FROM recovery_cases WHERE id LIKE '%TTJ%' OR order_id LIKE '%TTJ%' OR payment_link_id LIKE '%TTJ%'").fetchall()
    case_ids = []
    for c in cases:
        d = dict(c)
        case_ids.append(d["id"])
        print(json.dumps(d, indent=2))

    print("\n==================================================")
    print("2. PAYMENT ATTEMPTS")
    print("==================================================")
    attempts = conn.execute("SELECT * FROM payment_attempts").fetchall()
    for a in attempts:
        d = dict(a)
        if any(term in str(d) for term in ["TTJ", "TTJcCYBHmCjzW7", "TTJydoZLBPvj3T", "TTJc1ucMZro9z3"] + case_ids):
            print(json.dumps(d, indent=2))

    print("\n==================================================")
    print("3. AUDIT EVENTS FOR MATCHED CASES")
    print("==================================================")
    for cid in case_ids:
        events = conn.execute("SELECT * FROM audit_events WHERE case_id = ? ORDER BY id ASC", (cid,)).fetchall()
        for ev in events:
            print(json.dumps(dict(ev), indent=2))

    print("\n==================================================")
    print("4. OPERATIONAL KPIS")
    print("==================================================")
    stats = get_dashboard_stats()
    print(json.dumps(stats, indent=2))

    print("\n==================================================")
    print("5. RAZORPAY API INSPECTION (READ-ONLY)")
    print("==================================================")
    client = get_razorpay_client()
    
    # Check link 1: plink_TTJydoZLBPvj3T
    try:
        l1 = client.payment_link.fetch("plink_TTJydoZLBPvj3T")
        print("\n--- Payment Link: plink_TTJydoZLBPvj3T ---")
        print(json.dumps(l1, indent=2))
    except Exception as e:
        print(f"Failed fetching plink_TTJydoZLBPvj3T: {e}")

    # Check link 2: plink_TTJc1ucMZro9z3
    try:
        l2 = client.payment_link.fetch("plink_TTJc1ucMZro9z3")
        print("\n--- Payment Link: plink_TTJc1ucMZro9z3 ---")
        print(json.dumps(l2, indent=2))
    except Exception as e:
        print(f"Failed fetching plink_TTJc1ucMZro9z3: {e}")

    # Check order: order_TTJcCYBHmCjzW7
    try:
        ord1 = client.order.fetch("order_TTJcCYBHmCjzW7")
        print("\n--- Order: order_TTJcCYBHmCjzW7 ---")
        print(json.dumps(ord1, indent=2))
        ord_payments = client.order.payments("order_TTJcCYBHmCjzW7")
        print("Payments for order_TTJcCYBHmCjzW7:")
        print(json.dumps(ord_payments, indent=2))
    except Exception as e:
        print(f"Failed fetching order_TTJcCYBHmCjzW7: {e}")

    # Check recent payments in Razorpay
    try:
        recent_payments = client.payment.all({"count": 10})
        print("\n--- Recent 10 Payments in Razorpay ---")
        for p in recent_payments.get("items", []):
            print(f"Payment ID: {p.get('id')} | Order: {p.get('order_id')} | Amount: {p.get('amount')/100.0} | Status: {p.get('status')} | CreatedAt: {p.get('created_at')} | Description: {p.get('description')} | Notes: {p.get('notes')}")
    except Exception as e:
        print(f"Failed fetching recent payments: {e}")

if __name__ == "__main__":
    main()
