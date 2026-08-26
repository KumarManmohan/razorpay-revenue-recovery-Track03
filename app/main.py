import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException, Header, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from app.config import settings
from app.razorpay_client import get_razorpay_client, verify_webhook_signature
from app.revenue_risk import analyze_payment_failure, extract_payment_link_id
from app.recovery_decision import decide_recovery_action
from app.recovery_executor import execute_recovery_action, fetch_payment_link_url
from app.ai_recovery_agent import ai_decide_recovery_action
from app.notification_service import send_recovery_notification
from app.security import check_rate_limit, require_merchant_auth
from app.database import (
    init_db,
    create_or_get_recovery_case,
    update_recovery_decision,
    update_execution_status,
    add_audit_event,
    get_all_cases,
    get_case_with_audit,
    get_all_audit_events,
    get_case_by_id,
    approve_case,
    reject_case,
    get_dashboard_stats,
    reconcile_recovery_payment,
    evaluate_case_exhaustion,
    exhaust_recovery_case,
    count_failed_attempts_for_case,
    update_case_payment_link_url,
)

# Configure basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.MERCHANT_API_KEY:
        logger.info("[Security] Merchant API key authentication ENABLED for administrative endpoints.")
    else:
        logger.warning("[Security] No MERCHANT_API_KEY configured; running in unauthenticated development mode.")
    logger.info("[Database] Initialized SQLite tables with WAL mode: recovery_cases, audit_events.")
    yield


# Initialize FastAPI application
app = FastAPI(
    title="AI Revenue Recovery Agent API",
    description="Backend service for detecting, analyzing, recovering at-risk revenue, and maintaining an audit trail.",
    version="0.1.0",
    lifespan=lifespan,
)

# Security Middleware: Request Body Size Limit & Security Response Headers
@app.middleware("http")
async def enforce_payload_and_security_headers(request: Request, call_next):
    # 1. Reject oversized requests based on Content-Length header or body size
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.MAX_REQUEST_BODY_SIZE_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={
                        "status": "error",
                        "message": f"Payload Too Large: Request body exceeds maximum allowed size of {settings.MAX_REQUEST_BODY_SIZE_BYTES // 1024} KB.",
                    },
                )
        except ValueError:
            pass

    response = await call_next(request)

    # 2. Attach security response headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response


# Global Exception Handler for unexpected server errors (sanitized response with reference ID)
@app.exception_handler(Exception)
async def global_unhandled_exception_handler(request: Request, exc: Exception):
    ref_id = f"err_{uuid.uuid4().hex[:10]}"
    logger.error(
        f"[Unhandled Server Error] Reference: {ref_id} | Path: {request.url.path} | Error: {str(exc)}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "status": "error",
            "message": "An internal server error occurred.",
            "reference_id": ref_id,
        },
    )


# Restricted CORS configuration (No wildcard '*')
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Ensure DB is initialized at module load time
init_db()



# Pydantic Request Models for Approval & Notification
class ApprovalRequest(BaseModel):
    approver: Optional[str] = "admin"
    notes: Optional[str] = "Approved via merchant review console."


class RejectionRequest(BaseModel):
    approver: Optional[str] = "admin"
    reason: Optional[str] = "Rejected during risk/policy assessment."


class NotificationRequest(BaseModel):
    recipient: Optional[str] = "customer@example.com"
    channel: Optional[str] = "EMAIL"


@app.get("/")
def root():
    """
    Root endpoint returning service identity and status.
    """
    return {
        "service": "AI Revenue Recovery Agent",
        "status": "online",
        "environment": settings.ENVIRONMENT,
        "docs_url": "/docs",
    }


@app.get("/health")
def health_check():
    """
    Health check endpoint to verify backend operational readiness.
    """
    return {
        "status": "healthy",
        "service": "revenue-recovery-backend",
        "database": "sqlite_ready",
    }


@app.get("/razorpay-test")
def razorpay_test():
    """
    Read-only test endpoint to verify Razorpay Test Mode authentication.
    Fetches the latest single payment or order to confirm valid credentials.
    """
    try:
        client = get_razorpay_client()
        result = client.payment.all({"count": 1})
        
        key_id = settings.RAZORPAY_KEY_ID
        masked_key = key_id[:8] + "..." + key_id[-4:] if len(key_id) > 12 else key_id
        
        return {
            "status": "success",
            "message": "Successfully authenticated with Razorpay Test Mode API.",
            "key_id": masked_key,
            "sample_records_retrieved": len(result.get("items", [])),
        }
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "message": str(val_err),
            },
        )
    except Exception as api_err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "status": "error",
                "message": f"Razorpay API call failed: {str(api_err)}",
            },
        )


