import hmac
import hashlib
import razorpay
from app.config import settings

def get_razorpay_client() -> razorpay.Client:
    """
    Creates and returns an authenticated Razorpay Client instance using
    credentials loaded securely from environment variables.
    
    Raises:
        ValueError: If RAZORPAY_KEY_ID or RAZORPAY_KEY_SECRET is not configured.
    """
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise ValueError(
            "Razorpay credentials not found. Please set RAZORPAY_KEY_ID "
            "and RAZORPAY_KEY_SECRET in your .env file."
        )

    return razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )

def verify_webhook_signature(raw_body: bytes | str, signature: str, secret: str) -> bool:
    """
    Verifies the HMAC-SHA256 signature sent by Razorpay in the X-Razorpay-Signature header.
    
    Args:
        raw_body: The unmodified raw bytes or string of the incoming webhook request body.
        signature: The hex-encoded signature from X-Razorpay-Signature header.
        secret: The webhook secret configured in Razorpay Dashboard and stored in .env.
        
    Returns:
        bool: True if signature matches, False otherwise.
    """
    if not secret or not signature or not raw_body:
        return False
    
    body_bytes = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
    secret_bytes = secret.encode("utf-8")
    
    # Calculate HMAC-SHA256 hash
    expected_signature = hmac.new(
        secret_bytes,
        body_bytes,
        hashlib.sha256
    ).hexdigest()
    
    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected_signature, signature)

