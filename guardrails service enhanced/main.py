"""
Enhanced Guardrails Service — Phase 3 Full Suite
NeMo Guardrails + Guardrails AI + Custom Indian Accounting Constraints
"""

import os
import re
from typing import Literal, List, Optional, Dict, Any
from datetime import datetime

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import pipeline

structlog.configure(
    processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()]
)
logger = structlog.get_logger()

TOPIC_CLASSIFIER_MODEL = os.getenv("TOPIC_CLASSIFIER_MODEL", "facebook/bart-large-mnli")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.85"))
NEMO_RAILS_PATH = os.getenv("NEMO_RAILS_PATH", "/app/rails")

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class GuardrailsRequest(BaseModel):
    message: str = Field(..., max_length=4000)
    client_id: str
    domain: str = "accounting"
    user_role: Literal["admin", "ca", "accountant", "viewer", "auditor"] = "viewer"
    requested_action: Optional[str] = None

class GuardrailsResponse(BaseModel):
    allowed: bool
    topic_violation: bool = False
    hallucination_risk: Optional[str] = None
    execution_blocked: bool = False
    confidence_score: float = 0.0
    reason: Optional[str] = None
    blocked_patterns: List[str] = Field(default_factory=list)
    required_approvals: List[str] = Field(default_factory=list)
    audit_severity: Literal["low", "medium", "high", "critical"] = "low"

# ---------------------------------------------------------------------------
# NeMo Guardrails Configuration (YAML-based in production)
# ---------------------------------------------------------------------------
NEMO_CONFIG = """
colang_version: "1.0"

define user ask off_topic
    "What is the weather today?"
    "Tell me a joke"
    "How do I cook biryani?"

define bot respond off_topic
    "I can only assist with accounting, taxation, compliance, corporate law, and financial advisory matters."

define user request autonomous_action
    "File my GSTR-3B now"
    "Transfer money to vendor"
    "Delete all ledger entries"

define bot respond autonomous_action_blocked
    "I cannot perform autonomous actions such as filing returns, executing transfers, or modifying records. These require human approval with biometric or cryptographic signature."

define user ask specific_section
    "What is the tax rate under Section 115BAC?"

define bot respond section_uncertain
    "I will retrieve the exact text of this section from the official database before responding."
"""

