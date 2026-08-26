import re
from typing import Any, Dict, Optional

# Standard Normalized Failure Categories
CATEGORY_INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
CATEGORY_CARD_LIMIT_EXCEEDED = "CARD_LIMIT_EXCEEDED"
CATEGORY_CARD_EXPIRED = "CARD_EXPIRED"
CATEGORY_INVALID_CARD = "INVALID_CARD"
CATEGORY_AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
CATEGORY_BANK_DECLINED = "BANK_DECLINED"
CATEGORY_TEMPORARY_GATEWAY_ERROR = "TEMPORARY_GATEWAY_ERROR"
CATEGORY_FRAUD_OR_SECURITY = "FRAUD_OR_SECURITY"
CATEGORY_UNKNOWN = "UNKNOWN"

# Category Labels for Dashboard Display
CATEGORY_LABELS = {
    CATEGORY_INSUFFICIENT_FUNDS: "Insufficient Funds",
    CATEGORY_CARD_LIMIT_EXCEEDED: "Card Limit Exceeded",
    CATEGORY_CARD_EXPIRED: "Card Expired",
    CATEGORY_INVALID_CARD: "Invalid Card Details",
    CATEGORY_AUTHENTICATION_REQUIRED: "3DS / Auth Required",
    CATEGORY_BANK_DECLINED: "Bank Declined",
    CATEGORY_TEMPORARY_GATEWAY_ERROR: "Gateway Error / Timeout",
    CATEGORY_FRAUD_OR_SECURITY: "Security / Fraud Risk",
    CATEGORY_UNKNOWN: "Unknown Failure Reason",
}

# High amount threshold (in INR) requiring mandatory human approval
HIGH_VALUE_THRESHOLD = 50000.0


