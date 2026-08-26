import json
import logging
import re
from typing import Any, Dict, Optional
import requests

from app.config import settings
from app.recovery_decision import (
    ALLOWED_ACTIONS,
    HIGH_VALUE_THRESHOLD,
    decide_recovery_action,
)
from app.failure_classifier import (
    classify_payment_failure,
    CATEGORY_INSUFFICIENT_FUNDS,
    CATEGORY_CARD_LIMIT_EXCEEDED,
    CATEGORY_CARD_EXPIRED,
    CATEGORY_AUTHENTICATION_REQUIRED,
    CATEGORY_INVALID_CARD,
    CATEGORY_BANK_DECLINED,
    CATEGORY_TEMPORARY_GATEWAY_ERROR,
    CATEGORY_FRAUD_OR_SECURITY,
    CATEGORY_UNKNOWN,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an AI Revenue Recovery Specialist for a merchant using Razorpay.
Analyze the multi-dimensional payment failure context and return a structured JSON recovery recommendation.

ALLOWED ACTIONS:
- "SEND_PAYMENT_LINK": Reissue a direct Razorpay payment link for recoverable card/bank failures (e.g. insufficient funds, card limit reached, card expired, 3DS authentication timeout, or bank declines).
- "SEND_INVOICE": Issue a formal B2B invoice when subscription billing requires formal merchant invoice reconciliation.
- "WAIT": Pause automated recovery briefly when a temporary gateway timeout, network glitch, or bank server issue occurred, to prevent duplicate customer charges.
- "NO_ACTION": Halt automated recovery when payment was flagged for fraud, stolen card, or compliance/security restrictions. Requires human review.
- "INVESTIGATE": Flag for manual merchant investigation when payment details or amounts are missing/indeterminate.

CONTEXTUAL REASONING INSTRUCTIONS:
Evaluate all dimensions provided in the context payload:
1. Customer Profile: Consider customer tenure (months), previous successful payments, and time since last success. Loyal high-tenure customers warrant higher priority.
2. Recovery History: Consider previous failed attempts and prior ignored recovery links. If a customer has ignored multiple prior links, recommend escalation (escalation_recommended: true).
3. Transaction Urgency: Weigh financial exposure and recurring subscription impact. High-value transactions (>= ₹50,000) or high MRR risk warrant priority "HIGH".
4. Transient Failures: If a gateway timeout occurred with a successful payment just minutes prior, action MUST be "WAIT" with priority "LOW" to avoid duplicate customer charges.
5. Security / Fraud: If fraud indicators, stolen cards, or blacklisted instruments appear, action MUST be "NO_ACTION" with priority "HIGH" and escalation_recommended: true.

STRICT OUTPUT FORMAT:
Return ONLY a valid JSON object with the exact keys:
{
  "action": "SEND_PAYMENT_LINK" | "SEND_INVOICE" | "WAIT" | "NO_ACTION" | "INVESTIGATE",
  "confidence": 0.0 to 1.0,
  "urgency_score": 1 to 5,
  "priority": "LOW" | "MEDIUM" | "HIGH",
  "escalation_recommended": true | false,
  "contextual_factors_used": [
    "customer_tenure",
    "previous_successful_payments",
    "failed_attempt_count",
    "ignored_recovery_link",
    "high_value_transaction",
    "transient_gateway_error",
    "fraud_indicator"
  ],
  "reason": "Specific, factual rationale synthesizing the failure cause with customer tenure, retry history, and recovery urgency."
}
"""


class LLMProvider:
    """Base interface for LLM providers."""
    def generate_recommendation(self, prompt: str) -> Optional[str]:
        raise NotImplementedError


class GeminiProvider(LLMProvider):
    """Google Gemini API provider implementation using HTTP requests (Free Tier supported)."""
    def __init__(self, api_key: str, model: str = "gemini-3.6-flash"):
        self.api_key = api_key
        self.model = model
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

    def generate_recommendation(self, prompt: str) -> Optional[str]:
        if not self.api_key:
            return None
        
        headers = {
            "Content-Type": "application/json",
        }
        params = {
            "key": self.api_key,
        }
        payload = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.1,
            },
        }
        try:
            response = requests.post(self.api_url, params=params, headers=headers, json=payload, timeout=25)
            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text")
                return None
            else:
                if response.status_code == 429:
                    logger.info("[Gemini Provider] API rate limit reached (HTTP 429); engaging deterministic decision engine.")
                    return None
                if self.model == "gemini-3.6-flash":
                    logger.info(f"[Gemini Provider] Model {self.model} returned status {response.status_code}; trying gemini-3.5-flash-lite...")
                    fallback_prov = GeminiProvider(api_key=self.api_key, model="gemini-3.5-flash-lite")
                    return fallback_prov.generate_recommendation(prompt)
                logger.warning(f"[Gemini Provider] API returned status {response.status_code}: {response.text}")
                return None
        except Exception as exc:
            logger.warning(f"[Gemini Provider] API call failed: {exc}")
            return None


class OpenAIProvider(LLMProvider):
    """OpenAI API provider implementation using HTTP requests."""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.api_url = "https://api.openai.com/v1/chat/completions"

    def generate_recommendation(self, prompt: str) -> Optional[str]:
        if not self.api_key:
            return None
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        try:
            response = requests.post(self.api_url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.warning(f"OpenAI API returned status {response.status_code}: {response.text}")
                return None
        except Exception as exc:
            logger.warning(f"OpenAI provider call failed: {exc}")
            return None


def mask_customer_identifier(ident: Optional[str]) -> Optional[str]:
    """Masks customer email or identifier to prevent PII exposure to AI models."""
    if not ident:
        return "cust_anonymous"
    if "@" in ident:
        parts = ident.split("@")
        name = parts[0]
        domain = parts[1] if len(parts) > 1 else "example.com"
        masked_name = name[0] + "***" + (name[-1] if len(name) > 1 else "")
        return f"{masked_name}@{domain}"
    if len(ident) > 6:
        return ident[:3] + "***" + ident[-2:]
    return "cust_masked"


def build_sanitized_recovery_context(risk_case: Dict[str, Any]) -> Dict[str, Any]:
    """
    Constructs a comprehensive, sanitized contextual payload for the AI decision engine.
    Strictly includes non-sensitive business history while stripping all secrets, tokens, and PII.
    """
    failure_cat = risk_case.get("failure_category")
    failure_lbl = risk_case.get("failure_category_label")
    if not failure_cat:
        classified = classify_payment_failure(
            error_code=risk_case.get("error_code"),
            error_description=risk_case.get("error_description") or risk_case.get("risk_reason"),
            is_recurring=bool(risk_case.get("is_recurring_revenue")),
            amount=risk_case.get("amount"),
        )
        failure_cat = classified["category"]
        failure_lbl = classified["category_label"]

    raw_customer = risk_case.get("customer_id") or risk_case.get("customer_email") or risk_case.get("recipient")
    masked_customer = mask_customer_identifier(raw_customer)

    attempts_count = int(risk_case.get("payment_attempts_count") or 1)
    prev_failed = int(risk_case.get("previous_failed_attempts_count") or max(0, attempts_count - 1))
    prev_success = int(risk_case.get("previous_successful_payments_count") or 0)
    
    time_since_failed = risk_case.get("time_since_last_failed_attempt_hours")
    time_since_success = risk_case.get("time_since_last_successful_payment_days")
    prior_links = int(risk_case.get("prior_recovery_links_count") or 0)
    links_ignored = bool(risk_case.get("recovery_link_previously_ignored", False))
    has_active_link = bool(risk_case.get("has_active_recovery_link", False))
    is_escalated = bool(risk_case.get("is_escalated", False))
    tenure_months = risk_case.get("customer_tenure_months")

    context = {
        "transaction": {
            "payment_id": risk_case.get("payment_id"),
            "order_id": risk_case.get("order_id"),
            "subscription_id": risk_case.get("subscription_id"),
            "amount": risk_case.get("amount"),
            "currency": risk_case.get("currency", "INR"),
            "payment_status": risk_case.get("payment_status", "failed"),
            "is_recurring_revenue": bool(risk_case.get("is_recurring_revenue")),
            "is_high_value": bool(risk_case.get("amount") and float(risk_case.get("amount")) >= HIGH_VALUE_THRESHOLD),
        },
        "failure": {
            "category": failure_cat,
            "category_label": failure_lbl,
            "error_code": risk_case.get("error_code"),
            "error_description": risk_case.get("error_description") or risk_case.get("risk_reason"),
        },
        "customer_profile": {
            "masked_identifier": masked_customer,
            "tenure_months": tenure_months,
            "previous_successful_payments_count": prev_success,
            "time_since_last_successful_payment_days": time_since_success,
        },
        "recovery_history": {
            "current_case_attempt_count": attempts_count,
            "previous_failed_attempts_count": prev_failed,
            "time_since_last_failed_attempt_hours": time_since_failed,
            "has_active_recovery_link": has_active_link,
            "prior_recovery_links_count": prior_links,
            "prior_recovery_link_ignored": links_ignored,
            "is_escalated_to_ops": is_escalated,
        },
    }
    return context


def build_ai_prompt(risk_case: Dict[str, Any]) -> str:
    """
    Constructs a contextual business prompt from the sanitized recovery context.
    Directs the model to evaluate the failure reason within the customer history context.
    """
    safe_context = build_sanitized_recovery_context(risk_case)
    return (
        "Evaluate this payment failure case using both the failure cause and customer recovery history.\n"
        "Select the single most appropriate bounded action from [SEND_PAYMENT_LINK, SEND_INVOICE, WAIT, NO_ACTION, INVESTIGATE].\n"
        "Provide a clear, factual rationale explaining your recommendation in the context of the customer history.\n\n"
        f"Context Payload:\n{json.dumps(safe_context, indent=2)}"
    )


def enforce_ai_guardrails(ai_parsed: Dict[str, Any], risk_case: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enforces deterministic safety constraints and ground truth anchoring on raw AI outputs.
    Guarantees:
    - action is strictly one of the 5 allowed actions.
    - confidence is a valid float between 0.0 and 1.0.
    - high-value (>= ₹50,000) transactions require human approval.
    - fraud/security cases strictly force NO_ACTION and human approval.
    - temporary gateway errors force WAIT.
    - decision_source is set to "llm".
    """
    action = ai_parsed.get("action")
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"AI proposed unsupported action: '{action}'")

    raw_conf = ai_parsed.get("confidence", 0.85)
    try:
        confidence = max(0.0, min(1.0, float(raw_conf)))
    except (ValueError, TypeError):
        confidence = 0.85

    reason = str(ai_parsed.get("reason") or "AI recommended recovery action.")
    requires_approval = bool(ai_parsed.get("requires_human_approval", False))

    amount = risk_case.get("amount")
    currency = risk_case.get("currency", "INR")
    risk_case_id = risk_case.get("payment_id") or risk_case.get("event_id") or "unknown"
    error_desc = (
        risk_case.get("error_description")
        or risk_case.get("risk_reason")
        or ""
    ).lower()
    error_code = str(risk_case.get("error_code") or "").lower()
    failure_cat = risk_case.get("failure_category")

    # Parse Advisory Contextual Fields (bounded & sanitized)
    raw_urgency = ai_parsed.get("urgency_score", 3)
    try:
        urgency_score = max(1, min(5, int(raw_urgency)))
    except (ValueError, TypeError):
        urgency_score = 3

    raw_priority = str(ai_parsed.get("priority", "")).upper()
    if raw_priority in ("LOW", "MEDIUM", "HIGH"):
        priority = raw_priority
    else:
        priority = "HIGH" if urgency_score >= 4 else ("LOW" if urgency_score <= 2 else "MEDIUM")

    escalation_recommended = bool(ai_parsed.get("escalation_recommended", False))

    raw_factors = ai_parsed.get("contextual_factors_used", [])
    contextual_factors_used = []
    if isinstance(raw_factors, list):
        for f in raw_factors:
            if isinstance(f, str) and f.strip():
                clean_f = re.sub(r"[^a-z0-9_]", "_", f.strip().lower()).strip("_")
                if clean_f and clean_f not in contextual_factors_used:
                    contextual_factors_used.append(clean_f)

    # Guardrail 0: Exhaustion Guardrail - Gemini cannot override retry exhaustion stopping rules
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
        if "recovery_exhausted" not in contextual_factors_used:
            contextual_factors_used.append("recovery_exhausted")
        return {
            "action": "NO_ACTION",
            "confidence": 1.0,
            "urgency_score": 5,
            "priority": "HIGH",
            "escalation_recommended": True,
            "contextual_factors_used": contextual_factors_used,
            "reason": f"Automated recovery retry limit exhausted (attempts={attempts_count}, ignored_links={prior_links}). Automated recovery permanently stopped; manual merchant escalation required.",
            "requires_human_approval": True,
            "risk_case_id": risk_case_id,
            "amount": amount,
            "currency": currency,
            "failure_category": "RECOVERY_EXHAUSTED",
            "failure_category_label": "Recovery Exhausted",
            "decision_source": "llm",
        }

    # Guardrail 1: Indeterminate or missing amount must trigger INVESTIGATE
    if amount is None or amount <= 0 or risk_case.get("risk_status") == "needs_investigation":
        return {
            "action": "INVESTIGATE",
            "confidence": 0.60,
            "urgency_score": max(3, urgency_score),
            "priority": priority,
            "escalation_recommended": True,
            "contextual_factors_used": contextual_factors_used or ["missing_amount"],
            "reason": "Payment amount or critical failure metadata is indeterminate. Escalating for investigation.",
            "requires_human_approval": True,
            "risk_case_id": risk_case_id,
            "amount": amount,
            "currency": currency,
            "failure_category": failure_cat or CATEGORY_UNKNOWN,
            "failure_category_label": risk_case.get("failure_category_label"),
            "decision_source": "llm",
        }

    # Guardrail 2: Fraud / security / stolen card / blacklisted instruments MUST be NO_ACTION + approval + escalation
    fraud_indicators = ["fraud", "stolen", "blacklisted", "card_blocked", "blocked", "restricted", "security"]
    if (
        failure_cat == CATEGORY_FRAUD_OR_SECURITY
        or any(ind in error_desc for ind in fraud_indicators)
        or any(ind in error_code for ind in ["fraud", "blocked", "blacklist"])
    ):
        if "fraud_indicator" not in contextual_factors_used:
            contextual_factors_used.append("fraud_indicator")
        return {
            "action": "NO_ACTION",
            "confidence": max(0.95, confidence),
            "urgency_score": 5,
            "priority": "HIGH",
            "escalation_recommended": True,
            "contextual_factors_used": contextual_factors_used,
            "reason": reason if action == "NO_ACTION" else "Security or fraud indicator detected in payment failure metadata. Automated recovery halted.",
            "requires_human_approval": True,
            "risk_case_id": risk_case_id,
            "amount": amount,
            "currency": currency,
            "failure_category": CATEGORY_FRAUD_OR_SECURITY,
            "failure_category_label": "Security / Fraud Risk",
            "decision_source": "llm",
        }

    # Guardrail 3: Temporary Gateway Error must enforce WAIT action to prevent double charging
    if failure_cat == CATEGORY_TEMPORARY_GATEWAY_ERROR:
        if action != "WAIT":
            action = "WAIT"
            confidence = max(0.85, confidence)
            reason = "Temporary gateway or banking infrastructure glitch detected. Waiting before automated retry to prevent duplicate charges."
        urgency_score = min(urgency_score, 2)
        priority = "LOW"
        if "transient_gateway_error" not in contextual_factors_used:
            contextual_factors_used.append("transient_gateway_error")

    # Guardrail 4: High value transactions (>= ₹50,000) MUST mandate human approval and HIGH priority
    if amount >= HIGH_VALUE_THRESHOLD:
        requires_approval = True
        priority = "HIGH"
        urgency_score = max(4, urgency_score)
        if "high_value_transaction" not in contextual_factors_used:
            contextual_factors_used.append("high_value_transaction")

    return {
        "action": action,
        "confidence": confidence,
        "urgency_score": urgency_score,
        "priority": priority,
        "escalation_recommended": escalation_recommended,
        "contextual_factors_used": contextual_factors_used,
        "reason": reason,
        "requires_human_approval": requires_approval,
        "risk_case_id": risk_case_id,
        "amount": amount,
        "currency": currency,
        "failure_category": failure_cat,
        "failure_category_label": risk_case.get("failure_category_label"),
        "decision_source": "llm",
    }


