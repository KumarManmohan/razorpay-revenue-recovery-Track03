#!/usr/bin/env python3
"""
scripts/seed_demo_data.py

Deterministic Seed and Reset Utility for Phase 13 Demo Dataset.
Seeds safe, categorized test scenarios covering all 9 failure categories,
high-value approval cases, and recovered payment flows.

Usage:
    python scripts/seed_demo_data.py          # Seed/Reset demo dataset
    python scripts/seed_demo_data.py --reset  # Reset/Purge only demo dataset
"""

import sys
import os
import argparse

# Ensure project root is on PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.demo_dataset import seed_demo_dataset, reset_demo_dataset, DEMO_CASES


def main():
    parser = argparse.ArgumentParser(description="Seed or reset the Razorpay AI Recovery demo dataset.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Purge existing demo cases without re-seeding",
    )
    args = parser.parse_args()

    if args.reset:
        deleted = reset_demo_dataset()
        print(f"[OK] Demo dataset reset successfully. Deleted {deleted} demo records.")
        return

    print(">>> Seeding Phase 13 Demo Dataset...")
    result = seed_demo_dataset(reset_first=True)
    print(f"[OK] Successfully seeded {result['cases_seeded']} demo recovery cases and {result['events_seeded']} audit events.")
    print("\nScenario Coverage:")
    for c in DEMO_CASES:
        status_note = f"[{c['execution_status'].upper()}]"
        recov_note = f" (Rs.{c['recovered_amount']} recovered)" if c['recovered_amount'] else ""
        print(f" - {c['failure_category_label']:<24} | Action: {c['decision_action']:<18} | Amount: Rs.{c['amount']:>8.2f} {status_note}{recov_note}")



if __name__ == "__main__":
    main()
