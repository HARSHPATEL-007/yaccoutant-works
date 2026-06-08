"""
Valuation Engine — ESOP Black-Scholes + 409A Valuation (Income/Market/Asset Approaches)
"""

import os
import uuid
from typing import List, Optional, Literal
from datetime import datetime, date
from math import log, exp, sqrt

import structlog
import asyncpg
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field, validator
from scipy.stats import norm
import numpy as np

structlog.configure(
    processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()]
)
logger = structlog.get_logger()

DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost/accounting_platform")

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------
class BlackScholesInput(BaseModel):
    strike_price: float = Field(..., gt=0)
    fair_market_value: float = Field(..., gt=0)
    volatility: float = Field(..., gt=0, le=2.0)  # Annualized, e.g., 0.45 for 45%
    time_to_expiry_years: float = Field(..., gt=0, le=10)
    risk_free_rate: float = Field(default=0.065, ge=0, le=0.2)  # India 10-yr bond approx
    dividend_yield: float = Field(default=0.0, ge=0)

    @validator('volatility')
    def validate_volatility(cls, v):
        if v > 1.5:
            logger.warning("High volatility detected", volatility=v)
        return v

class ESOPValuationOutput(BaseModel):
    valuation_id: uuid.UUID
    option_value_per_share: float
    total_grant_value: float
    d1: float
    d2: float
    methodology: str = "Black-Scholes-Merton"
    disclaimer: str = "This valuation is for ESOP accounting purposes only. A 409A valuation by a qualified appraiser is required for US tax compliance."
    computed_at: datetime

class Valuation409AInput(BaseModel):
    client_id: uuid.UUID
    valuation_date: date
    approach_weights: dict = Field(default={"income": 0.5, "market": 0.4, "asset": 0.1})

    # Income Approach (DCF)
    projected_cash_flows: List[float] = Field(..., min_length=3, max_length=10)
    terminal_growth_rate: float = Field(default=0.03, ge=0, le=0.1)
    wacc: float = Field(..., gt=0, le=0.5)

    # Market Approach
    comparable_ev_revenue: List[float] = Field(default_factory=list)
    comparable_ev_ebitda: List[float] = Field(default_factory=list)
    company_revenue: float = Field(default=0, ge=0)
    company_ebitda: float = Field(default=0)

    # Asset Approach
    net_asset_value: float = Field(default=0)

class Valuation409AOutput(BaseModel):
    valuation_id: uuid.UUID
    enterprise_value: float
    equity_value: float
    value_per_share: float
    approach_breakdown: dict
    discount_for_lack_of_marketability: float = Field(default=0.20)
    final_fair_market_value: float
    report_url: Optional[str] = None
    computed_at: datetime