@app.post("/revenue-risk/analyze")
async def analyze_revenue_risk(request: Request):
    """
    Direct endpoint to analyze a payment failure payload and calculate
    a structured revenue-at-risk case.
    """
    try:
        payload = await request.json()
    except Exception as parse_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "message": f"Invalid JSON body: {str(parse_err)}",
            },
        )

    analysis_result = analyze_payment_failure(payload)
    return {
        "status": "success",
        "data": analysis_result,
    }


@app.post("/recovery/decide")
async def decide_recovery(request: Request):
    """
    Evaluates a payment failure or revenue-risk case using the AI Reasoning Agent
    (with deterministic safety fallback) and persists the recovery decision.
    """
    check_rate_limit(request, bucket_name="recovery_decide", max_requests=30)
    try:
        payload = await request.json()
    except Exception as parse_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "message": f"Invalid JSON body: {str(parse_err)}",
            },
        )

    # 1. Run risk analysis
    if "risk_status" not in payload:
        risk_analysis = analyze_payment_failure(payload)
    else:
        risk_analysis = payload

    # 2. Persist/Fetch Recovery Case in DB
    case_record, is_new = create_or_get_recovery_case(risk_analysis)
    case_id = case_record["id"]

    if is_new:
        add_audit_event(
            case_id=case_id,
            event_type="PAYMENT_FAILED",
            message=f"Payment failure registered for amount ₹{risk_analysis.get('amount')}.",
            metadata={"payment_id": risk_analysis.get("payment_id"), "currency": risk_analysis.get("currency")},
        )
        add_audit_event(
            case_id=case_id,
            event_type="RISK_ANALYZED",
            message=f"Risk classified as '{risk_analysis.get('risk_status')}': {risk_analysis.get('risk_reason')}.",
            metadata={"is_recurring": risk_analysis.get("is_recurring_revenue")},
        )

    # 3. Run AI recovery decision engine with deterministic fallback & guardrails
    decision = ai_decide_recovery_action(risk_analysis)

    # 4. Update Case in DB with Decision & Record Audit
    updated_case = update_recovery_decision(case_id, decision)
    
    event_type = "RECOVERY_DECIDED"
    if decision.get("action") == "NO_ACTION":
        event_type = "RECOVERY_BLOCKED"
    elif decision.get("requires_human_approval"):
        event_type = "HUMAN_APPROVAL_REQUIRED"

    add_audit_event(
        case_id=case_id,
        event_type=event_type,
        message=f"Recommended action '{decision.get('action')}' (confidence: {decision.get('confidence')}). {decision.get('reason')}",
        metadata={"decision_source": decision.get("decision_source"), "requires_approval": decision.get("requires_human_approval")},
    )

    logger.info(
        f"[Recovery Decision] Case: {case_id} | "
        f"Action: {decision.get('action')} | "
        f"Source: {decision.get('decision_source')} | "
        f"Approval Required: {decision.get('requires_human_approval')}"
    )

    return {
        "status": "success",
        "case_id": case_id,
        "risk_analysis": risk_analysis,
        "recovery_decision": decision,
    }


