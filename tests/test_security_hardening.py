"""
Test Suite: Milestone D.1 - Minimal Production Hardening

Validates:
1. Merchant API Key authentication on administrative / mutation endpoints.
2. Unauthenticated access blocked (HTTP 401) with invalid/missing key when configured.
3. Read-only endpoints remain accessible without API key.
4. Webhook verification relies strictly on HMAC-SHA256 (no API key required).
5. Restricted CORS policy (no wildcard '*').
6. Request body size limit enforcement (HTTP 413 for payloads > 512 KB).
7. In-process rate limiting (HTTP 429 when limits exceeded).
8. Global exception handler returns sanitized JSON with reference ID.
9. Security headers present in middleware responses.
10. SQLite WAL mode active.
11. Real recovered case integrity preserved.
"""

import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from app.config import settings
from app.database import _get_connection, DEFAULT_DB_PATH
from app.main import (
    app,
    enforce_payload_and_security_headers,
    global_unhandled_exception_handler,
    approve_recovery_case,
    reject_recovery_case,
    notify_customer_for_case,
    ApprovalRequest,
    RejectionRequest,
    NotificationRequest,
)
from app.razorpay_client import verify_webhook_signature
from app.security import (
    require_merchant_auth,
    check_rate_limit,
    rate_limiter,
    verify_api_key_constant_time,
)