def classify_payment_failure(
    error_code: Optional[str] = None,
    error_description: Optional[str] = None,
    is_recurring: bool = False,
    amount: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Intelligently normalizes payment failure errors into distinct categories
    and recommends tailored, bounded recovery strategies with specific explanations.

    Args:
        error_code: Gateway error code (e.g. 'BAD_REQUEST_ERROR', 'CARD_LIMIT_EXCEEDED').
        error_description: Raw error description from webhook or gateway.
        is_recurring: Whether the failure belongs to a recurring subscription.
        amount: Transaction amount in rupees.

    Returns:
        Dict containing category, label, action, confidence, specific reason, and approval requirement.
    """
    text_to_check = f"{error_code or ''} {error_description or ''}".lower()
    requires_approval_by_amount = bool(amount is not None and amount >= HIGH_VALUE_THRESHOLD)

    # 1. Fraud / Security / Stolen / Blocked Instruments (Highest Priority Guardrail)
    fraud_keywords = [
        "fraud", "stolen", "blacklisted", "card_blocked", "blocked",
        "restricted", "suspicious", "security violation", "lost_card"
    ]
    if any(k in text_to_check for k in fraud_keywords):
        return {
            "category": CATEGORY_FRAUD_OR_SECURITY,
            "category_label": CATEGORY_LABELS[CATEGORY_FRAUD_OR_SECURITY],
            "action": "NO_ACTION",
            "confidence": 0.95,
            "reason": (
                "Payment was flagged for security or fraud risk (blocked/stolen instrument). "
                "Automated recovery is halted and flagged for compliance review."
            ),
            "requires_human_approval": True,
        }

    # 2. Card Expired
    expired_keywords = ["expired", "expiry", "card_expired", "expiration"]
    if any(k in text_to_check for k in expired_keywords):
        reason = (
            "Recurring subscription payment failed because the customer's registered card has expired. "
            "Recommending an automated payment link for the customer to update card details."
            if is_recurring
            else "Payment declined because the card's expiration date has passed. "
            "Issuing a payment link so the customer can enter active card credentials."
        )
        return {
            "category": CATEGORY_CARD_EXPIRED,
            "category_label": CATEGORY_LABELS[CATEGORY_CARD_EXPIRED],
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.92,
            "reason": reason,
            "requires_human_approval": requires_approval_by_amount,
        }

    # 3. Card Limit Exceeded
    limit_keywords = ["limit exceeded", "card limit", "daily limit", "credit limit", "over limit", "transaction limit", "spending limit"]
    if any(k in text_to_check for k in limit_keywords):
        reason = (
            "Recurring payment failed because the transaction exceeded the customer's card spending limit. "
            "Recommending a payment link allowing payment via a different card or UPI."
            if is_recurring
            else "Transaction was declined because the card's purchase or credit limit was exceeded. "
            "Providing a payment link for alternate card payment."
        )
        return {
            "category": CATEGORY_CARD_LIMIT_EXCEEDED,
            "category_label": CATEGORY_LABELS[CATEGORY_CARD_LIMIT_EXCEEDED],
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.90,
            "reason": reason,
            "requires_human_approval": requires_approval_by_amount,
        }

    # 4. Insufficient Funds / Low Balance
    funds_keywords = ["insufficient funds", "insufficient balance", "low balance", "not enough funds", "insufficient"]
    if any(k in text_to_check for k in funds_keywords):
        reason = (
            "Recurring subscription payment failed due to insufficient funds in customer account. "
            "Sending a payment link so the customer can retry with an alternate card, UPI, or netbanking."
            if is_recurring
            else "Payment failed due to insufficient account balance. "
            "Issuing a payment link allowing the customer to complete payment with another account or method."
        )
        return {
            "category": CATEGORY_INSUFFICIENT_FUNDS,
            "category_label": CATEGORY_LABELS[CATEGORY_INSUFFICIENT_FUNDS],
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.88,
            "reason": reason,
            "requires_human_approval": requires_approval_by_amount,
        }

    # 5. 3DS Authentication Required / Failed / OTP Dropped
    auth_keywords = ["authentication", "3ds", "otp", "challenge", "verification failed", "not authenticated"]
    if any(k in text_to_check for k in auth_keywords):
        reason = (
            "Customer 3DS / OTP authentication was not completed for this recurring billing cycle. "
            "Sending a payment link for immediate authentication."
            if is_recurring
            else "Customer 3DS / OTP authentication timed out or was cancelled during checkout. "
            "Reissuing payment link to complete authentication."
        )
        return {
            "category": CATEGORY_AUTHENTICATION_REQUIRED,
            "category_label": CATEGORY_LABELS[CATEGORY_AUTHENTICATION_REQUIRED],
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.90,
            "reason": reason,
            "requires_human_approval": requires_approval_by_amount,
        }

    # 6. Invalid Card Number / CVV / Details
    invalid_keywords = ["invalid card", "incorrect card", "invalid cvv", "invalid number", "invalid_card"]
    if any(k in text_to_check for k in invalid_keywords):
        return {
            "category": CATEGORY_INVALID_CARD,
            "category_label": CATEGORY_LABELS[CATEGORY_INVALID_CARD],
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.88,
            "reason": "Payment rejected due to invalid card number or security details. Sending payment link so customer can re-enter valid payment details.",
            "requires_human_approval": requires_approval_by_amount,
        }

    # 7. Temporary Gateway Error / Network Timeout / Bank Server Glitch
    # NOTE: "bad_request_error" was removed because it is Razorpay's generic error_code
    # used for nearly ALL failure types (insufficient funds, card expired, bank declined, etc.),
    # not a gateway-specific indicator. Keeping it here would misclassify real webhooks.
    gateway_keywords = [
        "gateway error", "timeout", "network", "system error", "bad gateway",
        "service unavailable", "bank server down", "connection reset"
    ]
    if any(k in text_to_check for k in gateway_keywords):
        return {
            "category": CATEGORY_TEMPORARY_GATEWAY_ERROR,
            "category_label": CATEGORY_LABELS[CATEGORY_TEMPORARY_GATEWAY_ERROR],
            "action": "WAIT",
            "confidence": 0.85,
            "reason": "Temporary gateway or banking infrastructure glitch detected. Holding recovery execution to prevent duplicate charges while waiting for a subsequent payment event or merchant intervention.",
            "requires_human_approval": False,
        }

    # 8. Bank Declined / Do Not Honor / Issuer Policy
    bank_keywords = ["bank declined", "declined by bank", "issuer declined", "do not honor", "not permitted"]
    if any(k in text_to_check for k in bank_keywords):
        return {
            "category": CATEGORY_BANK_DECLINED,
            "category_label": CATEGORY_LABELS[CATEGORY_BANK_DECLINED],
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.82,
            "reason": "Payment was declined by customer's issuing bank. Sending a payment link allowing payment with a different bank card or UPI.",
            "requires_human_approval": requires_approval_by_amount,
        }

    # 9. User Dropped Checkout Session
    if "dropped by user" in text_to_check or "cancelled by user" in text_to_check:
        reason = (
            "Customer abandoned the checkout session. Prompting with payment link to recover recurring subscription."
            if is_recurring
            else "Customer dropped checkout session before completing payment. Reissuing payment link to recover transaction."
        )
        return {
            "category": CATEGORY_AUTHENTICATION_REQUIRED,
            "category_label": CATEGORY_LABELS[CATEGORY_AUTHENTICATION_REQUIRED],
            "action": "SEND_PAYMENT_LINK",
            "confidence": 0.85,
            "reason": reason,
            "requires_human_approval": requires_approval_by_amount,
        }

    # 10. Explicit or Default Unknown Failure
    unknown_keywords = ["unknown", "unclassified", "unrecognized", "internal error", "general decline", "undefined"]
    if (not error_description and not error_code) or any(k in text_to_check for k in unknown_keywords):
        return {
            "category": CATEGORY_UNKNOWN,
            "category_label": CATEGORY_LABELS[CATEGORY_UNKNOWN],
            "action": "INVESTIGATE",
            "confidence": 0.50,
            "reason": "Payment failure occurred with unknown or unclassified gateway code. Manual investigation recommended.",
            "requires_human_approval": True,
        }

    # General known failure fallback
    reason = (
        f"Recurring subscription charge failed for ₹{amount:.2f}. Payment link recommended to prevent customer churn."
        if (is_recurring and amount is not None)
        else (
            f"Standard one-time transaction failure detected ({error_description}). Recommending payment link."
            if error_description
            else "Transaction failure detected. Recommending payment link."
        )
    )
    return {
        "category": CATEGORY_BANK_DECLINED,
        "category_label": CATEGORY_LABELS[CATEGORY_BANK_DECLINED],
        "action": "SEND_PAYMENT_LINK",
        "confidence": 0.80,
        "reason": reason,
        "requires_human_approval": requires_approval_by_amount,
    }