@app.post("/recovery/execute", dependencies=[Depends(require_merchant_auth)])
async def execute_recovery(request: Request):
    """
    Executes a recovery action (SEND_PAYMENT_LINK) strictly in Razorpay Test Mode.
    The database is the source of truth: client cannot bypass human approval by tampering with payload flags.
    Protected by Merchant API Key authentication.
    """
    check_rate_limit(request, bucket_name="recovery_execute", max_requests=20)
    try:
        payload = await request.json()
    except Exception as parse_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "message": f"Invalid JSON body: {str(parse_err)}",
            },
        )

    decision = payload.get("recovery_decision", payload)
    risk_case_id = str(decision.get("risk_case_id") or decision.get("payment_id") or "unspecified")

    # DB Integrity Check: Verify true approval status from DB if case exists
    db_case = get_case_by_id(risk_case_id)
    if db_case:
        # If DB says human approval is required and case has not been approved, enforce approval requirement!
        if db_case.get("requires_human_approval") == 1 and db_case.get("execution_status") not in ("approved", "executed"):
            decision["requires_human_approval"] = True

    # 1. Execute recovery action with safety checks
    execution_result = execute_recovery_action(decision)
    exec_status = execution_result.get("status")

    # 2. Update Database & Audit Trail
    update_execution_status(risk_case_id, execution_result)

    if exec_status == "executed":
        add_audit_event(
            case_id=risk_case_id,
            event_type="PAYMENT_LINK_CREATED",
            message=f"Test Mode Payment Link created: {execution_result.get('payment_link_id')}",
            metadata={
                "payment_link_id": execution_result.get("payment_link_id"),
                "payment_link_url": execution_result.get("payment_link_url"),
                "amount": execution_result.get("amount"),
            },
        )
    elif exec_status == "approval_required":
        add_audit_event(
            case_id=risk_case_id,
            event_type="HUMAN_APPROVAL_REQUIRED",
            message="Execution blocked pending manual human authorization.",
            metadata={"amount": decision.get("amount")},
        )
    elif exec_status == "failed":
        add_audit_event(
            case_id=risk_case_id,
            event_type="RECOVERY_FAILED",
            message=f"Payment Link execution failed: {execution_result.get('error')}",
            metadata={"error": execution_result.get("error")},
        )

    return {
        "status": "success",
        "result": execution_result,
    }