# ---------------------------------------------------------------------------
# Guardrails Engine (Phase 3 Enhanced)
# ---------------------------------------------------------------------------
class GuardrailsEngine:
    ACCOUNTING_TOPICS = [
        "accounting", "taxation", "GST", "income tax", "corporate law",
        "financial advisory", "audit", "bookkeeping", "compliance",
        "FEMA", "DTAA", "transfer pricing", "ESOP", "valuation",
        "CMA report", "banking", "invoice", "ledger", "M&A",
        "ESG", "runway", "burn rate", "409A", "Black-Scholes"
    ]

    PROHIBITED_ACTIONS = {
        "file_gst": {"requires": ["biometric_approval", "ca_signature"], "severity": "critical"},
        "file_itr": {"requires": ["biometric_approval", "ca_signature"], "severity": "critical"},
        "transfer_funds": {"requires": ["hsm_signature", "dual_approval"], "severity": "critical"},
        "modify_ledger": {"requires": ["ca_signature", "audit_trail"], "severity": "high"},
        "delete_record": {"requires": ["hsm_signature", "compliance_officer"], "severity": "critical"},
        "send_client_communication": {"requires": ["manager_approval"], "severity": "medium"},
        "export_pii": {"requires": ["dpdp_consent", "audit_log"], "severity": "high"},
    }

    BLOCKED_PATTERNS = [
        r"(execute|transfer|send|withdraw|pay)\s+(Rs|INR)?\s*\d+",
        r"(file|submit)\s+(gst|itr|tds|gstr)\s+(return|now|immediately)",
        r"(delete|remove|wipe)\s+(ledger|transaction|record|entry)",
        r"(send|email|whatsapp|sms)\s+(client|customer|vendor)\s+(without|bypass)",
        r"(approve|authorize)\s+(all|every)\s+(transaction|entry)",
    ]

    def __init__(self):
        self.classifier = pipeline(
            "zero-shot-classification",
            model=TOPIC_CLASSIFIER_MODEL,
            device=-1
        )

    def check(self, request: GuardrailsRequest) -> GuardrailsResponse:
        topic_result = self.classifier(
            request.message,
            candidate_labels=self.ACCOUNTING_TOPICS + ["general_chat", "harmful", "irrelevant", "autonomous_action"]
        )

        top_label = topic_result["labels"][0]
        top_score = topic_result["scores"][0]

        is_accounting = top_label in self.ACCOUNTING_TOPICS and top_score > 0.6
        is_autonomous = top_label == "autonomous_action" or top_score < 0.4

        if not is_accounting and not is_autonomous:
            return GuardrailsResponse(
                allowed=False,
                topic_violation=True,
                confidence_score=top_score,
                reason=f"Query classified as '{top_label}' (score: {top_score:.2f}). I can only assist with accounting and financial matters."
            )

        blocked = []
        required_approvals = []
        execution_blocked = False
        severity = "low"

        for pattern in self.BLOCKED_PATTERNS:
            if re.search(pattern, request.message, re.IGNORECASE):
                blocked.append(pattern)
                execution_blocked = True
                severity = "critical"

        if request.requested_action and request.requested_action in self.PROHIBITED_ACTIONS:
            action_meta = self.PROHIBITED_ACTIONS[request.requested_action]
            required_approvals = action_meta["requires"]
            execution_blocked = True
            severity = action_meta["severity"]
            blocked.append(f"action:{request.requested_action}")

        if execution_blocked:
            return GuardrailsResponse(
                allowed=False,
                execution_blocked=True,
                confidence_score=top_score,
                reason="Prohibited autonomous action detected. This action requires human approval with biometric verification or HSM-backed cryptographic signature.",
                blocked_patterns=blocked,
                required_approvals=required_approvals,
                audit_severity=severity
            )

        hallucination_risk = None
        if re.search(r'section\s+\d+[A-Z]?\s+(of|under)', request.message, re.IGNORECASE):
            if top_score < 0.8:
                hallucination_risk = "medium"
            if top_score < 0.7:
                hallucination_risk = "high"

        if re.search(r'tax\s+rate\s+(for|in)\s+(FY\s+)?\d{4}[-\d{2}]?', request.message, re.IGNORECASE):
            if top_score < 0.85:
                hallucination_risk = "high"

        if re.search(r'penalty|fine|interest\s+rate\s+under\s+section', request.message, re.IGNORECASE):
            hallucination_risk = "medium"

        if request.user_role == "viewer" and request.requested_action in ["modify_ledger", "export_pii"]:
            return GuardrailsResponse(
                allowed=False,
                execution_blocked=True,
                reason="Viewer role does not have permission for this action. Contact your administrator.",
                audit_severity="high"
            )

        confidence = top_score
        if hallucination_risk == "high":
            confidence *= 0.6
        elif hallucination_risk == "medium":
            confidence *= 0.8

        if confidence < 0.6:
            return GuardrailsResponse(
                allowed=True,
                hallucination_risk=hallucination_risk or "high",
                confidence_score=round(confidence, 3),
                reason="Low confidence — response flagged for human CPA verification.",
                audit_severity="medium"
            )

        return GuardrailsResponse(
            allowed=True,
            topic_violation=False,
            hallucination_risk=hallucination_risk,
            confidence_score=round(confidence, 3),
            reason="All guardrails passed",
            audit_severity="low"
        )

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="Guardrails Service — Phase 3 Full Suite", version="3.0.0")
engine = GuardrailsEngine()

@app.post("/api/v1/guardrails/check", response_model=GuardrailsResponse)
async def check_guardrails(request: GuardrailsRequest):
    return engine.check(request)

@app.get("/api/v1/guardrails/nemo-config")
async def get_nemo_config():
    return {"config": NEMO_CONFIG.strip(), "version": "3.0.0"}

@app.get("/api/v1/guardrails/health")
async def health():
    return {
        "status": "healthy",
        "service": "guardrails-service",
        "model": TOPIC_CLASSIFIER_MODEL,
        "layers": ["topical", "execution", "hallucination", "rbac"],
        "phase": 3
    }
