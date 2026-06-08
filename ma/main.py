"""
M&A Simulation Engine — Multi-Agent Scenario Modeling
Agents: Financial, Legal, Tax, Strategic
DCF / LBO / Accretion-Dilution Analysis
"""

import os
import uuid
from typing import List, Dict, Any, Optional, Literal
from dataclasses import dataclass, field
from datetime import datetime
from math import pow

import structlog
import httpx
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

structlog.configure(
    processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()]
)
logger = structlog.get_logger()

OPENAI_KEY = os.getenv("OPENAI_API_KEY")
LITELLM_URL = os.getenv("LITELLM_PROXY_URL")

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------
class TargetCompany(BaseModel):
    name: str
    revenue: float  # INR Crores
    ebitda: float
    net_debt: float
    shares_outstanding: float  # Millions
    growth_rate: float = Field(default=0.12, ge=0, le=0.5)
    tax_rate: float = Field(default=0.25, ge=0, le=0.5)
    sector: str

class AcquirerCompany(BaseModel):
    name: str
    revenue: float
    ebitda: float
    cash_available: float
    shares_outstanding: float
    current_share_price: float

class DealAssumptions(BaseModel):
    offer_premium: float = Field(default=0.25, ge=0, le=1.0)  # 25% premium
    synergy_revenue: float = Field(default=0.05, ge=0)  # 5% revenue synergy
    synergy_cost: float = Field(default=0.03, ge=0)  # 3% cost synergy
    financing_mix: Dict[str, float] = Field(default={"cash": 0.4, "debt": 0.4, "equity": 0.2})
    wacc: float = Field(default=0.12, ge=0.05, le=0.25)
    integration_cost: float = Field(default=50.0)  # INR Crores

class MAScenarioRequest(BaseModel):
    scenario_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    target: TargetCompany
    acquirer: AcquirerCompany
    assumptions: DealAssumptions
    simulation_years: int = Field(default=5, ge=3, le=10)

class AgentInsight(BaseModel):
    agent: Literal["financial", "legal", "tax", "strategic"]
    insight: str
    risk_score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)

class DCFOutput(BaseModel):
    enterprise_value: float
    equity_value: float
    implied_share_price: float
    premium_to_current: float
    npv_synergies: float

class LBOOutput(BaseModel):
    irr: float
    moic: float
    entry_ebitda_multiple: float
    exit_ebitda_multiple: float
    debt_paydown_schedule: List[float]

class MAScenarioResult(BaseModel):
    scenario_id: str
    dcf: DCFOutput
    lbo: Optional[LBOOutput] = None
    accretion_dilution: Dict[str, Any]
    agent_insights: List[AgentInsight]
    recommendation: str
    disclaimer: str = "This M&A simulation is for strategic planning only. Actual transaction outcomes depend on market conditions, regulatory approvals, and execution quality. Consult investment banking and legal advisors before proceeding."
    generated_at: datetime

# ---------------------------------------------------------------------------
# Financial Models
# ---------------------------------------------------------------------------
def dcf_valuation(target: TargetCompany, assumptions: DealAssumptions, years: int) -> DCFOutput:
    """Unlevered DCF with synergy value."""
    revenues = [target.revenue * pow(1 + target.growth_rate, i) for i in range(1, years + 1)]
    ebitdas = [r * (target.ebitda / target.revenue) for r in revenues]

    # Apply synergies from year 2 onwards
    for i in range(1, years):
        synergy_rev = revenues[i] * assumptions.synergy_revenue
        synergy_cost_savings = revenues[i] * assumptions.synergy_cost
        ebitdas[i] += synergy_rev + synergy_cost_savings

    # Taxes, D&A, CapEx, Working Capital (simplified)
    fcfs = []
    for ebitda in ebitdas:
        tax = ebitda * target.tax_rate
        capex = ebitda * 0.15
        wc_change = ebitda * 0.02
        fcfs.append(ebitda - tax - capex - wc_change)

    # Discount
    pv_fcfs = [fcfs[i] / pow(1 + assumptions.wacc, i + 1) for i in range(years)]
    terminal_value = fcfs[-1] * (1 + 0.03) / (assumptions.wacc - 0.03)
    pv_terminal = terminal_value / pow(1 + assumptions.wacc, years)

    enterprise_value = sum(pv_fcfs) + pv_terminal - assumptions.integration_cost
    equity_value = enterprise_value - target.net_debt
    implied_share = equity_value / target.shares_outstanding
    premium = (implied_share / (target.revenue * target.ebitda / target.shares_outstanding)) - 1  # Simplified baseline

    npv_synergies = sum([
        (revenues[i] * (assumptions.synergy_revenue + assumptions.synergy_cost)) / pow(1 + assumptions.wacc, i + 1)
        for i in range(1, years)
    ])

    return DCFOutput(
        enterprise_value=round(enterprise_value, 2),
        equity_value=round(equity_value, 2),
        implied_share_price=round(implied_share, 2),
        premium_to_current=round(premium, 2),
        npv_synergies=round(npv_synergies, 2)
    )