@app.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(None, alias="X-Razorpay-Signature"),
    x_razorpay_event_id: str | None = Header(None, alias="X-Razorpay-Event-Id"),
):
    """
    Webhook endpoint to receive real-time payment and order events from Razorpay.
    Verifies HMAC-SHA256 signature, enforces idempotency, persists recovery cases,
    runs AI reasoning, and logs audit events.
    Protected by HMAC-SHA256 signature verification and sliding-window rate limiting.
    """
    check_rate_limit(request, bucket_name="webhook_razorpay", max_requests=60)
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        logger.error("RAZORPAY_WEBHOOK_SECRET is not configured in environment.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "message": "Webhook secret is not configured on the server.",
            },
        )

    if not x_razorpay_signature:
        logger.warning("Webhook received without X-Razorpay-Signature header.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "message": "Missing X-Razorpay-Signature header.",
            },
        )

    raw_body = await request.body()
    is_valid = verify_webhook_signature(
        raw_body=raw_body,
        signature=x_razorpay_signature,
        secret=settings.RAZORPAY_WEBHOOK_SECRET,
    )

    if not is_valid:
        logger.warning("Invalid webhook signature rejected.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "message": "Invalid webhook signature.",
            },
        )

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as parse_err:
        logger.error(f"Failed to parse webhook JSON payload: {parse_err}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "message": "Invalid JSON payload.",
            },
        )

    event_name = payload.get("event", "unknown")
    event_id = x_razorpay_event_id or payload.get("id", "unknown")
    logger.info(f"[Razorpay Webhook Verified] Event: '{event_name}' | Event ID: '{event_id}'")

    if event_name == "payment.failed":
        payload["x_razorpay_event_id"] = event_id
        risk_analysis = analyze_payment_failure(payload)

        # 1. Idempotency & Attempt Tracking: Create or fetch unified case
        case_record, is_new = create_or_get_recovery_case(risk_analysis)
        case_id = case_record["id"]

        # Check exact event-level idempotency
        if not is_new and case_record.get("event_id") == event_id:
            logger.info(f"[Webhook Idempotency] Event '{event_id}' already processed for case '{case_id}'.")
            return {
                "status": "already_processed",
                "message": "Duplicate webhook event ignored (idempotent).",
                "event": event_name,
                "event_id": event_id,
                "case_id": case_id,
            }

        # If this is an additional failed attempt on an existing case
        if not is_new:
            logger.info(f"[Payment Attempt Failed] Case '{case_id}' recorded additional failed attempt '{risk_analysis.get('payment_id')}'.")
            add_audit_event(
                case_id=case_id,
                event_type="PAYMENT_ATTEMPT_FAILED",
                message=f"Additional payment attempt {risk_analysis.get('payment_id')} failed: {risk_analysis.get('error_description') or risk_analysis.get('risk_reason') or 'Declined.'}",
                metadata={
                    "payment_id": risk_analysis.get("payment_id"),
                    "order_id": risk_analysis.get("order_id"),
                    "error_code": risk_analysis.get("error_code"),
                },
            )
        else:
            # Initial lifecycle audit events
            add_audit_event(
                case_id=case_id,
                event_type="PAYMENT_FAILED",
                message=f"Received payment.failed webhook for payment {risk_analysis.get('payment_id')} (₹{risk_analysis.get('amount')}).",
                metadata={"payment_id": risk_analysis.get("payment_id"), "order_id": risk_analysis.get("order_id"), "error_code": risk_analysis.get("error_code")},
            )
            add_audit_event(
                case_id=case_id,
                event_type="RISK_ANALYZED",
                message=f"Risk status: {risk_analysis.get('risk_status')}. Reason: {risk_analysis.get('risk_reason')}",
                metadata={"is_recurring": risk_analysis.get("is_recurring_revenue")},
            )

        # 2. Check Deterministic Retry Exhaustion Stopping Rules
        is_exhausted, exhaust_reason, exhaust_meta = evaluate_case_exhaustion(case_record)
        if is_exhausted and case_record.get("execution_status") != "exhausted":
            case_record, _ = exhaust_recovery_case(case_id, reason=exhaust_reason or "Retry limit reached.", metadata=exhaust_meta)
            logger.info(f"[Recovery Exhausted] Case '{case_id}' marked as exhausted: {exhaust_reason}")

        # 3. Run AI recovery decision engine with updated context
        risk_analysis["execution_status"] = case_record.get("execution_status")
        risk_analysis["attempts_count"] = count_failed_attempts_for_case(case_id)
        decision = ai_decide_recovery_action(risk_analysis)
        update_recovery_decision(case_id, decision)

        event_type = "RECOVERY_DECIDED"
        if decision.get("action") == "NO_ACTION":
            event_type = "RECOVERY_BLOCKED"
        elif decision.get("requires_human_approval"):
            event_type = "HUMAN_APPROVAL_REQUIRED"

        add_audit_event(
            case_id=case_id,
            event_type=event_type,
            message=f"AI Decision: {decision.get('action')}. {decision.get('reason')}",
            metadata={"confidence": decision.get("confidence"), "source": decision.get("decision_source")},
        )

        # 4. Auto-Execution for Safe Low-Value Cases (SEND_PAYMENT_LINK without human approval and NOT exhausted)
        if decision.get("action") == "SEND_PAYMENT_LINK" and not decision.get("requires_human_approval") and case_record.get("execution_status") != "exhausted":
            latest_case = get_case_by_id(case_id) or case_record
            existing_link_id = (
                latest_case.get("payment_link_id")
                or latest_case.get("original_payment_link_id")
                or risk_analysis.get("payment_link_id")
            )
            existing_link_url = (
                latest_case.get("payment_link_url")
                or latest_case.get("original_payment_link_url")
            )
            # If we have the link ID but not the official short_url, fetch it directly from Razorpay
            if existing_link_id and not existing_link_url:
                existing_link_url = fetch_payment_link_url(existing_link_id)

            # If a valid payment link is already active for this case, preserve it (One Active Payment Path)
            if existing_link_id:
                # Ensure the payment_link_id & url are persisted on the case record
                if not latest_case.get("payment_link_id") or not latest_case.get("payment_link_url"):
                    update_execution_status(
                        case_id,
                        {
                            "status": "executed",
                            "payment_link_id": existing_link_id,
                            "payment_link_url": existing_link_url,
                        },
                    )

                logger.info(f"[Payment Path Preserved] Case '{case_id}' already has active payment link: {existing_link_id}")
                add_audit_event(
                    case_id=case_id,
                    event_type="PAYMENT_PATH_PRESERVED",
                    message=f"Existing active payment link '{existing_link_id}' preserved for customer recovery without duplicate issuance.",
                    metadata={"payment_link_id": existing_link_id, "payment_link_url": existing_link_url},
                )
            else:
                # Generate new Razorpay Test Mode Payment Link
                exec_decision = {
                    "action": "SEND_PAYMENT_LINK",
                    "requires_human_approval": False,
                    "risk_case_id": case_id,
                    "amount": risk_analysis.get("amount"),
                    "currency": risk_analysis.get("currency", "INR"),
                }
                exec_result = execute_recovery_action(exec_decision)
                update_execution_status(case_id, exec_result)

                if exec_result.get("status") == "executed":
                    add_audit_event(
                        case_id=case_id,
                        event_type="PAYMENT_LINK_CREATED",
                        message=f"Auto-generated Test Mode Payment Link: {exec_result.get('payment_link_id')}",
                        metadata={
                            "payment_link_id": exec_result.get("payment_link_id"),
                            "payment_link_url": exec_result.get("payment_link_url"),
                            "amount": exec_result.get("amount"),
                        },
                    )

        return {
            "status": "received",
            "event": event_name,
            "event_id": event_id,
            "case_id": case_id,
            "risk_analysis": risk_analysis,
            "recovery_decision": decision,
        }

    elif event_name in ("payment.captured", "payment_link.paid", "order.paid"):
        payload_data = payload.get("payload", {})
        payment_entity = payload_data.get("payment", {}).get("entity", {})
        link_entity = payload_data.get("payment_link", {}).get("entity", {})

        # Extract payment identifiers & metadata
        payment_id = payment_entity.get("id") or payload.get("id", "unknown")
        raw_amount = payment_entity.get("amount") or link_entity.get("amount_paid") or link_entity.get("amount") or 0
        amount_rupees = round(float(raw_amount) / 100.0, 2)
        currency = payment_entity.get("currency") or link_entity.get("currency") or "INR"

        # Discover Payment Link ID using robust extraction hierarchy:
        # 1. Explicit payment_link_id on payment entity
        # 2. Explicit ID on payment_link entity
        # 3. Notes / description extraction via extract_payment_link_id
        plink_id = (
            payment_entity.get("payment_link_id")
            or link_entity.get("id")
            or extract_payment_link_id(payment_entity, payload_obj=payload_data)
        )
        if not plink_id and link_entity.get("id"):
            l_id = link_entity.get("id")
            if isinstance(l_id, str) and l_id.startswith("plink_"):
                plink_id = l_id

        # Check notes and order IDs for recovery association
        notes = payment_entity.get("notes") or link_entity.get("notes") or {}
        risk_case_id = notes.get("risk_case_id")
        order_id = payment_entity.get("order_id") or link_entity.get("order_id")

        # Target identifier to match case: risk_case_id, plink_id, or order_id
        target_case_key = risk_case_id or plink_id or order_id or payment_id

        if not target_case_key:
            logger.info(f"[Webhook Unmanaged Payment] Event '{event_name}' ({event_id}) not associated with recovery.")
            return {
                "status": "ignored",
                "event": event_name,
                "event_id": event_id,
                "message": "Payment is not associated with an AI revenue recovery case.",
            }

        # Attempt server-side reconciliation
        reconciled_case, recon_status = reconcile_recovery_payment(
            case_id_or_link_id=target_case_key,
            recovered_payment_id=payment_id,
            recovered_amount=amount_rupees,
            metadata={
                "event_id": event_id,
                "event_name": event_name,
                "currency": currency,
                "payment_link_id": plink_id,
                "order_id": order_id,
            },
        )

        if recon_status == "Case not found.":
            logger.warning(f"[Reconciliation Unmatched] Payment {payment_id} references unknown case/link/order: {target_case_key}")
            return {
                "status": "unmatched",
                "event": event_name,
                "event_id": event_id,
                "target_case_key": target_case_key,
                "message": "No matching recovery case found in server records.",
            }

        if recon_status == "already_reconciled":
            logger.info(f"[Reconciliation Idempotent] Case '{reconciled_case['id']}' already reconciled for payment '{payment_id}'.")
            return {
                "status": "already_processed",
                "event": event_name,
                "event_id": event_id,
                "case_id": reconciled_case["id"],
                "message": "Recovery payment already reconciled (idempotent).",
            }

        if recon_status == "duplicate_payment_recorded":
            logger.warning(f"[Duplicate Payment Recorded] Case '{reconciled_case['id']}' received duplicate payment '{payment_id}'.")
            return {
                "status": "duplicate_payment_recorded",
                "event": event_name,
                "event_id": event_id,
                "case_id": reconciled_case["id"],
                "message": "Duplicate payment detected and recorded on already-recovered case.",
            }

        logger.info(f"[Reconciliation Success] Case '{reconciled_case['id']}' marked as recovered (₹{amount_rupees}).")
        return {
            "status": "reconciled",
            "event": event_name,
            "event_id": event_id,
            "case_id": reconciled_case["id"],
            "recovered_amount": amount_rupees,
            "recovered_payment_id": payment_id,
        }

    return {
        "status": "received",
        "event": event_name,
        "event_id": event_id,
    }



