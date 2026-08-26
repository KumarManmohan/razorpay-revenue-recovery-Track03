import re
from typing import Any, Dict, Optional
from app.failure_classifier import classify_payment_failure


def extract_payment_link_id(
    payment_entity: Dict[str, Any],
    payload_obj: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Discovers an associated Razorpay Payment Link ID from payment entity / webhook payload.
    Safe hierarchy:
    1. Explicit 'payment_link_id' in payment entity or payload.
    2. Explicit 'id' in payment_link entity if present in payload.
    3. 'payment_link_id' or 'plink_id' in payment notes.
    4. Validated description parser: Razorpay Payment Link checkouts default description to '#<link_id_suffix>'.
       Matches e.g. '#TTJc1ucMZro9z3' -> 'plink_TTJc1ucMZro9z3' or '#plink_TTJc1ucMZro9z3' -> 'plink_TTJc1ucMZro9z3'.
       Never matches arbitrary text.
    """
    if not isinstance(payment_entity, dict):
        return None

    # 1. Explicit payment_link_id
    explicit_id = payment_entity.get("payment_link_id")
    if explicit_id and isinstance(explicit_id, str) and explicit_id.startswith("plink_"):
        return explicit_id

    # 2. Payment Link entity in payload
    if payload_obj and isinstance(payload_obj, dict):
        plink_entity = payload_obj.get("payment_link", {}).get("entity", {})
        if isinstance(plink_entity, dict) and plink_entity.get("id"):
            link_id = plink_entity.get("id")
            if isinstance(link_id, str) and link_id.startswith("plink_"):
                return link_id

    # 3. Notes
    notes = payment_entity.get("notes") or {}
    if isinstance(notes, dict):
        notes_link_id = notes.get("payment_link_id") or notes.get("plink_id")
        if notes_link_id and isinstance(notes_link_id, str) and notes_link_id.startswith("plink_"):
            return notes_link_id

    # 4. Validated Description Parser
    desc = payment_entity.get("description")
    if desc and isinstance(desc, str):
        desc = desc.strip()
        # Case A: Full plink ID like "#plink_TTJc1ucMZro9z3"
        match_full = re.match(r"^#(plink_[a-zA-Z0-9]{14,24})$", desc)
        if match_full:
            return match_full.group(1)
        # Case B: Suffix like "#TTJc1ucMZro9z3" (exact 14 alphanumeric chars observed in Razorpay)
        match_suffix = re.match(r"^#([a-zA-Z0-9]{14})$", desc)
        if match_suffix:
            return f"plink_{match_suffix.group(1)}"

    return None


def analyze_payment_failure(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministically analyzes a Razorpay payment failure event and extracts
    a structured revenue-risk case.
    
    Handles both full Razorpay webhook payloads and isolated payment entities.
    """
    # 1. Extract Event ID
    event_id: Optional[str] = (
        event_data.get("id")
        or event_data.get("event_id")
        or event_data.get("x_razorpay_event_id")
    )

    # 2. Locate Payment Entity within the payload
    # Webhooks structure: payload.payment.entity
    payload_obj = event_data.get("payload", {})
    payment_obj = payload_obj.get("payment", {}) if isinstance(payload_obj, dict) else {}
    
    if isinstance(payment_obj, dict) and "entity" in payment_obj:
        payment_entity = payment_obj["entity"]
    elif isinstance(payment_obj, dict) and payment_obj:
        payment_entity = payment_obj
    elif "entity" in event_data:
        payment_entity = event_data["entity"]
    else:
        # Fallback if the top-level dict itself is the payment entity
        payment_entity = event_data

    if not isinstance(payment_entity, dict):
        payment_entity = {}

    # 3. Extract Payment Fields
    payment_id = payment_entity.get("id")
    raw_amount = payment_entity.get("amount")
    currency = payment_entity.get("currency", "INR")
    payment_status = payment_entity.get("status")
    order_id = payment_entity.get("order_id")
    subscription_id = payment_entity.get("subscription_id")
    customer_id = (
        payment_entity.get("customer_id")
        or payment_entity.get("email")
        or payment_entity.get("contact")
    )
    
    error_code = payment_entity.get("error_code")
    error_description = payment_entity.get("error_description") or payment_entity.get("error_reason")

    # Extract associated Payment Link ID if pre-existing
    payment_link_id = extract_payment_link_id(payment_entity, payload_obj if isinstance(payload_obj, dict) else None)

    # 4. Currency Conversion (Razorpay provides amounts in smallest currency units, e.g. paise for INR)
    amount_in_rupees: Optional[float] = None
    if raw_amount is not None:
        try:
            # 100 paise = ₹1.00
            amount_in_rupees = round(float(raw_amount) / 100.0, 2)
        except (ValueError, TypeError):
            amount_in_rupees = None

    # 5. Deterministic Risk Classification
    # Only classify as recurring revenue if a valid subscription_id is present
    is_recurring_revenue = bool(subscription_id)

    if raw_amount is None or amount_in_rupees is None:
        risk_status = "needs_investigation"
        risk_reason = "Missing or invalid payment amount in event payload."
        recommended_next_step = "investigate"
    elif payment_status == "failed" or event_data.get("event") == "payment.failed":
        risk_status = "at_risk"
        if error_description:
            risk_reason = f"Payment failed: {error_description}"
        elif error_code:
            risk_reason = f"Payment failed with error code: {error_code}"
        else:
            risk_reason = "Payment status is failed."
        recommended_next_step = "investigate"
    else:
        risk_status = "needs_investigation"
        risk_reason = f"Uncertain risk state for payment status: '{payment_status}'"
        recommended_next_step = "investigate"

    # 6. Intelligent Failure Category Normalization
    classification = classify_payment_failure(
        error_code=error_code,
        error_description=error_description,
        is_recurring=is_recurring_revenue,
        amount=amount_in_rupees,
    )

    return {
        "event_id": event_id,
        "payment_id": payment_id,
        "order_id": order_id,
        "subscription_id": subscription_id,
        "customer_id": customer_id,
        "payment_link_id": payment_link_id,
        "original_payment_link_id": payment_link_id,
        "amount_raw_paise": raw_amount,
        "amount": amount_in_rupees,
        "currency": currency,
        "payment_status": payment_status or "failed",
        "is_recurring_revenue": is_recurring_revenue,
        "risk_status": risk_status,
        "risk_reason": risk_reason,
        "error_code": error_code,
        "error_description": error_description,
        "failure_category": classification["category"],
        "failure_category_label": classification["category_label"],
        "recommended_next_step": recommended_next_step,
    }
