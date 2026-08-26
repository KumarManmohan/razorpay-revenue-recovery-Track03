"""
Phase 14 — Real Razorpay Test Mode Webhook Integration Tests

Tests realistic Razorpay webhook payloads against the existing recovery architecture.
Covers:
  - Realistic payment.failed payload parsing
  - HMAC-SHA256 signature verification
  - Missing/invalid signature rejection
  - Recovery case creation from webhook
  - Failure classification from realistic error codes
  - Payment link association
  - Successful reconciliation from payment.captured payload
  - Duplicate webhook idempotency
  - Unmatched payment rejection
"""

import hashlib
import hmac
import json
import os
import tempfile
import unittest

from app.database import (
    init_db,
    create_or_get_recovery_case,
    update_recovery_decision,
    update_execution_status,
    get_case_by_id,
    get_case_with_audit,
    reconcile_recovery_payment,
    add_audit_event,
)
from app.razorpay_client import verify_webhook_signature
from app.revenue_risk import analyze_payment_failure
from app.failure_classifier import classify_payment_failure
from app.recovery_decision import decide_recovery_action
from app.ai_recovery_agent import ai_decide_recovery_action


def build_realistic_payment_failed_payload(
    payment_id="pay_TESTREAL001",
    amount_paise=150000,
    currency="INR",
    error_code="BAD_REQUEST_ERROR",
    error_description="Your payment didn't go through due to insufficient balance in your account. Try using another payment method.",
    order_id="order_TESTREAL001",
    email="testuser@example.com",
    contact="+919876543210",
    method="card",
    event_id="evt_TESTREAL001",
):
    """Builds a realistic Razorpay payment.failed webhook payload matching actual Razorpay structure."""
    return {
        "entity": "event",
        "account_id": "acc_TestAccount",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": amount_paise,
                    "currency": currency,
                    "status": "failed",
                    "order_id": order_id,
                    "invoice_id": None,
                    "international": False,
                    "method": method,
                    "amount_refunded": 0,
                    "refund_status": None,
                    "captured": False,
                    "description": "Test Order Payment",
                    "card_id": "card_TestCard001",
                    "bank": None,
                    "wallet": None,
                    "vpa": None,
                    "email": email,
                    "contact": contact,
                    "customer_id": None,
                    "notes": [],
                    "fee": None,
                    "tax": None,
                    "error_code": error_code,
                    "error_description": error_description,
                    "error_source": "customer",
                    "error_step": "payment_authorization",
                    "error_reason": "payment_failed",
                    "acquirer_data": {
                        "auth_code": None
                    },
                    "created_at": 1692000000
                }
            }
        },
        "created_at": 1692000000,
        "id": event_id,
    }


def build_realistic_payment_captured_payload(
    payment_id="pay_TESTCAP001",
    amount_paise=150000,
    currency="INR",
    risk_case_id="case_pay_TESTREAL001",
    plink_id="plink_TEST001",
    event_id="evt_TESTCAP001",
):
    """Builds a realistic Razorpay payment.captured webhook payload for reconciliation."""
    return {
        "entity": "event",
        "account_id": "acc_TestAccount",
        "event": "payment.captured",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": amount_paise,
                    "currency": currency,
                    "status": "captured",
                    "order_id": None,
                    "invoice_id": None,
                    "international": False,
                    "method": "card",
                    "amount_refunded": 0,
                    "captured": True,
                    "description": "[TEST RECOVERY] Recovery link",
                    "email": "testuser@example.com",
                    "contact": "+919876543210",
                    "payment_link_id": plink_id,
                    "notes": {
                        "purpose": "revenue_recovery_test",
                        "risk_case_id": risk_case_id,
                        "managed_by": "ai_revenue_recovery_agent",
                        "mode": "test",
                    },
                    "fee": 3540,
                    "tax": 540,
                    "error_code": None,
                    "error_description": None,
                    "created_at": 1692001000,
                }
            },
            "payment_link": {
                "entity": {
                    "id": plink_id,
                    "amount": amount_paise,
                    "amount_paid": amount_paise,
                    "currency": currency,
                    "status": "paid",
                    "notes": {
                        "purpose": "revenue_recovery_test",
                        "risk_case_id": risk_case_id,
                        "managed_by": "ai_revenue_recovery_agent",
                    },
                }
            },
        },
        "created_at": 1692001000,
        "id": event_id,
    }


