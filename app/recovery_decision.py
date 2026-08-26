from typing import Any, Dict, Optional
from app.failure_classifier import (
    classify_payment_failure,
    HIGH_VALUE_THRESHOLD,
    CATEGORY_FRAUD_OR_SECURITY,
    CATEGORY_UNKNOWN,
)

# Bounded explicit set of allowed recovery actions
ALLOWED_ACTIONS = {
    "SEND_PAYMENT_LINK",
    "SEND_INVOICE",
    "WAIT",
    "NO_ACTION",
    "INVESTIGATE",
}


def decide_recovery_action(risk_case: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates a structured revenue-risk case and deterministically recommends
    a bounded recovery action tailored to the specific failure reason.

    Args:
        risk_case: Structured risk case output from app.revenue_risk.analyze_payment_failure.

    Returns:
        Dict containing action, confidence, specific rationale, failure category, and approval requirement.
    """
    if not isinstance(risk_case, dict) or not risk_case:
        return {
            "action": "INVESTIGATE",
            "confidence": 0.50,
            "reason": "Invalid or empty risk case payload received.",
            "requires_human_approval": True,
            "risk_case_id": None,
            "amount": None,
            "currency": "INR",
            "failure_category": CATEGORY_UNKNOWN,
        }

    payment_id: Optional[str] = risk_case.get("payment_id")
    event_id: Optional[str] = risk_case.get("event_id")
    risk_case_id = payment_id or event_id or "unknown"

    amount: Optional[float] = risk_case.get("amount")
    currency: str = risk_case.get("currency", "INR")
    payment_status: Optional[str] = risk_case.get("payment_status")
    risk_status: Optional[str] = risk_case.get("risk_status")
    is_recurring: bool = bool(risk_case.get("is_recurring_revenue", False))
    error_code: Optional[str] = risk_case.get("error_code")
    error_desc: Optional[str] = risk_case.get("error_description") or risk_case.get("risk_reason")

    # Rule 0: Terminal Exhausted State or Retry Limit Exhaustion
    exec_status = risk_case.get("execution_status")
    attempts_count = int(risk_case.get("payment_attempts_count") or risk_case.get("attempts_count") or 0)
    prior_links = int(risk_case.get("prior_recovery_links_count") or 0)
    link_age_hours = float(risk_case.get("link_age_hours") or risk_case.get("hours_since_link_created") or 0.0)

    from app.config import settings
    is_exhausted = (
        exec_status == "exhausted"
        or (attempts_count >= settings.MAX_FAILED_ATTEMPTS and exec_status != "recovered")
        or (prior_links >= settings.MAX_IGNORED_RECOVERY_LINKS and link_age_hours >= settings.IGNORED_RECOVERY_TIMEOUT_HOURS and exec_status != "recovered")
    )

    if is_exhausted:
        return {
            "action": "NO_ACTION",
            "confidence": 1.0,
            "reason": f"Automated recovery retry limit exhausted (attempts={attempts_count}, ignored_links={prior_links}). Automated recovery permanently stopped; manual merchant escalation required.",
            "requires_human_approval": True,
            "risk_case_id": risk_case_id,
            "amount": amount,
            "currency": currency,
            "failure_category": "RECOVERY_EXHAUSTED",
            "failure_category_label": "Recovery Exhausted",
        }

    # Rule 1: Missing or invalid amount / needs investigation
    if amount is None or amount <= 0 or risk_status == "needs_investigation":
        return {
            "action": "INVESTIGATE",
            "confidence": 0.60,
            "reason": "Payment amount or critical failure metadata is missing or indeterminate. Investigation required.",
            "requires_human_approval": True,
            "risk_case_id": risk_case_id,
            "amount": amount,
            "currency": currency,
            "failure_category": CATEGORY_UNKNOWN,
        }

    # Rule 2: Payment status is not failed (no revenue recovery required)
    if payment_status in ("captured", "authorized", "refunded", "success", "paid") or (
        payment_status not in ("failed", None) and risk_status not in ("at_risk", None)
    ):
        return {
            "action": "NO_ACTION",
            "confidence": 1.0,
            "reason": f"Payment is not in a failed state (status: '{payment_status}').",
            "requires_human_approval": False,
            "risk_case_id": risk_case_id,
            "amount": amount,
            "currency": currency,
            "failure_category": "NOT_FAILED",
        }


    # Rule 3: Intelligent classification based on actual failure reason
    classification = classify_payment_failure(
        error_code=error_code,
        error_description=error_desc,
        is_recurring=is_recurring,
        amount=amount,
    )

    # Rule 4: Mandatory High-Value threshold check (Policy rule overrides automated approval)
    requires_human_approval = classification["requires_human_approval"]
    if amount is not None and amount >= HIGH_VALUE_THRESHOLD:
        requires_human_approval = True

    return {
        "action": classification["action"],
        "confidence": classification["confidence"],
        "reason": classification["reason"],
        "requires_human_approval": requires_human_approval,
        "risk_case_id": risk_case_id,
        "amount": amount,
        "currency": currency,
        "failure_category": classification["category"],
        "failure_category_label": classification["category_label"],
    }