def lbo_model(target: TargetCompany, assumptions: DealAssumptions, years: int) -> LBOOutput:
    """Simplified LBO: entry at 8x EBITDA, exit at 7x, debt paydown from FCF."""
    entry_multiple = 8.0
    exit_multiple = 7.0
    entry_ev = target.ebitda * entry_multiple

    debt_ratio = assumptions.financing_mix.get("debt", 0.4)
    initial_debt = entry_ev * debt_ratio

    # Annual FCF approximation
    annual_fcf = target.ebitda * (1 - target.tax_rate) * 0.6
    debt_schedule = []
    remaining_debt = initial_debt

    for year in range(years):
        paydown = min(annual_fcf * 0.7, remaining_debt)  # 70% of FCF to debt
        remaining_debt -= paydown
        debt_schedule.append(round(remaining_debt, 2))

    exit_ev = target.ebitda * pow(1.05, years) * exit_multiple  # 5% EBITDA growth
    exit_equity = exit_ev - remaining_debt
    entry_equity = entry_ev * (1 - debt_ratio)

    moic = exit_equity / entry_equity if entry_equity > 0 else 0
    irr = pow(moic, 1 / years) - 1

    return LBOOutput(
        irr=round(irr, 4),
        moic=round(moic, 2),
        entry_ebitda_multiple=entry_multiple,
        exit_ebitda_multiple=exit_multiple,
        debt_paydown_schedule=debt_schedule
    )

def accretion_dilution(target: TargetCompany, acquirer: AcquirerCompany, assumptions: DealAssumptions) -> Dict:
    """EPS accretion/dilution analysis."""
    offer_price = (target.revenue * target.ebitda / target.shares_outstanding) * (1 + assumptions.offer_premium)
    total_consideration = offer_price * target.shares_outstanding

    cash_component = total_consideration * assumptions.financing_mix["cash"]
    debt_component = total_consideration * assumptions.financing_mix["debt"]
    equity_component = total_consideration * assumptions.financing_mix["equity"]

    new_shares = equity_component / acquirer.current_share_price if acquirer.current_share_price > 0 else 0

    combined_ebitda = target.ebitda + acquirer.ebitda + (target.revenue * assumptions.synergy_revenue)
    combined_net_income = combined_ebitda * (1 - target.tax_rate) - (debt_component * 0.08)  # 8% interest

    combined_shares = acquirer.shares_outstanding + new_shares
    pro_forma_eps = combined_net_income / combined_shares
    standalone_eps = (acquirer.ebitda * (1 - target.tax_rate)) / acquirer.shares_outstanding

    return {
        "standalone_eps": round(standalone_eps, 2),
        "pro_forma_eps": round(pro_forma_eps, 2),
        "accretion_dilution_pct": round((pro_forma_eps - standalone_eps) / standalone_eps * 100, 2),
        "new_shares_issued": round(new_shares, 2),
        "total_consideration": round(total_consideration, 2),
        "deal_multiple": round(total_consideration / target.ebitda, 2)
    }

# ---------------------------------------------------------------------------
# Multi-Agent LangGraph
# ---------------------------------------------------------------------------
class AgentState:
    def __init__(self, request: MAScenarioRequest, dcf: DCFOutput, lbo: LBOOutput, accretion: Dict):
        self.request = request
        self.dcf = dcf
        self.lbo = lbo
        self.accretion = accretion
        self.insights: List[AgentInsight] = []
        self.recommendation: str = ""

async def financial_agent(state: AgentState) -> AgentState:
    """Analyzes DCF/LBO outputs and financial viability."""
    llm = ChatOpenAI(model="gpt-4o", temperature=0, api_key=OPENAI_KEY, base_url=LITELLM_URL)

    prompt = f"""You are a senior M&A financial analyst. Review the following metrics and provide a concise insight (max 150 words):

Target: {state.request.target.name} (Sector: {state.request.target.sector})
DCF Enterprise Value: INR {state.dcf.enterprise_value} Cr
LBO IRR: {state.lbo.irr * 100:.1f}%
Accretion/Dilution: {state.accretion['accretion_dilution_pct']}%
Synergy NPV: INR {state.dcf.npv_synergies} Cr

Assess: (1) Valuation fairness, (2) Financing feasibility, (3) Key financial risks.
Return JSON: {{"insight": "...", "risk_score": 0.0-1.0, "confidence": 0.0-1.0}}"""

    try:
        resp = await llm.ainvoke(prompt)
        import json, re
        text = resp.content
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        data = json.loads(text.strip())
        state.insights.append(AgentInsight(
            agent="financial",
            insight=data.get("insight", "No insight generated"),
            risk_score=data.get("risk_score", 0.5),
            confidence=data.get("confidence", 0.8)
        ))
    except Exception as e:
        logger.error("Financial agent failed", error=str(e))
        state.insights.append(AgentInsight(agent="financial", insight="Analysis error", risk_score=0.5, confidence=0.5))
    return state

