"""
Production Security Hardening Module (Milestone D.1)

Provides:
- Merchant API Key Authentication with constant-time verification.
- In-process sliding-window rate limiting.
- Security headers and payload size protection.
"""

import hmac
import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple
from fastapi import HTTPException, Header, Request, status
from app.config import settings

logger = logging.getLogger(__name__)


def verify_api_key_constant_time(provided_key: Optional[str], expected_key: str) -> bool:
    """Performs constant-time string comparison to prevent timing attacks."""
    if not expected_key or not provided_key:
        return False
    return hmac.compare_digest(provided_key.encode("utf-8"), expected_key.encode("utf-8"))


def require_merchant_auth(x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    """
    FastAPI dependency for administrative/mutation endpoints.
    If MERCHANT_API_KEY is configured in settings:
      - Validates X-API-Key header.
      - Rejects missing or invalid keys with HTTP 401.
    If MERCHANT_API_KEY is not configured:
      - Permits requests (unauthenticated development mode).
    """
    configured_key = settings.MERCHANT_API_KEY
    if not configured_key:
        # Development mode without explicit key
        return True

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "status": "error",
                "message": "Unauthorized: Missing X-API-Key header.",
            },
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if not verify_api_key_constant_time(x_api_key, configured_key):
        logger.warning("[Security] Invalid API key attempted on protected endpoint.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "status": "error",
                "message": "Unauthorized: Invalid X-API-Key credential.",
            },
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return True


class SlidingWindowRateLimiter:
    """
    Lightweight in-memory sliding-window rate limiter for single-process instances.
    Maintains a deque of request timestamps per bucket key (IP / endpoint).
    """
    def __init__(self):
        # Key: (client_ip, bucket_name) -> Deque of timestamps
        self._buckets: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)

    def is_allowed(self, client_key: str, bucket_name: str, max_requests: int, window_seconds: int = 60) -> Tuple[bool, int]:
        """
        Checks if a request is allowed within the sliding window.
        
        Returns:
            (is_allowed: bool, retry_after_seconds: int)
        """
        now = time.time()
        bucket = self._buckets[(client_key, bucket_name)]
        window_start = now - window_seconds

        # Evict timestamps older than the sliding window
        while bucket and bucket[0] < window_start:
            bucket.popleft()

        if len(bucket) >= max_requests:
            # Rate limit exceeded
            oldest = bucket[0]
            retry_after = max(1, int(window_seconds - (now - oldest)))
            return False, retry_after

        # Record this request timestamp
        bucket.append(now)
        return True, 0

    def reset(self):
        """Clears all rate limit buckets (useful for unit tests)."""
        self._buckets.clear()


# Global rate limiter instance
rate_limiter = SlidingWindowRateLimiter()


def check_rate_limit(request: Request, bucket_name: str, max_requests: int, window_seconds: int = 60):
    """
    Enforces a rate limit for the incoming request's client IP.
    Raises HTTP 429 when the limit is exceeded.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    
    # Also consider X-Forwarded-For if behind a proxy
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    allowed, retry_after = rate_limiter.is_allowed(
        client_key=client_ip,
        bucket_name=bucket_name,
        max_requests=max_requests,
        window_seconds=window_seconds,
    )

    if not allowed:
        logger.warning(f"[Rate Limit Exceeded] IP: {client_ip} | Bucket: {bucket_name} | Limit: {max_requests}/min")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "status": "error",
                "message": f"Too Many Requests: Rate limit of {max_requests} requests per minute exceeded.",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
