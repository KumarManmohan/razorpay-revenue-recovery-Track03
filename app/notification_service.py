import logging
import uuid
from typing import Any, Dict, Optional
from app.database import add_audit_event, _get_connection

logger = logging.getLogger(__name__)


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
    recipient: str,
    payment_link_url: str,
    amount: float,
    currency: str = "INR",
    channel: str = "EMAIL",
    provider: Optional[NotificationProvider] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends a test-safe payment link notification to the customer with anti-spam deduplication.
    
    Args:
        case_id: Unique recovery case identifier.
        recipient: Customer email or phone number.
        payment_link_url: The generated Razorpay Test Mode payment link URL.
        amount: Payment amount in standard currency unit.
        currency: Currency code (default 'INR').
        channel: Notification channel ('EMAIL', 'SMS', 'WHATSAPP').
        provider: Optional custom NotificationProvider (defaults to MockNotificationProvider).
        db_path: Optional SQLite database path.

    Returns:
        Structured dispatch result dictionary.
    """
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

    # 2. Format Test-Safe Message Template
    subject = f"Action Required: Complete your {currency} {amount:.2f} payment"
    body = (
        f"Hello,\n\n"
        f"Your recent payment of {currency} {amount:.2f} could not be completed.\n"
        f"Please click the secure link below to retry your payment:\n\n"
        f"{payment_link_url}\n\n"
        f"Thank you,\nMerchant Billing Team (Razorpay Test Mode Demo)"
    )

    # 3. Dispatch via Provider
    active_provider = provider or MockNotificationProvider()
    send_result = active_provider.send(
        recipient=recipient,
        subject=subject,
        body=body,
        metadata={"case_id": case_id, "amount": amount, "currency": currency, "channel": channel},
    )

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
        },
        db_path=db_path,
    )

    return {
        "status": "sent",
        "case_id": case_id,
        "channel": channel,
        "recipient": _mask_recipient(recipient),
        "message_id": send_result.get("message_id"),
    }