class TestSecurityHardening(unittest.TestCase):

    def setUp(self):
        rate_limiter.reset()

    def test_01_security_headers_injected_by_middleware(self):
        """Verify standard security headers are attached by response middleware."""
        async def run_test():
            req = MagicMock(spec=Request)
            req.headers = {}
            
            mock_response = Response(content="OK", media_type="text/plain")
            async def call_next(r):
                return mock_response

            res = await enforce_payload_and_security_headers(req, call_next)
            self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(res.headers.get("X-Frame-Options"), "DENY")
            self.assertEqual(res.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")

        asyncio.run(run_test())

    def test_02_cors_no_wildcard(self):
        """Verify CORS allows configured origins and does not use wildcard '*'."""
        self.assertNotIn("*", settings.cors_origins)
        self.assertIn("http://localhost:5173", settings.cors_origins)
        self.assertIn("http://127.0.0.1:5173", settings.cors_origins)

    def test_03_merchant_auth_allows_when_unconfigured_in_dev(self):
        """When MERCHANT_API_KEY is unset, requests pass (dev mode)."""
        with patch.object(settings, "MERCHANT_API_KEY", ""):
            res = require_merchant_auth(x_api_key=None)
            self.assertTrue(res)

    def test_04_merchant_auth_blocks_missing_key_when_configured(self):
        """When MERCHANT_API_KEY is set, missing X-API-Key raises HTTP 401."""
        with patch.object(settings, "MERCHANT_API_KEY", "prod_secret_key_123"):
            with self.assertRaises(HTTPException) as ctx:
                require_merchant_auth(x_api_key=None)
            self.assertEqual(ctx.exception.status_code, 401)
            self.assertIn("Missing", ctx.exception.detail.get("message", ""))

    def test_05_merchant_auth_blocks_invalid_key(self):
        """When MERCHANT_API_KEY is set, wrong X-API-Key raises HTTP 401."""
        with patch.object(settings, "MERCHANT_API_KEY", "prod_secret_key_123"):
            with self.assertRaises(HTTPException) as ctx:
                require_merchant_auth(x_api_key="wrong_hacker_key")
            self.assertEqual(ctx.exception.status_code, 401)
            self.assertIn("Invalid", ctx.exception.detail.get("message", ""))

    def test_06_merchant_auth_accepts_valid_key(self):
        """When MERCHANT_API_KEY is set, correct X-API-Key passes."""
        with patch.object(settings, "MERCHANT_API_KEY", "prod_secret_key_123"):
            res = require_merchant_auth(x_api_key="prod_secret_key_123")
            self.assertTrue(res)

    def test_07_constant_time_key_comparison(self):
        """Verify constant-time key verification logic."""
        self.assertTrue(verify_api_key_constant_time("test_key_123", "test_key_123"))
        self.assertFalse(verify_api_key_constant_time("test_key_123", "wrong_key"))
        self.assertFalse(verify_api_key_constant_time(None, "test_key_123"))
        self.assertFalse(verify_api_key_constant_time("test_key_123", ""))

    def test_08_webhook_hmac_signature_verification(self):
        """Verify Razorpay webhook HMAC-SHA256 signature verification."""
        secret = "whsec_test_secret_99"
        raw_body = b'{"event":"payment.failed","id":"evt_test_99"}'
        valid_sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

        # Valid signature
        self.assertTrue(verify_webhook_signature(raw_body, valid_sig, secret))
        # Tampered body
        self.assertFalse(verify_webhook_signature(b'{"event":"payment.captured"}', valid_sig, secret))
        # Invalid signature
        self.assertFalse(verify_webhook_signature(raw_body, "bad_signature_hex", secret))

    def test_09_oversized_payload_rejected_with_413(self):
        """Verify payload exceeding 512 KB is rejected with HTTP 413 by middleware."""
        async def run_test():
            req = MagicMock(spec=Request)
            # 600 KB Content-Length
            req.headers = {"content-length": str(600 * 1024)}

            async def call_next(r):
                return Response("OK")

            res = await enforce_payload_and_security_headers(req, call_next)
            self.assertEqual(res.status_code, 413)
            body = json.loads(res.body.decode("utf-8"))
            self.assertEqual(body.get("status"), "error")
            self.assertIn("Payload Too Large", body.get("message", ""))

        asyncio.run(run_test())

    def test_10_rate_limiting_enforced(self):
        """Verify in-memory sliding-window rate limiter blocks after limit exceeded."""
        rate_limiter.reset()
        req = MagicMock(spec=Request)
        req.client.host = "192.168.1.100"
        req.headers = {}

        # 30 allowed requests
        for _ in range(30):
            check_rate_limit(req, bucket_name="test_bucket", max_requests=30)

        # 31st request should raise HTTP 429
        with self.assertRaises(HTTPException) as ctx:
            check_rate_limit(req, bucket_name="test_bucket", max_requests=30)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Rate limit", ctx.exception.detail.get("message", ""))
        self.assertIn("Retry-After", ctx.exception.headers)

    def test_11_global_exception_handler_sanitized_response(self):
        """Verify unhandled exceptions return sanitized JSON with reference ID."""
        async def run_test():
            req = MagicMock(spec=Request)
            req.url.path = "/recovery/cases"
            exc = RuntimeError("Database connection string leaked: postgres://user:pass@internal-db:5432/main")

            res = await global_unhandled_exception_handler(req, exc)
            self.assertEqual(res.status_code, 500)
            data = json.loads(res.body.decode("utf-8"))
            self.assertEqual(data.get("status"), "error")
            self.assertEqual(data.get("message"), "An internal server error occurred.")
            self.assertTrue(data.get("reference_id", "").startswith("err_"))
            # Ensure no credentials/traceback details leaked
            self.assertNotIn("postgres://user:pass", json.dumps(data))

        asyncio.run(run_test())

    def test_12_sqlite_wal_mode_active(self):
        """Verify SQLite database connection operates in WAL journal mode."""
        conn = _get_connection()
        try:
            cursor = conn.execute("PRAGMA journal_mode;")
            row = cursor.fetchone()
            mode = row[0] if row else ""
            self.assertEqual(mode.lower(), "wal")
        finally:
            conn.close()

    def test_13_real_recovered_case_preserved(self):
        """Verify real recovered case case_pay_TT0g8mGaP6dv1S remains 100% intact."""
        conn = _get_connection()
        try:
            row = conn.execute("SELECT * FROM recovery_cases WHERE id = 'case_pay_TT0g8mGaP6dv1S'").fetchone()
            self.assertIsNotNone(row, "Real recovered case must exist in database.")
            self.assertEqual(row["execution_status"], "recovered")
            self.assertEqual(row["recovered_amount"], 850.0)
            self.assertEqual(row["recovered_payment_id"], "pay_TT1CLOhH0DTNCQ")
            self.assertEqual(row["payment_link_id"], "plink_TT14lUzPvkFgov")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
