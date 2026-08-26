import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.razorpay_client import get_razorpay_client

client = get_razorpay_client()

print("=== PAYMENT LINK 1: plink_TTJydoZLBPvj3T (Recovery Link) ===")
l1 = client.payment_link.fetch("plink_TTJydoZLBPvj3T")
print(json.dumps(l1, indent=2))

print("\n=== PAYMENT LINK 2: plink_TTJc1ucMZro9z3 (Original/Pre-existing Link) ===")
l2 = client.payment_link.fetch("plink_TTJc1ucMZro9z3")
print(json.dumps(l2, indent=2))