def _make_deterministic_fallback(risk_case: Dict[str, Any]) -> Dict[str, Any]:
    """Formats a consistent decision dictionary when LLM provider is unavailable or fails."""
    fallback = decide_recovery_action(risk_case)
    amount = risk_case.get("amount") if isinstance(risk_case, dict) else None
    failure_cat = fallback.get("failure_category")
    is_high_val = amount is not None and amount >= HIGH_VALUE_THRESHOLD
    is_fraud = failure_cat == CATEGORY_FRAUD_OR_SECURITY
    is_gw = failure_cat == CATEGORY_TEMPORARY_GATEWAY_ERROR

    priority = "HIGH" if (is_high_val or is_fraud) else ("LOW" if is_gw else "MEDIUM")
    urgency = 5 if (is_high_val or is_fraud) else (1 if is_gw else 3)
    attempts = risk_case.get("payment_attempts_count", 1) if isinstance(risk_case, dict) else 1
    escalation = bool(is_fraud or is_high_val or attempts >= 3)

    fallback["urgency_score"] = urgency
    fallback["priority"] = priority
    fallback["escalation_recommended"] = escalation
    fallback["contextual_factors_used"] = []
    fallback["decision_source"] = "deterministic_fallback"
    return fallback


def ai_decide_recovery_action(
    risk_case: Dict[str, Any],
    llm_provider: Optional[LLMProvider] = None,
) -> Dict[str, Any]:
    """
    Coordinates AI reasoning over a structured revenue-risk case.
    If an AI provider is unavailable or produces invalid output, safely falls back
    to the deterministic rule engine (app.recovery_decision.decide_recovery_action).

    Returns:
        Structured recovery decision dictionary with decision_source="llm" or "deterministic_fallback".
    """
    if not isinstance(risk_case, dict) or not risk_case:
        return _make_deterministic_fallback(risk_case)

    # 1. Resolve Provider (Gemini Free Tier preferred, OpenAI supported)
    provider = llm_provider
    if provider is None:
        gemini_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
        if gemini_key:
            provider = GeminiProvider(api_key=gemini_key)
        elif settings.OPENAI_API_KEY:
            provider = OpenAIProvider(api_key=settings.OPENAI_API_KEY)

    # 2. If no LLM provider is available, use deterministic fallback
    if provider is None:
        logger.info("[AI Agent] No active LLM provider configured; using deterministic decision engine.")
        return _make_deterministic_fallback(risk_case)

    # 3. Build Sanitized Prompt & Query Provider
    prompt = build_ai_prompt(risk_case)
    try:
        raw_response = provider.generate_recommendation(prompt)
    except Exception as call_err:
        logger.warning(f"[AI Agent] LLM provider call threw exception ({call_err}); falling back.")
        return _make_deterministic_fallback(risk_case)

    if not raw_response:
        logger.warning("[AI Agent] Provider returned empty response; falling back to deterministic decision engine.")
        return _make_deterministic_fallback(risk_case)

    # 4. Parse JSON Response
    try:
        parsed_json = json.loads(raw_response)
        if not isinstance(parsed_json, dict):
            raise ValueError("AI output is not a JSON dictionary.")
    except Exception as parse_err:
        logger.warning(f"[AI Agent] Failed to parse AI response ({parse_err}); falling back.")
        return _make_deterministic_fallback(risk_case)

    # 5. Enforce Guardrails & Policy Anchor
    try:
        validated_decision = enforce_ai_guardrails(parsed_json, risk_case)
        return validated_decision
    except Exception as guard_err:
        logger.warning(f"[AI Agent] AI response violated guardrail ({guard_err}); falling back.")
        return _make_deterministic_fallback(risk_case)