# ==========================================
# Read-Only Recovery Case Endpoints
# ==========================================

@app.get("/recovery/stats")
def get_recovery_dashboard_stats():
    """
    Read-only endpoint returning summary KPI metrics for the Merchant Dashboard.
    """
    stats = get_dashboard_stats()
    return {
        "status": "success",
        "stats": stats,
    }


@app.get("/recovery/cases")
def list_recovery_cases(limit: int = 50):

    """
    Read-only endpoint returning recent recovery cases from SQLite.
    """
    cases = get_all_cases(limit=limit)
    return {
        "status": "success",
        "count": len(cases),
        "cases": cases,
    }


@app.get("/recovery/cases/{case_id}")
def get_single_recovery_case(case_id: str):
    """
    Read-only endpoint returning a specific recovery case along with
    its chronological audit event history. Self-heals missing payment_link_url
    if a valid payment_link_id exists.
    """
    case_data = get_case_with_audit(case_id)
    if not case_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "status": "error",
                "message": f"Recovery case '{case_id}' not found.",
            },
        )

    case_obj = case_data["case"]
    plink_id = case_obj.get("payment_link_id")
    plink_url = case_obj.get("payment_link_url")

    # On-Demand Self-Healing: If link ID exists but URL is missing, resolve read-only
    if plink_id and not plink_url:
        resolved_url = fetch_payment_link_url(plink_id)
        if resolved_url:
            case_obj["payment_link_url"] = resolved_url
            update_case_payment_link_url(case_id, resolved_url)

    return {
        "status": "success",
        "case": case_obj,
        "audit": case_data["audit"],
        "attempts": case_data.get("attempts", []),
    }