# ---------------------------------------------------------------------------
# Black-Scholes Implementation
# ---------------------------------------------------------------------------
def black_scholes_call(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> tuple:
    """
    S: Current stock price (FMV)
    K: Strike price
    T: Time to expiry in years
    r: Risk-free rate
    sigma: Volatility
    q: Dividend yield
    Returns: (option_value, d1, d2)
    """
    d1 = (log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)

    call_price = S * exp(-q * T) * norm.cdf(d1) - K * exp(-r * T) * norm.cdf(d2)
    return call_price, d1, d2

# ---------------------------------------------------------------------------
# 409A Valuation Methods
# ---------------------------------------------------------------------------
def income_approach_dcf(cash_flows: List[float], terminal_growth: float, wacc: float) -> float:
    """Discounted Cash Flow to Firm."""
    pv = 0.0
    for i, cf in enumerate(cash_flows):
        pv += cf / ((1 + wacc) ** (i + 1))

    # Terminal value (Gordon Growth)
    terminal_value = cash_flows[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
    pv += terminal_value / ((1 + wacc) ** len(cash_flows))
    return round(pv, 2)

def market_approach(ev_revenue_multiples: List[float], ev_ebitda_multiples: List[float],
                   revenue: float, ebitda: float) -> float:
    """Guideline Public Company Method."""
    ev_rev = np.median(ev_revenue_multiples) * revenue if ev_revenue_multiples and revenue > 0 else 0
    ev_ebitda = np.median(ev_ebitda_multiples) * ebitda if ev_ebitda_multiples and ebitda > 0 else 0

    if ev_rev > 0 and ev_ebitda > 0:
        return round((ev_rev + ev_ebitda) / 2, 2)
    return round(ev_rev or ev_ebitda, 2)

def asset_approach(net_asset_value: float) -> float:
    """Adjusted Net Asset Method."""
    return round(net_asset_value, 2)

# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------
app = FastAPI(title="Valuation Engine — ESOP & 409A", version="2.0.0")

@app.post("/api/v1/valuation/esop", response_model=ESOPValuationOutput)
async def value_esop(input_data: BlackScholesInput, shares_granted: int = 10000):
    """Calculate ESOP fair value using Black-Scholes."""
    try:
        option_value, d1, d2 = black_scholes_call(
            S=input_data.fair_market_value,
            K=input_data.strike_price,
            T=input_data.time_to_expiry_years,
            r=input_data.risk_free_rate,
            sigma=input_data.volatility,
            q=input_data.dividend_yield
        )

        total_value = option_value * shares_granted

        return ESOPValuationOutput(
            valuation_id=uuid.uuid4(),
            option_value_per_share=round(option_value, 4),
            total_grant_value=round(total_value, 2),
            d1=round(d1, 6),
            d2=round(d2, 6),
            computed_at=datetime.utcnow()
        )
    except Exception as e:
        logger.error("ESOP valuation failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Valuation calculation error: {str(e)}")

@app.post("/api/v1/valuation/409a", response_model=Valuation409AOutput)
async def value_409a(input_data: Valuation409AInput, background_tasks: BackgroundTasks):
    """Full 409A valuation using three approaches."""
    try:
        weights = input_data.approach_weights

        # Income approach
        income_value = income_approach_dcf(
            input_data.projected_cash_flows,
            input_data.terminal_growth_rate,
            input_data.wacc
        ) if weights.get("income", 0) > 0 else 0

        # Market approach
        market_value = market_approach(
            input_data.comparable_ev_revenue,
            input_data.comparable_ev_ebitda,
            input_data.company_revenue,
            input_data.company_ebitda
        ) if weights.get("market", 0) > 0 else 0

        # Asset approach
        asset_value = asset_approach(input_data.net_asset_value) if weights.get("asset", 0) > 0 else 0

        # Weighted enterprise value
        total_weight = sum(weights.values())
        enterprise_value = (
            weights.get("income", 0) * income_value +
            weights.get("market", 0) * market_value +
            weights.get("asset", 0) * asset_value
        ) / total_weight

        # Apply DLOM (Discount for Lack of Marketability) — typical 15-25% for private companies
        dlom = 0.20
        equity_value = enterprise_value * (1 - dlom)

        # Assuming 10 million shares outstanding (configurable in production)
        shares_outstanding = 10_000_000
        value_per_share = equity_value / shares_outstanding

        result = Valuation409AOutput(
            valuation_id=uuid.uuid4(),
            enterprise_value=round(enterprise_value, 2),
            equity_value=round(equity_value, 2),
            value_per_share=round(value_per_share, 4),
            approach_breakdown={
                "income_approach": income_value,
                "market_approach": market_value,
                "asset_approach": asset_value,
                "weights": weights
            },
            discount_for_lack_of_marketability=dlom,
            final_fair_market_value=round(value_per_share, 4),
            computed_at=datetime.utcnow()
        )

        # Persist to database
        background_tasks.add_task(save_valuation, input_data.client_id, result)

        return result

    except Exception as e:
        logger.error("409A valuation failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Valuation calculation error: {str(e)}")

@app.get("/api/v1/valuation/health")
async def health():
    return {"status": "healthy", "service": "valuation-engine"}

async def save_valuation(client_id: uuid.UUID, result: Valuation409AOutput):
    try:
        conn = await asyncpg.connect(DB_URL)
        await conn.execute(
            """
            INSERT INTO esop_grants (client_id, valuation_method, fair_market_value, generated_at)
            VALUES ($1, $2, $3, NOW())
            """,
            client_id, "409A_composite", result.final_fair_market_value
        )
        await conn.close()
    except Exception as e:
        logger.error("Failed to save valuation", error=str(e))
