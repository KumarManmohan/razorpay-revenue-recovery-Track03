import logging
import uuid
from typing import Any, Dict, Optional
from app.database import add_audit_event, _get_connection

logger = logging.getLogger(__name__)

# Deterministic, category-specific recovery guidance mapping
CATEGORY_RECOVERY_GUIDANCE = {
    "BANK_DECLINED": (
        "Your payment could not be completed because the transaction was declined by your issuing bank.",
        "Please try again using the secure recovery link below, or try an alternate payment method (UPI, Netbanking, or another card)."
    ),
    "INSUFFICIENT_FUNDS": (
        "Your payment could not be completed because there were insufficient available funds in the selected account.",
        "Please check your account balance or complete your payment using another bank account, UPI, or card through the link below."
    ),
    "CARD_LIMIT_EXCEEDED": (
        "Your payment could not be completed because a card spending or single-transaction limit was reached.",
        "Please try another card or complete the transaction using UPI / Netbanking via the secure recovery link below."
    ),
    "CARD_EXPIRED": (
        "Your payment could not be completed because the card details appear to have expired.",
        "Please provide updated card credentials or select an alternate payment method through the secure recovery link below."
    ),
    "INVALID_CARD": (
        "Your payment could not be completed because the card details could not be verified.",
        "Please verify your card number, expiry date, and CVV or use another payment method through the secure recovery link below."
    ),
    "AUTHENTICATION_REQUIRED": (
        "Your payment could not be completed because 3DS authorization or OTP verification was not completed.",
        "Please retry your payment and complete the two-factor authentication step using the secure link below."
    ),
}

DEFAULT_RECOVERY_GUIDANCE = (
    "Your recent payment could not be completed.",
    "Please click the secure recovery link below to retry your payment using an available payment method."
)


def _mask_recipient(recipient: str) -> str:
    """Masks customer email or phone for privacy in logs and audit events."""
    if not recipient:
        return "unknown"
    if "@" in recipient:
        parts = recipient.split("@")
        name = parts[0]
        domain = parts[1] if len(parts) > 1 else ""
        masked_name = name[0] + "***" + (name[-1] if len(name) > 1 else "")
        return f"{masked_name}@{domain}"
    elif len(recipient) >= 10:
        return recipient[:2] + "******" + recipient[-2:]
    return recipient[:1] + "***"


def build_customer_recovery_message(
    category: Optional[str],
    amount: float,
    currency: str = "INR",
    payment_link_url: str = "",
) -> Dict[str, str]:
    """
    Constructs deterministic, category-aware customer communication copy.
    Avoids leaking AI reasoning, confidence metrics, or internal policy codes.
    """
    cat_key = (category or "").upper().strip()
    explanation, action_guidance = CATEGORY_RECOVERY_GUIDANCE.get(
        cat_key, DEFAULT_RECOVERY_GUIDANCE
    )
    subject = f"Action Required: Complete your {currency} {amount:.2f} payment"
    body = (
        f"Hello,\n\n"
        f"{explanation}\n"
        f"{action_guidance}\n\n"
        f"Secure Payment Link:\n{payment_link_url}\n\n"
        f"Amount Due: {currency} {amount:.2f}\n\n"
        f"Thank you,\nMerchant Billing Support (Razorpay Test Mode Demo)"
    )
    return {"subject": subject, "body": body}


class NotificationProvider:
    """Base interface for test-safe customer notification providers."""
    def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError


class MockNotificationProvider(NotificationProvider):
    """
    Test-safe mock notification provider that simulates SMS/Email dispatch
    without communicating over external networks or sending real messages.
    """
    def send(
        self,
        recipient: str,
        subject: str,
        body: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        msg_id = f"mock_notif_{uuid.uuid4().hex[:8]}"
        masked = _mask_recipient(recipient)
        logger.info(
            f"[Mock Notification] Sent to '{masked}' | Subject: '{subject}' | Message ID: '{msg_id}'"
        )
        return {
            "status": "sent",
            "provider": "mock_notification_service",
            "message_id": msg_id,
            "recipient_masked": masked,
            "subject": subject,
            "mode": "test",
        }


def has_recent_notification(case_id: str, channel: str = "EMAIL", db_path: Optional[str] = None) -> bool:
    """
    Anti-spam guardrail: Checks if a notification was already sent for this recovery case.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM audit_events 
            WHERE case_id = ? AND event_type = 'NOTIFICATION_SENT'
            """,
            (case_id,),
        ).fetchone()
        return bool(row and row["cnt"] > 0)
    finally:
        conn.close()