@app.get("/recovery/audit-events")
def list_system_audit_events(limit: int = 100, case_id: Optional[str] = None):
    """
    Read-only endpoint returning system-wide chronological audit logs from SQLite.
    """
    events = get_all_audit_events(limit=limit, case_id=case_id)
    return {
        "status": "success",
        "count": len(events),
        "events": events,
    }



# ==========================================
# Human-Approval & Customer Notification Endpoints
# ==========================================

@app.post("/recovery/cases/{case_id}/approve", dependencies=[Depends(require_merchant_auth)])
def approve_recovery_case(case_id: str, request: Request, body: ApprovalRequest = ApprovalRequest()):
    """
    Human-in-the-loop approval endpoint.
    Transitions an approval-gated case to 'approved', authorizes execution,
    and automatically issues the Razorpay Test Mode Payment Link.
    Protected by Merchant API Key authentication.
    """
    check_rate_limit(request, bucket_name="case_approve", max_requests=30)
    updated_case, msg = approve_case(case_id, approver=body.approver or "admin", notes=body.notes)
    if not updated_case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "error", "message": msg},
        )
    if msg in ("Case is already executed.", "Case is in exhausted state; automated execution is permanently stopped."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": msg},
        )

    # Authorized: Execute approved recovery action
    decision = {
        "action": updated_case.get("decision_action") or "SEND_PAYMENT_LINK",
        "requires_human_approval": False,  # Granted by human
        "risk_case_id": updated_case["id"],
        "amount": updated_case.get("amount"),
        "currency": updated_case.get("currency", "INR"),
    }

    execution_result = execute_recovery_action(decision)
    update_execution_status(updated_case["id"], execution_result)

    if execution_result.get("status") == "executed":
        add_audit_event(
            case_id=updated_case["id"],
            event_type="PAYMENT_LINK_CREATED",
            message=f"Test Mode Payment Link generated post-approval: {execution_result.get('payment_link_id')}",
            metadata={
                "payment_link_id": execution_result.get("payment_link_id"),
                "payment_link_url": execution_result.get("payment_link_url"),
                "amount": execution_result.get("amount"),
            },
        )

    return {
        "status": "approved_and_executed",
        "case_id": updated_case["id"],
        "approval_message": msg,
        "execution_result": execution_result,
    }


