"""
Guardrails Service — Three-Layer Protection
1. Topical Guardrails (Accounting-only domain)
2. Hallucination Risk Scoring
3. Execution Constraint Validation
"""

import os
import re
from typing import Literal, List, Optional
from datetime import datetime

import structlog
from fastapi import FastAPI
from pydantic import BaseModel, Field
from transformers import pipeline

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
TOPIC_CLASSIFIER_MODEL = os.getenv("TOPIC_CLASSIFIER_MODEL", "facebook/bart-large-mnli")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.85"))

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ]
)
logger = structlog.get_logger()

# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------
class GuardrailsRequest(BaseModel):
    message: str = Field(..., max_length=4000)
    client_id: str
    domain: str = "accounting"

class GuardrailsResponse(BaseModel):
    allowed: bool
    topic_violation: bool = False
    hallucination_risk: Optional[str] = None
    confidence_score: float = 0.0
    reason: Optional[str] = None
    blocked_patterns: List[str] = Field(default_factory=list)

# -----------------------------------------------------------------------------
# Guardrails Engine
# -----------------------------------------------------------------------------
class GuardrailsEngine:
    ACCOUNTING_TOPICS = [
        "accounting", "taxation", "GST", "income tax", "corporate law",
        "financial advisory", "audit", "bookkeeping", "compliance",
        "FEMA", "DTAA", "transfer pricing", "ESOP", "valuation",
        "CMA report", "banking", "invoice", "ledger"
    ]
    
    BLOCKED_PATTERNS = [
        r"(execute|transfer|send|withdraw)\s+(₹|Rs\.?|INR)?\s*\d+",  # Financial transactions
        r"file\s+(gst|itr|tds)\s+return\s+(now|immediately)",  # Autonomous filing
        r"delete\s+(ledger|transaction|record)",  # Data destruction
        r"send\s+(email|sms|whatsapp)\s+to\s+(client|customer)",  # Unauthorized comms
    ]
    
    def __init__(self):
        self.classifier = pipeline(
            "zero-shot-classification",
            model=TOPIC_CLASSIFIER_MODEL,
            device=-1  # CPU
        )
    
    def check(self, request: GuardrailsRequest) -> GuardrailsResponse:
        # Layer 1: Topical Check
        topic_result = self.classifier(
            request.message,
            candidate_labels=self.ACCOUNTING_TOPICS + ["general_chat", "harmful", "irrelevant"]
        )
        
        top_label = topic_result["labels"][0]
        top_score = topic_result["scores"][0]
        
        is_accounting = top_label in self.ACCOUNTING_TOPICS and top_score > 0.6
        
        if not is_accounting:
            return GuardrailsResponse(
                allowed=False,
                topic_violation=True,
                confidence_score=top_score,
                reason=f"Query classified as '{top_label}' (score: {top_score:.2f}). "
                       "I can only assist with accounting and financial matters."
            )
        
        # Layer 2: Execution Guardrails (Pattern matching)
        blocked = []
        for pattern in self.BLOCKED_PATTERNS:
            if re.search(pattern, request.message, re.IGNORECASE):
                blocked.append(pattern)
        
        if blocked:
            return GuardrailsResponse(
                allowed=False,
                confidence_score=top_score,
                reason="Prohibited autonomous action detected. "
                       "I cannot execute financial transactions, file returns, or send communications without human approval.",
                blocked_patterns=blocked
            )
        
        # Layer 3: Hallucination Risk (Heuristic)
        # High risk if asking for specific section numbers without context
        hallucination_risk = None
        if re.search(r'section\s+\d+[A-Z]?\s+of\s+(IT Act|GST|FEMA)', request.message, re.IGNORECASE):
            if top_score < 0.8:
                hallucination_risk = "medium"
        
        # Calculate overall confidence
        confidence = top_score
        if hallucination_risk:
            confidence *= 0.8
        
        return GuardrailsResponse(
            allowed=True,
            topic_violation=False,
            hallucination_risk=hallucination_risk,
            confidence_score=round(confidence, 3),
            reason="All guardrails passed"
        )

# -----------------------------------------------------------------------------
# FastAPI
# -----------------------------------------------------------------------------
app = FastAPI(
    title="Guardrails Service",
    version="1.0.0",
    docs_url="/api/v1/guardrails/docs"
)

engine = GuardrailsEngine()

@app.post("/api/v1/guardrails/check", response_model=GuardrailsResponse)
async def check_guardrails(request: GuardrailsRequest):
    return engine.check(request)

@app.get("/api/v1/guardrails/health")
async def health():
    return {"status": "healthy", "model": TOPIC_CLASSIFIER_MODEL}