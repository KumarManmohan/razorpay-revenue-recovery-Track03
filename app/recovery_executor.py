import logging
import re
import uuid
from typing import Any, Dict, Optional
from app.config import settings
from app.razorpay_client import get_razorpay_client

logger = logging.getLogger(__name__)

# Allowed executable actions in this phase
EXECUTABLE_ACTIONS = {"SEND_PAYMENT_LINK"}
VALID_CURRENCIES = {"INR", "USD", "EUR", "GBP", "SGD", "AED"}


def _sanitize_error_message(err_msg: str) -> str:
    """Removes any accidental secret leaks from error strings."""
    if settings.RAZORPAY_KEY_SECRET:
        err_msg = err_msg.replace(settings.RAZORPAY_KEY_SECRET, "[REDACTED_SECRET]")
    return err_msg


def execute_recovery_action(decision: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a bounded recovery action (SEND_PAYMENT_LINK) in Razorpay Test Mode
    strictly after policy and safety checks.

    Args:
        decision: Dictionary produced by app.recovery_decision.decide_recovery_action.

    Returns:
        Structured execution result dictionary.
    """
    if not isinstance(decision, dict) or not decision:
        return {
            "status": "rejected",
            "action": None,
            "message": "Invalid or empty decision payload.",
            "requires_human_approval": False,
        }

    action = decision.get("action")
    requires_approval = bool(decision.get("requires_human_approval", False))
    risk_case_id = str(decision.get("risk_case_id") or decision.get("payment_id") or "unspecified")
    amount = decision.get("amount")
    currency = str(decision.get("currency", "INR")).upper()

    # 0. Terminal Exhaustion Check
    if decision.get("execution_status") == "exhausted" or decision.get("is_exhausted"):
        logger.info(f"[Execution Blocked] Case '{risk_case_id}' is in exhausted state; automated execution stopped.")
        return {
            "status": "rejected",
            "action": action,
            "message": "Automated recovery retry limit exhausted. Automated execution is permanently stopped.",
            "requires_human_approval": True,
            "risk_case_id": risk_case_id,
        }

    # 1. Action validation
    if action not in EXECUTABLE_ACTIONS:
        return {
            "status": "rejected",
            "action": action,
            "message": f"Action '{action}' is not supported for automated execution in Phase 6.",
            "requires_human_approval": requires_approval,
        }

    # 2. Safety Guardrail: Block execution if human approval is required
    if requires_approval:
        logger.info(f"[Execution Blocked] Action '{action}' for case '{risk_case_id}' requires human approval.")
        return {
            "status": "approval_required",
            "action": action,
            "requires_human_approval": True,
            "risk_case_id": risk_case_id,
            "message": "Execution blocked until human approval.",
        }

    # 3. Amount & Currency Validation
    if amount is None or not isinstance(amount, (int, float)) or amount <= 0:
        return {
            "status": "rejected",
            "action": action,
            "message": "Valid positive payment amount is required to create a payment link.",
            "risk_case_id": risk_case_id,
        }

    if currency not in VALID_CURRENCIES:
        return {
            "status": "rejected",
            "action": action,
            "message": f"Currency '{currency}' is invalid or unsupported.",
            "risk_case_id": risk_case_id,
        }

    # 4. Razorpay Amount Conversion (rupees to paise for INR)
    # Razorpay Payment Link API expects the smallest currency unit
    amount_in_subunits = int(round(float(amount) * 100))

    # 5. Build Safe Test-Mode Payment Link Payload
    # Reference ID sanitized to alphanumeric with max length 40
    clean_ref_id = re.sub(r"[^a-zA-Z0-9_-]", "", risk_case_id)[:24]
    reference_id = f"rec_{clean_ref_id}_{uuid.uuid4().hex[:6]}"

    payload = {
        "amount": amount_in_subunits,
        "currency": currency,
        "accept_partial": False,
        "description": f"[TEST RECOVERY] Recovery link for {risk_case_id}",
        "reference_id": reference_id,
        "notify": {"sms": False, "email": False},  # Do not send unsolicited messages in test mode
        "notes": {
            "purpose": "revenue_recovery_test",
            "risk_case_id": risk_case_id,
            "managed_by": "ai_revenue_recovery_agent",
            "mode": "test",
        },
    }

    # 6. Execute API call via Razorpay Test Mode Client
    try:
        client = get_razorpay_client()
        logger.info(f"[Executing Payment Link] Creating test link for amount ₹{amount:.2f} ({currency})")
        plink_response = client.payment_link.create(payload)

        # Extract only safe, necessary fields (never include credentials)
        return {
            "status": "executed",
            "action": action,
            "payment_link_id": plink_response.get("id"),
            "payment_link_url": plink_response.get("short_url"),
            "amount": amount,
            "currency": currency,
            "risk_case_id": risk_case_id,
            "reference_id": plink_response.get("reference_id"),
            "link_status": plink_response.get("status", "created"),
            "created_at": plink_response.get("created_at"),
        }

    except Exception as exc:
        sanitized_err = _sanitize_error_message(str(exc))
        logger.error(f"[Payment Link Creation Failed] Case: {risk_case_id} | Error: {sanitized_err}")
        return {
            "status": "failed",
            "action": action,
            "message": "Failed to create Razorpay Payment Link in Test Mode.",
            "error": sanitized_err,
            "risk_case_id": risk_case_id,
        }


def cancel_payment_link(link_id: str) -> Dict[str, Any]:
    """
    Safely cancels an open Razorpay Payment Link in Test Mode.
    Handles already-paid, expired, or already-cancelled links gracefully.
    """
    if not link_id or not isinstance(link_id, str) or not link_id.startswith("plink_"):
        return {"status": "skipped", "reason": "Invalid or empty link ID"}

    try:
        client = get_razorpay_client()
        res = client.payment_link.cancel(link_id)
        logger.info(f"[Payment Link Cancelled] Successfully cancelled link '{link_id}'.")
        return {"status": "cancelled", "payment_link_id": link_id, "response": res}
    except Exception as exc:
        sanitized_err = _sanitize_error_message(str(exc))
        logger.warning(f"[Payment Link Cancel Skipped/Failed] Link: {link_id} | Reason: {sanitized_err}")
        return {"status": "failed", "payment_link_id": link_id, "error": sanitized_err}


def fetch_payment_link_url(link_id: str) -> Optional[str]:
    """
    Safely retrieves the official Razorpay short_url for an existing payment link ID.
    Never invents or synthesizes a URL. Returns None if fetch fails.
    """
    if not link_id or not isinstance(link_id, str) or not link_id.startswith("plink_"):
        return None

    try:
        client = get_razorpay_client()
        res = client.payment_link.fetch(link_id)
        if isinstance(res, dict):
            return res.get("short_url") or res.get("url")
    except Exception as exc:
        sanitized_err = _sanitize_error_message(str(exc))
        logger.warning(f"[Payment Link Fetch Failed] Link: {link_id} | Error: {sanitized_err}")
    return None