@app.post("/recovery/cases/{case_id}/reject", dependencies=[Depends(require_merchant_auth)])
def reject_recovery_case(case_id: str, request: Request, body: RejectionRequest = RejectionRequest()):
    """
    Human-in-the-loop rejection endpoint.
    Marks the recovery case as rejected and prevents any automated recovery.
    Protected by Merchant API Key authentication.
    """
    check_rate_limit(request, bucket_name="case_reject", max_requests=30)
    updated_case, msg = reject_case(case_id, approver=body.approver or "admin", reason=body.reason)
    if not updated_case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "error", "message": msg},
        )

    return {
        "status": "rejected",
        "case_id": updated_case["id"],
        "message": "Recovery action was rejected by human reviewer.",
    }


@app.post("/recovery/cases/{case_id}/notify", dependencies=[Depends(require_merchant_auth)])
def notify_customer_for_case(case_id: str, request: Request, body: NotificationRequest = NotificationRequest()):
    """
    Dispatches a test-safe payment link notification to the customer with anti-spam deduplication.
    Protected by Merchant API Key authentication.
    """
    check_rate_limit(request, bucket_name="case_notify", max_requests=30)
    case = get_case_by_id(case_id)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "error", "message": f"Recovery case '{case_id}' not found."},
        )

    plink_url = case.get("payment_link_url")
    if not plink_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "message": "Cannot dispatch notification: No payment link has been generated for this case.",
            },
        )

    result = send_recovery_notification(
        case_id=case["id"],
        recipient=body.recipient or case.get("customer_id") or "customer@example.com",
        payment_link_url=plink_url,
        amount=case.get("amount", 0.0),
        currency=case.get("currency", "INR"),
        channel=body.channel or "EMAIL",
    )

    return {
        "status": result.get("status"),
        "notification_details": result,
    }


# ==========================================
# Batch Recovery Evaluation Endpoints (Isolated Simulation)
# ==========================================
class EvaluationRunRequest(BaseModel):
    num_cases: Optional[int] = 100
    seed: Optional[int] = 42
    mode: Optional[str] = "all"


@app.get("/evaluation/latest")
def get_latest_evaluation():
    """Retrieves the latest synthetic batch recovery evaluation report."""
    from app.evaluation_engine import get_latest_evaluation_report
    report = get_latest_evaluation_report()
    return {
        "status": "success",
        "data": report,
        "is_simulated_evaluation": True,
    }


@app.post("/evaluation/run", dependencies=[Depends(require_merchant_auth)])
def trigger_batch_evaluation(body: Optional[EvaluationRunRequest] = None):
    """Runs an isolated batch evaluation over the synthetic benchmark dataset."""
    from app.evaluation_engine import run_batch_evaluation
    req = body or EvaluationRunRequest()
    results = {}
    if req.mode in ["deterministic", "all"]:
        results["deterministic"] = run_batch_evaluation(
            num_cases=req.num_cases or 100,
            seed=req.seed or 42,
            mode="deterministic",
        )
    if req.mode in ["llm", "all"]:
        results["llm"] = run_batch_evaluation(
            num_cases=req.num_cases or 100,
            seed=req.seed or 42,
            mode="llm",
        )
    return {
        "status": "success",
        "mode": req.mode,
        "results": results,
        "is_simulated_evaluation": True,
    }


@app.get("/evaluation/contextual")
def get_latest_contextual_eval():
    """Retrieves the latest contextual AI intelligence evaluation report."""
    from app.contextual_evaluator import get_latest_contextual_evaluation
    report = get_latest_contextual_evaluation()
    return {
        "status": "success",
        "data": report,
        "is_simulated_evaluation": True,
    }


@app.post("/evaluation/contextual/run", dependencies=[Depends(require_merchant_auth)])
def trigger_contextual_eval():
    """Executes the 28-case contextual AI intelligence benchmark."""
    from app.contextual_evaluator import run_contextual_evaluation
    report = run_contextual_evaluation()
    return {
        "status": "success",
        "data": report,
        "is_simulated_evaluation": True,
    }


# ==========================================
# Mount Frontend Static Assets (if built)
# ==========================================
import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

frontend_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    @app.get("/dashboard", include_in_schema=False)
    @app.get("/dashboard/{full_path:path}", include_in_schema=False)
    def serve_frontend_dashboard():
        index_file = os.path.join(frontend_dist, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return {"service": "Dashboard build not found"}