async def legal_agent(state: AgentState) -> AgentState:
    """Analyzes regulatory and legal risks (CCI, SEBI, sectoral caps)."""
    sector = state.request.target.sector
    risks = []

    if sector in ["defense", "telecom", "banking"]:
        risks.append("Sectoral approval required from relevant ministry")
    if state.request.target.revenue > 1000:  # > 1000 Cr triggers CCI
        risks.append("Mandatory CCI approval required (Section 5 & 6 of Competition Act)")

    risk_score = 0.7 if risks else 0.3
    insight = f"Legal risks: {'; '.join(risks)}" if risks else "No major regulatory red flags identified. Standard SEBI disclosure requirements apply."

    state.insights.append(AgentInsight(
        agent="legal",
        insight=insight,
        risk_score=risk_score,
        confidence=0.85
    ))
    return state

async def tax_agent(state: AgentState) -> AgentState:
    """Analyzes tax structuring and transfer pricing implications."""
    insight = (
        f"Transfer pricing analysis required for related-party transactions. "
        f"Section 92(1) of Income Tax Act mandates ALP determination. "
        f"Consider step-up basis for asset acquisition vs. stock purchase. "
        f"GST on slump sale vs. itemized sale needs evaluation."
    )
    state.insights.append(AgentInsight(
        agent="tax",
        insight=insight,
        risk_score=0.55,
        confidence=0.8
    ))
    return state

async def strategic_agent(state: AgentState) -> AgentState:
    """Synthesizes all insights into a recommendation."""
    avg_risk = np.mean([i.risk_score for i in state.insights])

    if avg_risk > 0.6:
        rec = "PROCEED WITH CAUTION: Elevated risk profile across financial, legal, and tax dimensions. Recommend phased diligence with kill criteria at 60 days."
    elif state.accretion["accretion_dilution_pct"] < -5:
        rec = "HOLD: Deal is EPS dilutive in base case. Renegotiate terms or identify additional cost synergies before proceeding."
    elif state.lbo.irr < 0.15:
        rec = "REVIEW STRUCTURE: LBO IRR below hurdle rate. Consider increasing equity component or reducing purchase price."
    else:
        rec = "PROCEED: Financial metrics support transaction. Execute comprehensive due diligence and secure financing commitments."

    state.recommendation = rec
    return state

# Build graph
graph = StateGraph(AgentState)
graph.add_node("financial", financial_agent)
graph.add_node("legal", legal_agent)
graph.add_node("tax", tax_agent)
graph.add_node("strategic", strategic_agent)

graph.set_entry_point("financial")
graph.add_edge("financial", "legal")
graph.add_edge("legal", "tax")
graph.add_edge("tax", "strategic")
graph.add_edge("strategic", END)

ma_workflow = graph.compile()

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="M&A Simulation Engine", version="3.0.0")

@app.post("/api/v1/ma/simulate", response_model=MAScenarioResult)
async def simulate_deal(request: MAScenarioRequest):
    """Run multi-agent M&A scenario simulation."""
    try:
        # Deterministic financial models
        dcf = dcf_valuation(request.target, request.assumptions, request.simulation_years)
        lbo = lbo_model(request.target, request.assumptions, request.simulation_years)
        accretion = accretion_dilution(request.target, request.acquirer, request.assumptions)

        # Multi-agent reasoning
        initial_state = AgentState(request, dcf, lbo, accretion)
        final_state = await ma_workflow.ainvoke(initial_state)

        return MAScenarioResult(
            scenario_id=request.scenario_id,
            dcf=dcf,
            lbo=lbo,
            accretion_dilution=accretion,
            agent_insights=final_state.insights,
            recommendation=final_state.recommendation,
            generated_at=datetime.utcnow()
        )

    except Exception as e:
        logger.error("M&A simulation failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/ma/health")
async def health():
    return {"status": "healthy", "service": "ma-simulation", "agents": ["financial", "legal", "tax", "strategic"]}