def compute_hmac_signature(body_bytes: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 hex digest matching Razorpay's signing method."""
    return hmac.new(
        secret.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()


class TestRealisticWebhookSignatureVerification(unittest.TestCase):
    """Test HMAC-SHA256 signature verification with realistic payloads."""

    def setUp(self):
        self.webhook_secret = "whsec_test_realistic_secret_2026"
        self.payload = build_realistic_payment_failed_payload()
        self.body_bytes = json.dumps(self.payload, separators=(",", ":")).encode("utf-8")

    def test_valid_signature_accepted(self):
        """Correct HMAC signature is accepted."""
        signature = compute_hmac_signature(self.body_bytes, self.webhook_secret)
        result = verify_webhook_signature(self.body_bytes, signature, self.webhook_secret)
        self.assertTrue(result)

    def test_invalid_signature_rejected(self):
        """Wrong HMAC signature is rejected."""
        result = verify_webhook_signature(self.body_bytes, "deadbeef" * 8, self.webhook_secret)
        self.assertFalse(result)

    def test_missing_signature_rejected(self):
        """Empty/None signature is rejected."""
        self.assertFalse(verify_webhook_signature(self.body_bytes, "", self.webhook_secret))
        self.assertFalse(verify_webhook_signature(self.body_bytes, None, self.webhook_secret))

    def test_missing_secret_rejected(self):
        """Empty/None secret is rejected."""
        sig = compute_hmac_signature(self.body_bytes, self.webhook_secret)
        self.assertFalse(verify_webhook_signature(self.body_bytes, sig, ""))
        self.assertFalse(verify_webhook_signature(self.body_bytes, sig, None))

    def test_tampered_body_rejected(self):
        """If the body is tampered after signing, verification fails."""
        signature = compute_hmac_signature(self.body_bytes, self.webhook_secret)
        tampered_body = self.body_bytes + b"TAMPERED"
        result = verify_webhook_signature(tampered_body, signature, self.webhook_secret)
        self.assertFalse(result)

    def test_wrong_secret_rejected(self):
        """Signature computed with a different secret is rejected."""
        signature = compute_hmac_signature(self.body_bytes, "wrong_secret_999")
        result = verify_webhook_signature(self.body_bytes, signature, self.webhook_secret)
        self.assertFalse(result)


class TestRealisticPayloadParsing(unittest.TestCase):
    """Test that realistic Razorpay payment.failed payloads parse correctly."""

    def test_parse_insufficient_funds_payload(self):
        """Realistic insufficient-funds payload is classified correctly."""
        payload = build_realistic_payment_failed_payload(
            payment_id="pay_INS001",
            amount_paise=150000,
            error_description="Your payment didn't go through due to insufficient balance in your account.",
        )
        result = analyze_payment_failure(payload)

        self.assertEqual(result["payment_id"], "pay_INS001")
        self.assertEqual(result["amount"], 1500.0)
        self.assertEqual(result["currency"], "INR")
        self.assertEqual(result["payment_status"], "failed")
        self.assertEqual(result["risk_status"], "at_risk")
        self.assertIn("insufficient", result["risk_reason"].lower())
        self.assertEqual(result["failure_category"], "INSUFFICIENT_FUNDS")

    def test_parse_card_expired_payload(self):
        """Realistic card-expired payload is classified correctly."""
        payload = build_realistic_payment_failed_payload(
            payment_id="pay_EXP001",
            amount_paise=250000,
            error_description="The card is expired. Please use another card.",
        )
        result = analyze_payment_failure(payload)
        self.assertEqual(result["payment_id"], "pay_EXP001")
        self.assertEqual(result["amount"], 2500.0)
        self.assertEqual(result["failure_category"], "CARD_EXPIRED")

    def test_parse_authentication_failed_payload(self):
        """Realistic 3DS authentication failure is classified correctly."""
        payload = build_realistic_payment_failed_payload(
            payment_id="pay_AUTH001",
            amount_paise=500000,
            error_description="Payment was not authorized by the customer. Authentication failed.",
        )
        result = analyze_payment_failure(payload)
        self.assertEqual(result["payment_id"], "pay_AUTH001")
        self.assertEqual(result["amount"], 5000.0)
        self.assertEqual(result["failure_category"], "AUTHENTICATION_REQUIRED")

    def test_parse_bank_declined_payload(self):
        """Realistic bank declined payload is classified correctly."""
        payload = build_realistic_payment_failed_payload(
            payment_id="pay_BANK001",
            amount_paise=300000,
            error_code="BAD_REQUEST_ERROR",
            error_description="The transaction has been declined by the issuing bank. Contact your bank for details.",
        )
        result = analyze_payment_failure(payload)
        self.assertEqual(result["payment_id"], "pay_BANK001")
        self.assertEqual(result["amount"], 3000.0)
        self.assertIn(result["failure_category"], ["BANK_DECLINED"])

    def test_parse_gateway_timeout_payload(self):
        """Realistic gateway timeout payload triggers WAIT strategy."""
        payload = build_realistic_payment_failed_payload(
            payment_id="pay_GW001",
            amount_paise=75000,
            error_description="Payment processing failed due to a gateway timeout. Please retry.",
        )
        result = analyze_payment_failure(payload)
        self.assertEqual(result["failure_category"], "TEMPORARY_GATEWAY_ERROR")

    def test_event_id_extraction(self):
        """The event_id is correctly extracted from realistic payloads."""
        payload = build_realistic_payment_failed_payload(event_id="evt_REAL123")
        result = analyze_payment_failure(payload)
        self.assertEqual(result["event_id"], "evt_REAL123")

    def test_paise_to_rupees_conversion(self):
        """Amount in paise (smallest unit) is correctly converted to rupees."""
        payload = build_realistic_payment_failed_payload(amount_paise=99950)
        result = analyze_payment_failure(payload)
        self.assertEqual(result["amount"], 999.50)
        self.assertEqual(result["amount_raw_paise"], 99950)

    def test_customer_contact_extraction(self):
        """Customer email/contact is extracted from payment entity."""
        payload = build_realistic_payment_failed_payload(
            email="customer@test.com",
            contact="+911234567890",
        )
        result = analyze_payment_failure(payload)
        # customer_id falls through to email or contact
        self.assertIn(result["customer_id"], ["customer@test.com", "+911234567890"])


class TestRealisticCaseCreationAndDecision(unittest.TestCase):
    """Test end-to-end case creation and AI decision from realistic payloads."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_real_webhook.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_case_creation_from_realistic_webhook(self):
        """A recovery case is created from a realistic Razorpay webhook payload."""
        payload = build_realistic_payment_failed_payload(
            payment_id="pay_REALCASE001",
            amount_paise=200000,
            error_description="Your payment didn't go through due to insufficient balance.",
            event_id="evt_REALCASE001",
        )
        risk_analysis = analyze_payment_failure(payload)
        case, is_new = create_or_get_recovery_case(risk_analysis, db_path=self.db_path)

        self.assertTrue(is_new)
        self.assertEqual(case["payment_id"], "pay_REALCASE001")
        self.assertEqual(case["amount"], 2000.0)
        self.assertEqual(case["currency"], "INR")
        self.assertEqual(case["risk_status"], "at_risk")
        self.assertEqual(case["event_id"], "evt_REALCASE001")

    def test_duplicate_event_idempotency(self):
        """Second webhook with the same event_id returns existing case."""
        payload = build_realistic_payment_failed_payload(
            payment_id="pay_DUPE001",
            event_id="evt_DUPE001",
        )
        risk_analysis = analyze_payment_failure(payload)

        case1, is_new1 = create_or_get_recovery_case(risk_analysis, db_path=self.db_path)
        case2, is_new2 = create_or_get_recovery_case(risk_analysis, db_path=self.db_path)

        self.assertTrue(is_new1)
        self.assertFalse(is_new2)
        self.assertEqual(case1["id"], case2["id"])

    def test_decision_engine_on_realistic_payload(self):
        """AI decision engine produces valid recovery action for realistic payload."""
        payload = build_realistic_payment_failed_payload(
            payment_id="pay_DECIDE001",
            amount_paise=350000,
            error_description="Payment failed due to card limit exceeded.",
        )
        risk_analysis = analyze_payment_failure(payload)
        decision = ai_decide_recovery_action(risk_analysis)

        self.assertIn(decision["action"], ["SEND_PAYMENT_LINK", "SEND_INVOICE", "WAIT", "NO_ACTION", "INVESTIGATE"])
        self.assertIn("confidence", decision)
        self.assertIn("reason", decision)
        self.assertIsNotNone(decision.get("decision_source"))

    def test_high_value_requires_approval(self):
        """₹50,000+ realistic payload requires human approval."""
        payload = build_realistic_payment_failed_payload(
            payment_id="pay_HIGHVAL001",
            amount_paise=7500000,  # ₹75,000
            error_description="Insufficient funds in account.",
        )
        risk_analysis = analyze_payment_failure(payload)
        decision = ai_decide_recovery_action(risk_analysis)

        self.assertTrue(decision["requires_human_approval"])


class TestRealisticReconciliation(unittest.TestCase):
    """Test reconciliation using realistic payment.captured payloads."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_real_recon.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_executed_case(self, case_id, payment_id, amount, plink_id):
        """Helper: create a recovery case in 'executed' state with payment link."""
        risk_analysis = analyze_payment_failure(
            build_realistic_payment_failed_payload(
                payment_id=payment_id,
                amount_paise=int(amount * 100),
                event_id=f"evt_{payment_id}",
            )
        )
        case, _ = create_or_get_recovery_case(risk_analysis, db_path=self.db_path)
        decision = ai_decide_recovery_action(risk_analysis)
        update_recovery_decision(case["id"], decision, db_path=self.db_path)
        update_execution_status(
            case["id"],
            {
                "status": "executed",
                "payment_link_id": plink_id,
                "payment_link_url": f"https://rzp.io/i/{plink_id}",
                "amount": amount,
            },
            db_path=self.db_path,
        )
        return case["id"]

    def test_successful_reconciliation_from_realistic_captured_payload(self):
        """Realistic payment.captured payload successfully reconciles a recovery case."""
        case_id = self._create_executed_case(
            "case_pay_RECONREAL001", "pay_RECONREAL001", 1500.0, "plink_RECONREAL001"
        )

        captured_payload = build_realistic_payment_captured_payload(
            payment_id="pay_CAPTURED_REAL001",
            amount_paise=150000,
            risk_case_id=case_id,
            plink_id="plink_RECONREAL001",
        )

        # Extract reconciliation data from realistic payload
        payment_entity = captured_payload["payload"]["payment"]["entity"]
        link_entity = captured_payload["payload"]["payment_link"]["entity"]
        notes = payment_entity.get("notes", {})
        target_key = notes.get("risk_case_id") or link_entity.get("id")
        captured_payment_id = payment_entity["id"]
        captured_amount = round(float(payment_entity["amount"]) / 100.0, 2)

        reconciled_case, status = reconcile_recovery_payment(
            case_id_or_link_id=target_key,
            recovered_payment_id=captured_payment_id,
            recovered_amount=captured_amount,
            metadata={"event_name": "payment.captured"},
            db_path=self.db_path,
        )

        self.assertIsNotNone(reconciled_case)
        self.assertEqual(reconciled_case["execution_status"], "recovered")
        self.assertEqual(reconciled_case["recovered_payment_id"], "pay_CAPTURED_REAL001")
        self.assertEqual(reconciled_case["recovered_amount"], 1500.0)

    def test_duplicate_reconciliation_is_idempotent(self):
        """Second reconciliation attempt for same case returns 'already_reconciled'."""
        case_id = self._create_executed_case(
            "case_pay_RECONDUP001", "pay_RECONDUP001", 800.0, "plink_RECONDUP001"
        )

        recon_args = {
            "case_id_or_link_id": case_id,
            "recovered_payment_id": "pay_CAP_DUP001",
            "recovered_amount": 800.0,
            "metadata": {"event_name": "payment.captured"},
        }

        case1, status1 = reconcile_recovery_payment(**recon_args, db_path=self.db_path)
        self.assertEqual(case1["execution_status"], "recovered")

        case2, status2 = reconcile_recovery_payment(**recon_args, db_path=self.db_path)
        self.assertEqual(status2, "already_reconciled")

    def test_unmatched_payment_returns_not_found(self):
        """A payment referencing unknown case_id returns 'Case not found.'."""
        result, status = reconcile_recovery_payment(
            case_id_or_link_id="case_NONEXISTENT_999",
            recovered_payment_id="pay_ORPHAN001",
            recovered_amount=500.0,
            db_path=self.db_path,
        )
        self.assertIsNone(result)
        self.assertEqual(status, "Case not found.")

    def test_reconciliation_via_payment_link_id(self):
        """Reconciliation works when matched by payment_link_id instead of case_id."""
        case_id = self._create_executed_case(
            "case_pay_PLINKMATCH001", "pay_PLINKMATCH001", 2500.0, "plink_MATCH001"
        )

        result, status = reconcile_recovery_payment(
            case_id_or_link_id="plink_MATCH001",
            recovered_payment_id="pay_CAPMATCH001",
            recovered_amount=2500.0,
            metadata={"event_name": "payment_link.paid"},
            db_path=self.db_path,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["execution_status"], "recovered")
        self.assertEqual(result["recovered_amount"], 2500.0)


class TestRealisticFailureClassification(unittest.TestCase):
    """Test failure classifier with realistic Razorpay error strings."""

    def test_razorpay_insufficient_balance_string(self):
        """Razorpay's exact insufficient balance error string is classified."""
        result = classify_payment_failure(
            error_code="BAD_REQUEST_ERROR",
            error_description="Your payment didn't go through due to insufficient balance in your account. Try using another payment method.",
        )
        self.assertEqual(result["category"], "INSUFFICIENT_FUNDS")
        self.assertEqual(result["action"], "SEND_PAYMENT_LINK")

    def test_razorpay_card_expired_string(self):
        """Razorpay's card expired error is classified."""
        result = classify_payment_failure(
            error_code="BAD_REQUEST_ERROR",
            error_description="The card is expired. Please use another card.",
        )
        self.assertEqual(result["category"], "CARD_EXPIRED")

    def test_razorpay_3ds_authentication_string(self):
        """Razorpay's authentication error is classified."""
        result = classify_payment_failure(
            error_code="BAD_REQUEST_ERROR",
            error_description="Payment was not authorized by the customer. Customer may have cancelled the authentication or there might be some issue with the account.",
        )
        self.assertEqual(result["category"], "AUTHENTICATION_REQUIRED")

    def test_razorpay_bank_refused_string(self):
        """Razorpay's bank decline error is classified."""
        result = classify_payment_failure(
            error_code="BAD_REQUEST_ERROR",
            error_description="The transaction was declined by bank. Do not honor.",
        )
        self.assertEqual(result["category"], "BANK_DECLINED")

    def test_razorpay_fraud_risk_string(self):
        """Razorpay fraud/blocked card error triggers NO_ACTION."""
        result = classify_payment_failure(
            error_code="BAD_REQUEST_ERROR",
            error_description="The transaction was blocked due to a fraud or suspicious activity on the card.",
        )
        self.assertEqual(result["category"], "FRAUD_OR_SECURITY")
        self.assertEqual(result["action"], "NO_ACTION")
        self.assertTrue(result["requires_human_approval"])

    def test_razorpay_timeout_string(self):
        """Razorpay gateway timeout error triggers WAIT."""
        result = classify_payment_failure(
            error_code="GATEWAY_ERROR",
            error_description="Payment processing failed due to a timeout at the payment gateway. Please try after some time.",
        )
        # Contains "timeout" and "gateway" → TEMPORARY_GATEWAY_ERROR
        self.assertEqual(result["category"], "TEMPORARY_GATEWAY_ERROR")
        self.assertEqual(result["action"], "WAIT")


if __name__ == "__main__":
    unittest.main()
