import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Default database path
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "recovery.db"
)


def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates a connection to the SQLite database with Row factory and WAL mode enabled."""
    path = db_path or DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except sqlite3.OperationalError:
        pass
    return conn


def _now_iso() -> str:
    """Returns current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: Optional[str] = None) -> None:
    """
    Initializes the SQLite database schema for recovery cases and audit trail.
    Safe to call multiple times (creates tables only IF NOT EXISTS).
    """
    conn = _get_connection(db_path)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recovery_cases (
                    id TEXT PRIMARY KEY,
                    event_id TEXT UNIQUE,
                    payment_id TEXT,
                    order_id TEXT,
                    subscription_id TEXT,
                    customer_id TEXT,
                    amount REAL,
                    currency TEXT,
                    payment_status TEXT,
                    is_recurring_revenue INTEGER DEFAULT 0,
                    risk_status TEXT,
                    risk_reason TEXT,
                    error_code TEXT,
                    error_description TEXT,
                    
                    decision_action TEXT,
                    decision_confidence REAL,
                    decision_reason TEXT,
                    requires_human_approval INTEGER DEFAULT 0,
                    decision_source TEXT,
                    
                    execution_status TEXT DEFAULT 'pending',
                    payment_link_id TEXT,
                    payment_link_url TEXT,
                    
                    recovered_amount REAL,
                    recovered_payment_id TEXT,
                    recovered_at TEXT,
                    
                    failure_category TEXT,
                    failure_category_label TEXT,
                    
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Backward-compatible column additions for existing databases
            for col_name, col_type in [
                ("recovered_amount", "REAL"),
                ("recovered_payment_id", "TEXT"),
                ("recovery_order_id", "TEXT"),
                ("recovered_at", "TEXT"),
                ("failure_category", "TEXT"),
                ("failure_category_label", "TEXT"),
                ("original_payment_link_id", "TEXT"),
                ("original_payment_link_url", "TEXT"),
                ("cancelled_payment_links", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE recovery_cases ADD COLUMN {col_name} {col_type}")
                except sqlite3.OperationalError:
                    pass  # Column already exists


            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (case_id) REFERENCES recovery_cases(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS payment_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    payment_id TEXT UNIQUE,
                    event_id TEXT,
                    order_id TEXT,
                    amount REAL,
                    currency TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    error_description TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (case_id) REFERENCES recovery_cases(id)
                )
            """)

            # Indices for fast lookup
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recovery_cases_event_id ON recovery_cases(event_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recovery_cases_payment_id ON recovery_cases(payment_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recovery_cases_order_id ON recovery_cases(order_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recovery_cases_subscription_id ON recovery_cases(subscription_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recovery_cases_payment_link_id ON recovery_cases(payment_link_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recovery_cases_orig_link_id ON recovery_cases(original_payment_link_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recovery_cases_recovered_payment_id ON recovery_cases(recovered_payment_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_case_id ON audit_events(case_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_attempts_case_id ON payment_attempts(case_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_attempts_payment_id ON payment_attempts(payment_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_attempts_order_id ON payment_attempts(order_id)")
    finally:
        conn.close()



def add_audit_event(
    case_id: str,
    event_type: str,
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> int:
    """
    Appends a new immutable audit record to the audit_events table.
    """
    conn = _get_connection(db_path)
    created_at = _now_iso()
    meta_json = json.dumps(metadata) if metadata is not None else None

    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO audit_events (case_id, event_type, message, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (case_id, event_type, message, meta_json, created_at),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def record_payment_attempt(
    case_id: str,
    payment_id: Optional[str] = None,
    event_id: Optional[str] = None,
    order_id: Optional[str] = None,
    amount: Optional[float] = None,
    currency: str = "INR",
    status: str = "failed",
    error_code: Optional[str] = None,
    error_description: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[int]:
    """
    Records an individual payment attempt against a recovery case.
    """
    if not payment_id and not event_id:
        return None
    conn = _get_connection(db_path)
    now = _now_iso()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO payment_attempts (
                    case_id, payment_id, event_id, order_id, amount,
                    currency, status, error_code, error_description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    payment_id,
                    event_id,
                    order_id,
                    amount,
                    currency,
                    status,
                    error_code,
                    error_description,
                    now,
                ),
            )
            return cursor.lastrowid
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def get_payment_attempts_for_case(
    case_id: str,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetches all payment attempts associated with a recovery case."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM payment_attempts 
            WHERE case_id = ? 
            ORDER BY id ASC
            """,
            (case_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def count_failed_attempts_for_case(case_id: str, db_path: Optional[str] = None) -> int:
    """Counts the number of non-successful payment attempts associated with a case."""
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM payment_attempts 
            WHERE case_id = ? AND status NOT IN ('captured', 'authorized', 'success', 'paid')
            """,
            (case_id,),
        ).fetchone()
        return int(row["cnt"]) if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def evaluate_case_exhaustion(
    case_dict: Dict[str, Any],
    db_path: Optional[str] = None,
    max_failed_attempts: Optional[int] = None,
    max_ignored_links: Optional[int] = None,
    ignored_timeout_hours: Optional[int] = None,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """
    Deterministically evaluates whether a recovery case has met retry exhaustion stopping rules.
    Rules:
      1. If already recovered -> Never exhaust (Financial truth wins).
      2. If already exhausted -> Return True.
      3. Condition A: failed_payment_attempt_count >= MAX_FAILED_ATTEMPTS (default 3) -> Exhaust.
      4. Condition B: prior_recovery_links_count >= MAX_IGNORED_RECOVERY_LINKS (default 2) AND
         recovery link(s) have remained unpaid/ignored for >= 48 hours -> Exhaust.
    """
    from app.config import settings
    limit_attempts = max_failed_attempts if max_failed_attempts is not None else settings.MAX_FAILED_ATTEMPTS
    limit_links = max_ignored_links if max_ignored_links is not None else settings.MAX_IGNORED_RECOVERY_LINKS
    timeout_hours = ignored_timeout_hours if ignored_timeout_hours is not None else settings.IGNORED_RECOVERY_TIMEOUT_HOURS

    case_id = case_dict.get("id") or case_dict.get("risk_case_id") or "unknown"
    exec_status = case_dict.get("execution_status")

    # Invariant: Recovered cases NEVER become exhausted
    if exec_status == "recovered":
        return False, None, {}

    # Already in terminal exhausted state
    if exec_status == "exhausted":
        failed_count = count_failed_attempts_for_case(case_id, db_path=db_path)
        return True, "Case is already in exhausted state.", {
            "failed_attempts": failed_count,
            "threshold": limit_attempts,
            "execution_status": "exhausted",
        }

    # Condition A: Check failed attempts at the case level
    failed_count = count_failed_attempts_for_case(case_id, db_path=db_path)
    payload_attempts = int(case_dict.get("payment_attempts_count") or case_dict.get("attempts_count") or 0)
    effective_failed_count = max(failed_count, payload_attempts)

    if effective_failed_count >= limit_attempts:
        reason = f"Maximum failed payment attempts reached ({effective_failed_count}/{limit_attempts})."
        return True, reason, {
            "condition": "failed_attempt_limit",
            "failed_attempts_count": effective_failed_count,
            "threshold": limit_attempts,
        }

    # Condition B: Repeated ignored recovery links (>= 48h timeout)
    prior_links = int(case_dict.get("prior_recovery_links_count") or 0)
    # Check if case has an active payment link and calculate time elapsed
    created_at_str = case_dict.get("created_at")
    hours_elapsed = 0.0
    if created_at_str:
        try:
            clean_str = created_at_str.replace("Z", "+00:00")
            created_dt = datetime.fromisoformat(clean_str)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            now_dt = datetime.now(timezone.utc)
            hours_elapsed = (now_dt - created_dt).total_seconds() / 3600.0
        except Exception:
            hours_elapsed = 0.0

    if "link_age_hours" in case_dict:
        hours_elapsed = float(case_dict["link_age_hours"])
    elif "hours_since_link_created" in case_dict:
        hours_elapsed = float(case_dict["hours_since_link_created"])

    if prior_links >= limit_links and hours_elapsed >= timeout_hours:
        reason = f"Recovery links ignored {prior_links} times exceeding timeout of {timeout_hours}h (active for {hours_elapsed:.1f}h)."
        return True, reason, {
            "condition": "ignored_recovery_links_timeout",
            "prior_recovery_links_count": prior_links,
            "hours_elapsed": round(hours_elapsed, 1),
            "timeout_hours": timeout_hours,
            "threshold": limit_links,
        }

    return False, None, {}


def exhaust_recovery_case(
    case_id: str,
    reason: str,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Transitions a recovery case to the terminal 'exhausted' execution state.
    1. Permanently halts automated recovery outreach for the obligation.
    2. Cancels eligible open/outstanding recovery Payment Links.
    3. Emits a structured RECOVERY_EXHAUSTED audit event.
    4. Preserves financial truth (does not change recovered cases, does not reset revenue at risk).
    """
    conn = _get_connection(db_path)
    now = _now_iso()
    try:
        case_row = conn.execute(
            "SELECT * FROM recovery_cases WHERE id = ? OR payment_id = ? OR event_id = ?",
            (case_id, case_id, case_id),
        ).fetchone()

        if not case_row:
            return None, "Case not found."

        case_dict = dict(case_row)
        actual_id = case_dict["id"]

        # Invariant: Never exhaust an already-recovered case
        if case_dict.get("execution_status") == "recovered":
            return case_dict, "Case is already recovered; exhaustion skipped."

        with conn:
            conn.execute(
                """
                UPDATE recovery_cases
                SET execution_status = 'exhausted',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, actual_id),
            )

        # Cancel eligible open recovery links for this case
        cancelled_links = cancel_open_payment_links_for_case(case_dict, db_path=db_path)
        if cancelled_links:
            with conn:
                existing_cancels = case_dict.get("cancelled_payment_links") or ""
                new_cancels = ",".join(cancelled_links)
                combined = f"{existing_cancels},{new_cancels}".strip(",")
                conn.execute(
                    "UPDATE recovery_cases SET cancelled_payment_links = ? WHERE id = ?",
                    (combined, actual_id),
                )

        audit_meta = {
            "reason": reason,
            "exhausted_at": now,
            "cancelled_links": cancelled_links,
            "case_id": actual_id,
            "order_id": case_dict.get("order_id"),
            **(metadata or {}),
        }

        add_audit_event(
            case_id=actual_id,
            event_type="RECOVERY_EXHAUSTED",
            message=f"Automated recovery permanently stopped (exhausted). Reason: {reason}",
            metadata=audit_meta,
            db_path=db_path,
        )

        updated = conn.execute("SELECT * FROM recovery_cases WHERE id = ?", (actual_id,)).fetchone()
        return dict(updated), "Case marked as exhausted."
    finally:
        conn.close()



def create_or_get_recovery_case(
    risk_case: Dict[str, Any],
    db_path: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    """
    Idempotently creates or updates a recovery case.
    Associates multiple payment attempts for the same order/subscription/link
    with ONE unified recovery case.

    Returns:
        Tuple of (case_dict, is_newly_created)
    """
    event_id = risk_case.get("event_id")
    payment_id = risk_case.get("payment_id")
    order_id = risk_case.get("order_id")
    subscription_id = risk_case.get("subscription_id")
    
    conn = _get_connection(db_path)
    now = _now_iso()
    failure_cat = risk_case.get("failure_category")
    failure_lbl = risk_case.get("failure_category_label")
    amount = risk_case.get("amount")
    currency = risk_case.get("currency", "INR")
    error_code = risk_case.get("error_code")
    error_desc = risk_case.get("error_description")

    try:
        existing = None
        # 1. Match by exact event_id if present
        if event_id:
            existing = conn.execute("SELECT * FROM recovery_cases WHERE event_id = ?", (event_id,)).fetchone()
        
        # 2. Match by exact payment_id if present
        if not existing and payment_id:
            existing = conn.execute("SELECT * FROM recovery_cases WHERE payment_id = ?", (payment_id,)).fetchone()

        # 3. Match by order_id (if unrecovered, same order = same recovery case)
        if not existing and order_id:
            existing = conn.execute(
                "SELECT * FROM recovery_cases WHERE order_id = ? AND execution_status != 'recovered' ORDER BY created_at DESC LIMIT 1",
                (order_id,),
            ).fetchone()

        # 4. Match by subscription_id (if unrecovered, same subscription = same recovery case)
        if not existing and subscription_id:
            existing = conn.execute(
                "SELECT * FROM recovery_cases WHERE subscription_id = ? AND execution_status != 'recovered' ORDER BY created_at DESC LIMIT 1",
                (subscription_id,),
            ).fetchone()

        # 5. Match by payment_link_id (if unrecovered, same payment link = same recovery case)
        payment_link_id = risk_case.get("payment_link_id") or risk_case.get("original_payment_link_id")
        payment_link_url = risk_case.get("payment_link_url") or risk_case.get("original_payment_link_url")
        if not existing and payment_link_id:
            existing = conn.execute(
                "SELECT * FROM recovery_cases WHERE (payment_link_id = ? OR original_payment_link_id = ?) AND execution_status != 'recovered' ORDER BY created_at DESC LIMIT 1",
                (payment_link_id, payment_link_id),
            ).fetchone()

        if existing:
            existing_dict = dict(existing)
            actual_case_id = existing_dict["id"]

            # Record this attempt in payment_attempts
            record_payment_attempt(
                case_id=actual_case_id,
                payment_id=payment_id,
                event_id=event_id,
                order_id=order_id,
                amount=amount,
                currency=currency,
                status=risk_case.get("payment_status", "failed"),
                error_code=error_code,
                error_description=error_desc,
                db_path=db_path,
            )

            # If existing case lacked payment link information but this attempt discovered it, update it
            if payment_link_id and not existing_dict.get("original_payment_link_id"):
                with conn:
                    conn.execute(
                        """
                        UPDATE recovery_cases
                        SET original_payment_link_id = COALESCE(original_payment_link_id, ?),
                            original_payment_link_url = COALESCE(original_payment_link_url, ?),
                            payment_link_id = COALESCE(payment_link_id, ?),
                            payment_link_url = COALESCE(payment_link_url, ?),
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (payment_link_id, payment_link_url, payment_link_id, payment_link_url, now, actual_case_id),
                    )

            # If this is an additional attempt (different payment ID), update case's latest attempt info
            if payment_id and existing_dict.get("payment_id") != payment_id:
                with conn:
                    conn.execute(
                        """
                        UPDATE recovery_cases
                        SET payment_id = ?,
                            error_code = COALESCE(?, error_code),
                            error_description = COALESCE(?, error_description),
                            failure_category = COALESCE(?, failure_category),
                            failure_category_label = COALESCE(?, failure_category_label),
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (payment_id, error_code, error_desc, failure_cat, failure_lbl, now, actual_case_id),
                    )
                updated_row = conn.execute("SELECT * FROM recovery_cases WHERE id = ?", (actual_case_id,)).fetchone()
                return dict(updated_row), False

            return existing_dict, False

        # If no existing case found, create a new one
        case_id = f"case_{order_id or payment_id or event_id or 'unknown'}"
        if not order_id and not payment_id and not event_id:
            case_id = f"case_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

        with conn:
            conn.execute(
                """
                INSERT INTO recovery_cases (
                    id, event_id, payment_id, order_id, subscription_id, customer_id,
                    amount, currency, payment_status, is_recurring_revenue,
                    risk_status, risk_reason, error_code, error_description,
                    failure_category, failure_category_label,
                    payment_link_id, payment_link_url,
                    original_payment_link_id, original_payment_link_url,
                    execution_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    event_id,
                    payment_id,
                    order_id,
                    subscription_id,
                    risk_case.get("customer_id"),
                    amount,
                    currency,
                    risk_case.get("payment_status", "failed"),
                    1 if risk_case.get("is_recurring_revenue") else 0,
                    risk_case.get("risk_status", "at_risk"),
                    risk_case.get("risk_reason"),
                    error_code,
                    error_desc,
                    failure_cat,
                    failure_lbl,
                    payment_link_id,
                    payment_link_url,
                    risk_case.get("original_payment_link_id") or payment_link_id,
                    risk_case.get("original_payment_link_url") or payment_link_url,
                    "pending",
                    now,
                    now,
                ),
            )

        # Record initial attempt in payment_attempts
        record_payment_attempt(
            case_id=case_id,
            payment_id=payment_id,
            event_id=event_id,
            order_id=order_id,
            amount=amount,
            currency=currency,
            status=risk_case.get("payment_status", "failed"),
            error_code=error_code,
            error_description=error_desc,
            db_path=db_path,
        )

        new_row = conn.execute("SELECT * FROM recovery_cases WHERE id = ?", (case_id,)).fetchone()
        return dict(new_row), True
    finally:
        conn.close()


def update_recovery_decision(
    case_id: str,
    decision: Dict[str, Any],
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Updates the recovery case with the recommended decision, failure category, and source.
    """
    conn = _get_connection(db_path)
    now = _now_iso()
    requires_approval = 1 if decision.get("requires_human_approval") else 0
    # Set execution status to approval_required if human approval is mandated
    initial_exec_status = "approval_required" if requires_approval else "pending"
    failure_cat = decision.get("failure_category")
    failure_lbl = decision.get("failure_category_label")

    try:
        with conn:
            conn.execute(
                """
                UPDATE recovery_cases
                SET decision_action = ?,
                    decision_confidence = ?,
                    decision_reason = ?,
                    requires_human_approval = ?,
                    decision_source = ?,
                    failure_category = COALESCE(?, failure_category),
                    failure_category_label = COALESCE(?, failure_category_label),
                    execution_status = CASE 
                        WHEN execution_status IN ('executed', 'rejected', 'approved', 'recovered', 'exhausted') THEN execution_status 
                        ELSE ? 
                    END,
                    updated_at = ?
                WHERE id = ? OR event_id = ? OR payment_id = ?
                """,
                (
                    decision.get("action"),
                    decision.get("confidence"),
                    decision.get("reason"),
                    requires_approval,
                    decision.get("decision_source", "deterministic"),
                    failure_cat,
                    failure_lbl,
                    initial_exec_status,
                    now,
                    case_id,
                    case_id,
                    case_id,
                ),
            )
        updated = conn.execute(
            "SELECT * FROM recovery_cases WHERE id = ? OR event_id = ? OR payment_id = ?",
            (case_id, case_id, case_id)
        ).fetchone()
        return dict(updated) if updated else None
    finally:
        conn.close()



def update_execution_status(
    case_id_or_payment_id: str,
    execution_result: Dict[str, Any],
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Updates execution outcome (executed, approval_required, failed) and payment link details.
    """
    conn = _get_connection(db_path)
    now = _now_iso()
    status = execution_result.get("status", "pending")
    plink_id = execution_result.get("payment_link_id")
    plink_url = execution_result.get("payment_link_url")

    try:
        with conn:
            conn.execute(
                """
                UPDATE recovery_cases
                SET execution_status = ?,
                    payment_link_id = COALESCE(?, payment_link_id),
                    payment_link_url = COALESCE(?, payment_link_url),
                    updated_at = ?
                WHERE id = ? OR payment_id = ? OR event_id = ?
                """,
                (
                    status,
                    plink_id,
                    plink_url,
                    now,
                    case_id_or_payment_id,
                    case_id_or_payment_id,
                    case_id_or_payment_id,
                ),
            )
        updated = conn.execute(
            "SELECT * FROM recovery_cases WHERE id = ? OR payment_id = ? OR event_id = ?",
            (case_id_or_payment_id, case_id_or_payment_id, case_id_or_payment_id),
        ).fetchone()
        return dict(updated) if updated else None
    finally:
        conn.close()


def update_case_payment_link_url(
    case_id_or_payment_id: str,
    payment_link_url: str,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Safely updates ONLY the payment_link_url field on a recovery case.
    Preserves all execution status, risk status, amounts, decisions, timestamps, and other state intact.
    """
    if not case_id_or_payment_id or not payment_link_url:
        return None
    conn = _get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                UPDATE recovery_cases
                SET payment_link_url = ?
                WHERE id = ? OR payment_id = ? OR event_id = ?
                """,
                (
                    payment_link_url,
                    case_id_or_payment_id,
                    case_id_or_payment_id,
                    case_id_or_payment_id,
                ),
            )
        updated = conn.execute(
            "SELECT * FROM recovery_cases WHERE id = ? OR payment_id = ? OR event_id = ?",
            (case_id_or_payment_id, case_id_or_payment_id, case_id_or_payment_id),
        ).fetchone()
        return dict(updated) if updated else None
    finally:
        conn.close()


def get_case_by_id(case_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetches a recovery case by its primary ID, payment_id, or event_id."""
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM recovery_cases WHERE id = ? OR payment_id = ? OR event_id = ?",
            (case_id, case_id, case_id)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_cases(limit: int = 50, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetches list of recent recovery cases ordered by created_at DESC."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM recovery_cases ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_case_with_audit(case_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Fetches a recovery case along with all its associated chronological audit events.
    """
    conn = _get_connection(db_path)
    try:
        case_row = conn.execute(
            "SELECT * FROM recovery_cases WHERE id = ? OR payment_id = ? OR event_id = ?",
            (case_id, case_id, case_id)
        ).fetchone()
        if not case_row:
            return None

        actual_case_id = case_row["id"]
        audit_rows = conn.execute(
            "SELECT id, event_type, message, metadata, created_at FROM audit_events WHERE case_id = ? ORDER BY id ASC",
            (actual_case_id,)
        ).fetchall()

        audit_list = []
        for a in audit_rows:
            meta = json.loads(a["metadata"]) if a["metadata"] else None
            audit_list.append({
                "id": a["id"],
                "event_type": a["event_type"],
                "message": a["message"],
                "metadata": meta,
                "created_at": a["created_at"],
            })

        attempt_rows = conn.execute(
            """
            SELECT id, case_id, payment_id, event_id, order_id, amount, currency, status, error_code, error_description, created_at
            FROM payment_attempts
            WHERE case_id = ?
            ORDER BY id ASC
            """,
            (actual_case_id,)
        ).fetchall()
        attempt_list = [dict(att) for att in attempt_rows]

        return {
            "case": dict(case_row),
            "audit": audit_list,
            "attempts": attempt_list,
        }
    finally:
        conn.close()


def get_all_audit_events(
    limit: int = 100,
    case_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetches chronological audit events across recovery cases, optionally filtered by case_id.
    """
    conn = _get_connection(db_path)
    try:
        if case_id:
            rows = conn.execute(
                """
                SELECT id, case_id, event_type, message, metadata, created_at
                FROM audit_events
                WHERE case_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (case_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, case_id, event_type, message, metadata, created_at
                FROM audit_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        events = []
        for r in rows:
            meta = json.loads(r["metadata"]) if r["metadata"] else None
            events.append({
                "id": r["id"],
                "case_id": r["case_id"],
                "event_type": r["event_type"],
                "message": r["message"],
                "metadata": meta,
                "created_at": r["created_at"],
            })
        return events
    finally:
        conn.close()



def approve_case(
    case_id: str,
    approver: str = "admin",
    notes: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Transitions a recovery case to 'approved' state in the database.
    Database is the single source of truth: rejects approval if case is already executed or not found.

    Returns:
        Tuple of (updated_case_dict, status_message)
    """
    conn = _get_connection(db_path)
    now = _now_iso()
    try:
        case_row = conn.execute(
            "SELECT * FROM recovery_cases WHERE id = ? OR payment_id = ? OR event_id = ?",
            (case_id, case_id, case_id)
        ).fetchone()

        if not case_row:
            return None, "Case not found."

        case_dict = dict(case_row)
        actual_id = case_dict["id"]

        if case_dict.get("execution_status") == "executed":
            return case_dict, "Case is already executed."

        if case_dict.get("execution_status") == "exhausted":
            return case_dict, "Case is in exhausted state; automated execution is permanently stopped."

        with conn:
            conn.execute(
                """
                UPDATE recovery_cases
                SET execution_status = 'approved',
                    requires_human_approval = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, actual_id),
            )

        add_audit_event(
            case_id=actual_id,
            event_type="HUMAN_APPROVAL_GRANTED",
            message=f"Human approval granted by '{approver}'. Notes: {notes or 'No notes provided.'}",
            metadata={"approver": approver, "notes": notes or ""},
            db_path=db_path,
        )

        updated = conn.execute("SELECT * FROM recovery_cases WHERE id = ?", (actual_id,)).fetchone()
        return dict(updated), "Approval recorded."
    finally:
        conn.close()


def reject_case(
    case_id: str,
    approver: str = "admin",
    reason: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Transitions a recovery case to 'rejected' state in the database.
    Prevents any automated recovery link execution.

    Returns:
        Tuple of (updated_case_dict, status_message)
    """
    conn = _get_connection(db_path)
    now = _now_iso()
    try:
        case_row = conn.execute(
            "SELECT * FROM recovery_cases WHERE id = ? OR payment_id = ? OR event_id = ?",
            (case_id, case_id, case_id)
        ).fetchone()

        if not case_row:
            return None, "Case not found."

        case_dict = dict(case_row)
        actual_id = case_dict["id"]

        with conn:
            conn.execute(
                """
                UPDATE recovery_cases
                SET execution_status = 'rejected',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, actual_id),
            )

        add_audit_event(
            case_id=actual_id,
            event_type="HUMAN_APPROVAL_REJECTED",
            message=f"Human reviewer '{approver}' rejected recovery. Reason: {reason or 'No reason specified.'}",
            metadata={"approver": approver, "reason": reason},
            db_path=db_path,
        )

        updated = conn.execute("SELECT * FROM recovery_cases WHERE id = ?", (actual_id,)).fetchone()
        return dict(updated), "Case rejected."
    finally:
        conn.close()


def cancel_open_payment_links_for_case(
    case_dict: Dict[str, Any],
    paid_link_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[str]:
    """
    Safely cancels any open/outstanding Payment Links associated with the recovery case
    once a payment has successfully reconciled it.
    Does NOT cancel the paid link.
    Never raises an exception (resilient to API / network errors).
    """
    case_id = case_dict.get("id")
    if not case_id:
        return []

    # Gather all known payment link candidates
    link_candidates = set()
    for key in ["payment_link_id", "original_payment_link_id"]:
        val = case_dict.get(key)
        if val and isinstance(val, str) and val.startswith("plink_"):
            link_candidates.add(val)

    # Don't cancel the link that was paid!
    if paid_link_id and paid_link_id in link_candidates:
        link_candidates.remove(paid_link_id)

    cancelled_links = []
    for link_id in link_candidates:
        try:
            from app.recovery_executor import cancel_payment_link
            res = cancel_payment_link(link_id)
            if res.get("status") == "cancelled":
                cancelled_links.append(link_id)
                add_audit_event(
                    case_id=case_id,
                    event_type="PAYMENT_LINK_CANCELLED_AFTER_RECOVERY",
                    message=f"Outstanding Payment Link '{link_id}' was automatically cancelled after recovery succeeded.",
                    metadata={
                        "cancelled_payment_link_id": link_id,
                        "paid_payment_link_id": paid_link_id,
                    },
                    db_path=db_path,
                )
            elif res.get("status") == "failed":
                add_audit_event(
                    case_id=case_id,
                    event_type="PAYMENT_LINK_CANCELLATION_SKIPPED",
                    message=f"Could not cancel outstanding Payment Link '{link_id}': {res.get('error')}",
                    metadata={
                        "payment_link_id": link_id,
                        "error": res.get("error"),
                    },
                    db_path=db_path,
                )
        except Exception as e:
            logger.warning(f"[Cancel Error] Failed to cancel {link_id}: {e}")

    return cancelled_links


def reconcile_recovery_payment(
    case_id_or_link_id: str,
    recovered_payment_id: str,
    recovered_amount: float,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Reconciles a successful Razorpay payment with an existing recovery case in SQLite.
    Enforces idempotency, double-payment protection, and server-side verification.
    """
    conn = _get_connection(db_path)
    now = _now_iso()
    try:
        # Search by Case ID, payment_link_id, original_payment_link_id, payment_id, event_id, or order_id
        case_row = conn.execute(
            """
            SELECT * FROM recovery_cases 
            WHERE id = ? OR payment_link_id = ? OR original_payment_link_id = ? OR payment_id = ? OR event_id = ? OR order_id = ?
            """,
            (case_id_or_link_id, case_id_or_link_id, case_id_or_link_id, case_id_or_link_id, case_id_or_link_id, case_id_or_link_id)
        ).fetchone()

        if not case_row:
            return None, "Case not found."

        case_dict = dict(case_row)
        actual_id = case_dict["id"]

        # 1. Exact Idempotency Check: if this exact payment ID was already reconciled
        if case_dict.get("recovered_payment_id") == recovered_payment_id:
            return case_dict, "already_reconciled"

        # 2. Duplicate / Double-Payment Protection:
        # If the case is already recovered via a DIFFERENT payment ID, record it as a potential double payment
        # without double-counting the revenue!
        if case_dict.get("execution_status") == "recovered":
            existing_rec_id = case_dict.get("recovered_payment_id")
            
            # Record attempt
            record_payment_attempt(
                case_id=actual_id,
                payment_id=recovered_payment_id,
                amount=recovered_amount,
                currency=case_dict.get("currency", "INR"),
                status="captured_duplicate",
                db_path=db_path,
            )

            add_audit_event(
                case_id=actual_id,
                event_type="DUPLICATE_PAYMENT_DETECTED",
                message=(
                    f"Warning: Potential duplicate payment received! Payment '{recovered_payment_id}' for ₹{recovered_amount:.2f} "
                    f"was captured on already-recovered case '{actual_id}' (originally recovered via '{existing_rec_id}')."
                ),
                metadata={
                    "duplicate_payment_id": recovered_payment_id,
                    "existing_payment_id": existing_rec_id,
                    "amount": recovered_amount,
                    "currency": case_dict.get("currency", "INR"),
                    **(metadata or {}),
                },
                db_path=db_path,
            )

            return case_dict, "duplicate_payment_recorded"

        # 3. Standard First-Time Reconciliation
        recovery_order_id = metadata.get("order_id") if metadata else None
        with conn:
            conn.execute(
                """
                UPDATE recovery_cases
                SET execution_status = 'recovered',
                    risk_status = 'recovered',
                    recovered_amount = ?,
                    recovered_payment_id = ?,
                    recovery_order_id = COALESCE(?, recovery_order_id),
                    recovered_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (recovered_amount, recovered_payment_id, recovery_order_id, now, now, actual_id),
            )

        # Record successful attempt in payment_attempts
        record_payment_attempt(
            case_id=actual_id,
            payment_id=recovered_payment_id,
            order_id=recovery_order_id or case_dict.get("order_id"),
            amount=recovered_amount,
            currency=case_dict.get("currency", "INR"),
            status="captured",
            db_path=db_path,
        )

        meta = {
            "recovered_payment_id": recovered_payment_id,
            "recovered_amount": recovered_amount,
            "currency": case_dict.get("currency", "INR"),
            "original_order_id": case_dict.get("order_id"),
            "recovery_order_id": recovery_order_id,
            **(metadata or {}),
        }

        add_audit_event(
            case_id=actual_id,
            event_type="RECOVERY_PAYMENT_DETECTED",
            message=f"Successful recovery payment detected for amount ₹{recovered_amount:.2f} (Payment ID: {recovered_payment_id}).",
            metadata=meta,
            db_path=db_path,
        )

        add_audit_event(
            case_id=actual_id,
            event_type="RECOVERY_CASE_RECONCILED",
            message=f"Payment {recovered_payment_id} successfully reconciled with recovery case '{actual_id}'.",
            metadata={
                "payment_link_id": case_dict.get("payment_link_id"),
                "original_order_id": case_dict.get("order_id"),
                "recovery_order_id": recovery_order_id,
            },
            db_path=db_path,
        )

        add_audit_event(
            case_id=actual_id,
            event_type="REVENUE_RECOVERED",
            message=f"Revenue of ₹{recovered_amount:.2f} successfully recovered and confirmed.",
            metadata={"recovered_at": now},
            db_path=db_path,
        )

        # 4. Safely cancel remaining open links associated with this case
        paid_link_id = (metadata or {}).get("payment_link_id")
        cancelled_links = cancel_open_payment_links_for_case(case_dict, paid_link_id=paid_link_id, db_path=db_path)
        if cancelled_links:
            with conn:
                existing_cancels = case_dict.get("cancelled_payment_links") or ""
                new_cancels = ",".join(cancelled_links)
                combined = f"{existing_cancels},{new_cancels}".strip(",")
                conn.execute(
                    "UPDATE recovery_cases SET cancelled_payment_links = ? WHERE id = ?",
                    (combined, actual_id),
                )

        updated = conn.execute("SELECT * FROM recovery_cases WHERE id = ?", (actual_id,)).fetchone()
        return dict(updated), "reconciled"
    finally:
        conn.close()


def _classify_case_provenance(case_id: str, payment_id: Optional[str] = None, order_id: Optional[str] = None) -> str:
    """Classifies a recovery case into an operational provenance bucket."""
    c_id = case_id or ""
    p_id = payment_id or ""
    o_id = order_id or ""
    if c_id.startswith("case_demo_"):
        return "demo"
    elif any(prefix in c_id or prefix in p_id or prefix in o_id for prefix in ["TT", "TU4", "TU5"]):
        return "razorpay_test"
    else:
        return "internal_test"


def get_dashboard_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Computes key performance metrics for the Merchant Revenue Recovery Dashboard overview.
    - total_revenue_at_risk: CURRENT UNRESOLVED revenue at risk (cases where execution_status != 'recovered').
    - recovered_revenue: Confirmed captured and reconciled revenue (execution_status = 'recovered').
    - historical_exposure: Total volume of at-risk revenue ingested (current_unresolved + recovered_revenue).
    - recovery_rate_percentage: recovered_revenue / historical_exposure * 100.
    - risk_provenance: Provenance breakdown of unresolved revenue at risk (razorpay_test, demo, internal_test).
    - recovered_provenance: Provenance breakdown of recovered revenue (razorpay_test, demo, internal_test).
    """
    conn = _get_connection(db_path)
    try:
        total_cases = conn.execute("SELECT COUNT(*) AS cnt FROM recovery_cases").fetchone()["cnt"]
        
        at_risk_row = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0.0) AS total 
            FROM recovery_cases 
            WHERE (risk_status = 'at_risk' OR risk_status IS NULL) 
              AND execution_status != 'recovered'
            """
        ).fetchone()
        current_unresolved_at_risk = float(at_risk_row["total"])

        recovered_row = conn.execute(
            """
            SELECT COALESCE(SUM(recovered_amount), 0.0) AS total 
            FROM recovery_cases 
            WHERE execution_status = 'recovered'
            """
        ).fetchone()
        recovered_revenue = float(recovered_row["total"])

        pending_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt 
            FROM recovery_cases 
            WHERE (requires_human_approval = 1 OR execution_status = 'approval_required')
              AND execution_status != 'recovered'
            """
        ).fetchone()
        pending_approvals = int(pending_row["cnt"])

        # Historical exposure = current unresolved at-risk + confirmed recovered revenue
        historical_exposure = current_unresolved_at_risk + recovered_revenue
        recovery_rate = (
            round((recovered_revenue / historical_exposure) * 100.0, 1)
            if historical_exposure > 0
            else 0.0
        )

        # Compute provenance breakdown
        risk_provenance = {"razorpay_test": 0.0, "demo": 0.0, "internal_test": 0.0}
        recovered_provenance = {"razorpay_test": 0.0, "demo": 0.0, "internal_test": 0.0}

        all_cases = conn.execute(
            "SELECT id, payment_id, order_id, amount, recovered_amount, execution_status, risk_status FROM recovery_cases"
        ).fetchall()
        for c in all_cases:
            orig = _classify_case_provenance(c["id"], c["payment_id"], c["order_id"])
            amt = float(c["amount"] or 0.0)
            rec_amt = float(c["recovered_amount"] or 0.0)
            st = c["execution_status"]
            r_st = c["risk_status"]

            if (r_st == "at_risk" or r_st is None) and st != "recovered":
                risk_provenance[orig] = risk_provenance.get(orig, 0.0) + amt

            if st == "recovered":
                recovered_provenance[orig] = recovered_provenance.get(orig, 0.0) + rec_amt

        for k in risk_provenance:
            risk_provenance[k] = round(risk_provenance[k], 2)
        for k in recovered_provenance:
            recovered_provenance[k] = round(recovered_provenance[k], 2)

        return {
            "total_cases": total_cases,
            "total_revenue_at_risk": current_unresolved_at_risk,
            "recovered_revenue": recovered_revenue,
            "historical_exposure": historical_exposure,
            "pending_approvals": pending_approvals,
            "recovery_rate_percentage": recovery_rate,
            "risk_provenance": risk_provenance,
            "recovered_provenance": recovered_provenance,
        }
    finally:
        conn.close()
