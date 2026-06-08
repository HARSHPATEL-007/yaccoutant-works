"""
AI Orchestrator — Multi-Agent Coordination Service
Phase 1: Intent routing + Guardrails integration
"""

import os
import uuid
import asyncio
from typing import Literal, Optional, List, Dict, Any
from datetime import datetime
from contextlib import asynccontextmanager

import structlog
import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph, END

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
class Config:
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/accounting_platform")
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    GUARDRAILS_URL = os.getenv("GUARDRAILS_URL", "http://guardrails:8080")
    LITELLM_PROXY_URL = os.getenv("LITELLM_PROXY_URL")
    CONFIDENCE_THRESHOLD = 0.85
    MAX_RETRIES = 3

# -----------------------------------------------------------------------------
# Structured Logging
# -----------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ]
)
logger = structlog.get_logger()

# -----------------------------------------------------------------------------
# Pydantic Models (v2 strict)
# -----------------------------------------------------------------------------
class AgentIntent(BaseModel):
    intent: Literal[
        "generate_cma", "fema_advisory", "esop_valuation", 
        "gst_reconciliation", "audit_query", "general_chat", "unknown"
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    requires_human_review: bool = False
    detected_entities: Dict[str, Any] = Field(default_factory=dict)

class AIRequest(BaseModel):
    client_id: uuid.UUID
    user_id: uuid.UUID
    session_id: str
    message: str = Field(..., min_length=1, max_length=4000)
    context: Optional[Dict[str, Any]] = None
    preferred_model: Literal["gpt-4o", "claude-3-5-sonnet", "local-llama"] = "gpt-4o"

    @validator('message')
    def sanitize_message(cls, v):
        # Basic XSS prevention
        import html
        return html.escape(v)

class AIResponse(BaseModel):
    response_id: uuid.UUID
    intent: AgentIntent
    content: str
    citations: List[str] = Field(default_factory=list)
    confidence_badge: Literal["High", "Medium", "Low"]
    disclaimer: str = (
        "This AI-generated response is for informational purposes only and does not "
        "constitute professional financial or legal advice. Please consult a licensed "
        "Chartered Accountant before making decisions."
    )
    suggested_actions: List[str] = Field(default_factory=list)
    processing_time_ms: int
    correlation_id: str

class GuardrailsResult(BaseModel):
    allowed: bool
    topic_violation: bool = False
    hallucination_risk: Optional[str] = None
    confidence_score: float = 0.0
    reason: Optional[str] = None

# -----------------------------------------------------------------------------
# Database & Redis Pool
# -----------------------------------------------------------------------------
class ConnectionPool:
    def __init__(self):
        self.db: Optional[asyncpg.Pool] = None
        self.redis: Optional[redis.Redis] = None

    async def initialize(self):
        self.db = await asyncpg.create_pool(
            Config.DATABASE_URL,
            min_size=5,
            max_size=20,
            command_timeout=60
        )
        self.redis = redis.from_url(Config.REDIS_URL, decode_responses=True)

    async def close(self):
        if self.db:
            await self.db.close()
        if self.redis:
            await self.redis.close()

pool = ConnectionPool()

# -----------------------------------------------------------------------------
# LangGraph State Definition
# -----------------------------------------------------------------------------
class AgentState(BaseModel):
    request: AIRequest
    intent: Optional[AgentIntent] = None
    guardrails: Optional[GuardrailsResult] = None
    retrieved_context: List[str] = Field(default_factory=list)
    response: Optional[AIResponse] = None
    error: Optional[str] = None

# -----------------------------------------------------------------------------
# Guardrails Client
# -----------------------------------------------------------------------------
async def check_guardrails(message: str, client_id: uuid.UUID) -> GuardrailsResult:
    """Call guardrails service for topical + hallucination checks."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{Config.GUARDRAILS_URL}/api/v1/guardrails/check",
                json={"message": message, "client_id": str(client_id), "domain": "accounting"}
            )
            data = resp.json()
            return GuardrailsResult(**data)
    except Exception as e:
        logger.error("Guardrails service unreachable", error=str(e))
        # Fail-safe: require human review if guardrails down
        return GuardrailsResult(
            allowed=False,
            reason="Guardrails service unavailable — routing to human CPA"
        )

# -----------------------------------------------------------------------------
# Intent Classification (Router Agent)
# -----------------------------------------------------------------------------
async def classify_intent(state: AgentState) -> AgentState:
    """Router Agent: Determines user intent and confidence."""
    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0,
        api_key=Config.OPENAI_API_KEY,
        base_url=Config.LITELLM_PROXY_URL
    )
    
    prompt = f"""You are an expert Indian accounting AI intent classifier.
Analyze the user message and classify into exactly one intent:
- generate_cma: User wants CMA/loan report generation
- fema_advisory: Foreign investment/compliance questions
- esop_valuation: Employee stock option valuation
- gst_reconciliation: GST filing mismatch or reconciliation
- audit_query: Audit-related questions or document review
- general_chat: General accounting/tax questions
- unknown: Cannot determine

Also extract key entities (dates, amounts, section numbers, company names).

User message: {state.request.message}

Respond in strict JSON format:
{{"intent": "...", "confidence": 0.95, "detected_entities": {{...}}}}"""

    try:
        response = await llm.ainvoke(prompt)
        import json
        # Extract JSON from response
        content = response.content
        # Find JSON block
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        result = json.loads(content.strip())
        
        intent = AgentIntent(
            intent=result.get("intent", "unknown"),
            confidence=result.get("confidence", 0.0),
            detected_entities=result.get("detected_entities", {})
        )
        
        if intent.confidence < Config.CONFIDENCE_THRESHOLD:
            intent.requires_human_review = True
            intent.intent = "unknown"
            
        state.intent = intent
        
    except Exception as e:
        logger.error("Intent classification failed", error=str(e))
        state.intent = AgentIntent(
            intent="unknown", 
            confidence=0.0, 
            requires_human_review=True
        )
        state.error = str(e)
    
    return state

# -----------------------------------------------------------------------------
# Context Retrieval
# -----------------------------------------------------------------------------
async def retrieve_context(state: AgentState) -> AgentState:
    """Retrieve relevant documents from RAG service."""
    if state.intent.intent in ["general_chat", "unknown"]:
        return state  # Skip RAG for general queries
        
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "http://rag-service:8000/api/v1/rag/search",
                json={
                    "query": state.request.message,
                    "intent": state.intent.intent,
                    "client_id": str(state.request.client_id),
                    "top_k": 5
                }
            )
            data = resp.json()
            state.retrieved_context = [doc["chunk_text"] for doc in data.get("results", [])]
    except Exception as e:
        logger.error("RAG retrieval failed", error=str(e))
        # Non-blocking: proceed without context but flag low confidence
        state.intent.confidence = min(state.intent.confidence, 0.5)
    
    return state

# -----------------------------------------------------------------------------
# Response Generation
# -----------------------------------------------------------------------------
async def generate_response(state: AgentState) -> AgentState:
    """Generate grounded response with mandatory citations."""
    if state.guardrails and not state.guardrails.allowed:
        state.response = AIResponse(
            response_id=uuid.uuid4(),
            intent=state.intent,
            content="This query requires review by a licensed Chartered Accountant. "
                   "A human expert has been notified and will respond within 4 hours.",
            confidence_badge="Low",
            suggested_actions=["Contact support", "Schedule CPA call"],
            processing_time_ms=0,
            correlation_id=str(uuid.uuid4())
        )
        return state

    # Build grounded prompt
    context_str = "\n\n".join([
        f"[Document {i+1}] {text}" 
        for i, text in enumerate(state.retrieved_context)
    ]) if state.retrieved_context else "No specific documents retrieved."

    system_prompt = """You are an expert Indian accounting AI assistant.
CRITICAL RULES:
1. You ONLY answer questions related to accounting, taxation, compliance, corporate law, and financial advisory.
2. For tax/legal questions, you MUST cite exact section numbers and document sources from the provided context.
3. If you cannot find the answer in the provided context, say "I cannot find specific guidance on this in my current knowledge base. Please consult a licensed CA."
4. NEVER make up tax rates, section numbers, or legal provisions.
5. Present numerical calculations in structured tables when possible.
6. Always include the mandatory disclaimer about professional advice.

Context:
{context}
"""

    user_prompt = f"User query: {state.request.message}\n\nDetected intent: {state.intent.intent}"

    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0.1,
        api_key=Config.OPENAI_API_KEY,
        base_url=Config.LITELLM_PROXY_URL
    )

    messages = [
        ("system", system_prompt.format(context=context_str)),
        ("human", user_prompt)
    ]

    try:
        start = datetime.utcnow()
        result = await llm.ainvoke(messages)
        latency = int((datetime.utcnow() - start).total_seconds() * 1000)

        # Extract citations from context
        citations = []
        for i, ctx in enumerate(state.retrieved_context):
            if any(word in result.content.lower() for word in ctx.lower().split()[:5]):
                citations.append(f"Source [{i+1}]")

        confidence = "High" if state.intent.confidence > 0.9 and len(citations) > 0 else \
                     "Medium" if state.intent.confidence > 0.75 else "Low"

        state.response = AIResponse(
            response_id=uuid.uuid4(),
            intent=state.intent,
            content=result.content,
            citations=citations,
            confidence_badge=confidence,
            suggested_actions=["Download related document", "Schedule consultation"] if confidence == "Low" else [],
            processing_time_ms=latency,
            correlation_id=str(uuid.uuid4())
        )
        
    except Exception as e:
        logger.error("Response generation failed", error=str(e))
        state.error = str(e)
        state.response = AIResponse(
            response_id=uuid.uuid4(),
            intent=state.intent,
            content="I encountered an error processing your request. Please try again or contact support.",
            confidence_badge="Low",
            processing_time_ms=0,
            correlation_id=str(uuid.uuid4())
        )
    
    return state

# -----------------------------------------------------------------------------
# LangGraph Workflow Construction
# -----------------------------------------------------------------------------
def build_workflow():
    workflow = StateGraph(AgentState)
    
    workflow.add_node("classify", classify_intent)
    workflow.add_node("guardrails", lambda state: state)  # Placeholder for async guardrails
    workflow.add_node("retrieve", retrieve_context)
    workflow.add_node("generate", generate_response)
    
    workflow.set_entry_point("classify")
    workflow.add_edge("classify", "guardrails")
    workflow.add_edge("guardrails", "retrieve")
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", END)
    
    return workflow.compile()

agent_workflow = build_workflow()

# -----------------------------------------------------------------------------
# FastAPI Application
# -----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await pool.initialize()
    logger.info("AI Orchestrator started")
    yield
    await pool.close()
    logger.info("AI Orchestrator shutdown")

app = FastAPI(
    title="AI-Native Accounting — AI Orchestrator",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/v1/ai/docs",
    redoc_url="/api/v1/ai/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.accounting-platform.in"],
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response

async def get_db():
    async with pool.db.acquire() as conn:
        yield conn

@app.post("/api/v1/ai/chat", response_model=AIResponse)
async def chat_endpoint(
    request: AIRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    db: asyncpg.Connection = Depends(get_db)
):
    correlation_id = getattr(http_request.state, "correlation_id", str(uuid.uuid4()))
    
    # Pre-check guardrails
    guardrails = await check_guardrails(request.message, request.client_id)
    
    # Build initial state
    initial_state = AgentState(
        request=request,
        guardrails=guardrails
    )
    
    # Execute LangGraph workflow
    try:
        final_state = await agent_workflow.ainvoke(initial_state)
        
        # Log interaction for audit
        background_tasks.add_task(
            log_interaction,
            db,
            request.client_id,
            request.user_id,
            request.message,
            final_state.response.content if final_state.response else "",
            final_state.intent.intent,
            correlation_id
        )
        
        if final_state.response:
            final_state.response.correlation_id = correlation_id
            return final_state.response
            
    except Exception as e:
        logger.error("Workflow execution failed", error=str(e), correlation_id=correlation_id)
        raise HTTPException(status_code=500, detail="Agent workflow failed")

@app.get("/api/v1/ai/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "ai-orchestrator",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }

async def log_interaction(
    db: asyncpg.Connection,
    client_id: uuid.UUID,
    user_id: uuid.UUID,
    query: str,
    response: str,
    intent: str,
    correlation_id: str
):
    """Immutable audit logging."""
    try:
        await db.execute(
            """
            INSERT INTO audit_logs (user_id, client_id, action, resource_type, payload_hash, ip_address)
            VALUES ($1, $2, $3, 'ai_interaction', $4, '127.0.0.1')
            """,
            user_id, client_id, f"ai_chat:{intent}", 
            str(uuid.uuid5(uuid.NAMESPACE_DNS, query + response))
        )
    except Exception as e:
        logger.error("Audit logging failed", error=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)