def send_recovery_notification(
    case_id: str,
    recipient: Optional[str],
    payment_link_url: str,
    amount: float,
    currency: str = "INR",
    failure_category: Optional[str] = None,
    channel: str = "EMAIL",
    provider: Optional[NotificationProvider] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends a test-safe payment link notification to the customer with anti-spam deduplication
    and category-specific deterministic guidance.
    
    Args:
        case_id: Unique recovery case identifier.
        recipient: Customer email or phone number.
        payment_link_url: The generated Razorpay Test Mode payment link URL.
        amount: Payment amount in standard currency unit.
        currency: Currency code (default 'INR').
        failure_category: 1 of 9 normalized failure categories for message tailoring.
        channel: Notification channel ('EMAIL', 'SMS', 'WHATSAPP').
        provider: Optional custom NotificationProvider (defaults to MockNotificationProvider).
        db_path: Optional SQLite database path.

    Returns:
        Structured dispatch result dictionary.
    """
    if not recipient or not str(recipient).strip():
        logger.info(f"[Notification Skipped] No recipient address provided for case '{case_id}'.")
        return {
            "status": "skipped",
            "reason": "Missing recipient address.",
            "case_id": case_id,
        }

    if not payment_link_url:
        logger.warning(f"[Notification Skipped] No payment link URL available for case '{case_id}'.")
        return {
            "status": "skipped",
            "reason": "Missing payment link URL.",
            "case_id": case_id,
        }

    # 1. Anti-Spam / Duplicate Protection
    if has_recent_notification(case_id, channel=channel, db_path=db_path):
        reason = f"Duplicate notification blocked to prevent customer spam (case: {case_id})."
        logger.warning(f"[Notification Blocked] {reason}")
        add_audit_event(
            case_id=case_id,
            event_type="NOTIFICATION_BLOCKED_DUPLICATE",
            message=reason,
            metadata={"channel": channel, "attempted_recipient": _mask_recipient(recipient)},
            db_path=db_path,
        )
        return {
            "status": "blocked",
            "reason": "Duplicate notification blocked (anti-spam protection).",
            "case_id": case_id,
            "channel": channel,
        }

    # 2. Format Category-Aware Deterministic Message Template
    msg_data = build_customer_recovery_message(
        category=failure_category,
        amount=amount,
        currency=currency,
        payment_link_url=payment_link_url,
    )
    subject = msg_data["subject"]
    body = msg_data["body"]

    # 3. Dispatch via Provider (isolated with try/except)
    try:
        active_provider = provider or MockNotificationProvider()
        send_result = active_provider.send(
            recipient=recipient,
            subject=subject,
            body=body,
            metadata={
                "case_id": case_id,
                "amount": amount,
                "currency": currency,
                "channel": channel,
                "failure_category": failure_category,
            },
        )
    except Exception as e:
        logger.error(f"[Notification Failed] Dispatch error for case '{case_id}': {e}", exc_info=True)
        return {
            "status": "failed",
            "error": str(e),
            "case_id": case_id,
        }

    # 4. Record Audit Event
    add_audit_event(
        case_id=case_id,
        event_type="NOTIFICATION_SENT",
        message=f"Customer notification dispatched via {channel} (Message ID: {send_result.get('message_id')}).",
        metadata={
            "channel": channel,
            "recipient_masked": _mask_recipient(recipient),
            "message_id": send_result.get("message_id"),
            "payment_link_url": payment_link_url,
            "amount": amount,
            "failure_category": failure_category,
        },
        db_path=db_path,
    )

    return {
        "status": "sent",
        "case_id": case_id,
        "channel": channel,
        "recipient": _mask_recipient(recipient),
        "message_id": send_result.get("message_id"),
        "subject": subject,
    }
