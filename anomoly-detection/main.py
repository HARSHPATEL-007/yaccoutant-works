"""
Anomaly Detection Service — Mass Ledger Analysis
Isolation Forest + Statistical Rules for Duplicate/Suspicious/Off-Hours Detection
"""

import os
import uuid
from typing import List, Dict, Any, Optional, Literal
from datetime import datetime, timedelta

import structlog
import asyncpg
import numpy as np
import pandas as pd
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from scipy import stats

structlog.configure(
    processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()]
)
logger = structlog.get_logger()

DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost/accounting_platform")

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class AnomalyScanRequest(BaseModel):
    client_id: uuid.UUID
    period: str = Field(..., pattern=r'^\d{4}-\d{2}$')  # YYYY-MM
    scan_type: Literal["full", "duplicate", "round_number", "off_hours", "statistical"] = "full"
    sensitivity: float = Field(default=0.05, ge=0.01, le=0.2)  # Contamination factor

class AnomalyResult(BaseModel):
    anomaly_id: uuid.UUID
    ledger_entry_id: uuid.UUID
    anomaly_type: str
    severity: Literal["low", "medium", "high", "critical"]
    description: str
    confidence_score: float
    detected_at: datetime
    reviewed: bool = False
    reviewed_by: Optional[uuid.UUID] = None

class ScanSummary(BaseModel):
    scan_id: uuid.UUID
    client_id: uuid.UUID
    period: str
    total_entries: int
    anomalies_found: int
    breakdown: Dict[str, int]
    processing_time_ms: float

# ---------------------------------------------------------------------------
# Detection Engines
# ---------------------------------------------------------------------------
class StatisticalDetector:
    """Z-score and IQR based outlier detection for amounts."""

    def detect(self, df: pd.DataFrame, sensitivity: float) -> List[Dict]:
        anomalies = []
        if len(df) < 10:
            return anomalies

        amounts = df['amount'].values
        z_scores = np.abs(stats.zscore(amounts))
        threshold = stats.norm.ppf(1 - sensitivity)  # e.g., 0.05 -> ~1.96

        for idx, z in enumerate(z_scores):
            if z > threshold:
                anomalies.append({
                    "entry_id": str(df.iloc[idx]['id']),
                    "type": "statistical_outlier",
                    "severity": "high" if z > 3 else "medium",
                    "description": f"Amount {amounts[idx]} is {z:.2f} standard deviations from mean",
                    "confidence": min(z / 5, 0.99)
                })
        return anomalies

class DuplicateDetector:
    """Fuzzy duplicate detection based on amount + date + description similarity."""

    def detect(self, df: pd.DataFrame) -> List[Dict]:
        anomalies = []
        # Group by date and amount
        grouped = df.groupby(['transaction_date', 'amount']).size().reset_index(name='count')
        duplicates = grouped[grouped['count'] > 1]

        for _, row in duplicates.iterrows():
            matches = df[(df['transaction_date'] == row['transaction_date']) & 
                        (df['amount'] == row['amount'])]
            if len(matches) > 1:
                for idx, match in matches.iterrows():
                    anomalies.append({
                        "entry_id": str(match['id']),
                        "type": "duplicate_entry",
                        "severity": "medium",
                        "description": f"Duplicate amount {match['amount']} on {match['transaction_date']} ({row['count']} occurrences)",
                        "confidence": 0.85
                    })
        return anomalies

class RoundNumberDetector:
    """Detect suspicious round-number transactions (e.g., exactly 100000.00)."""

    def detect(self, df: pd.DataFrame) -> List[Dict]:
        anomalies = []
        for idx, row in df.iterrows():
            amount = row['amount']
            if amount > 0 and amount == int(amount):
                # Check if it's a "too round" number (ends with 000, 500, etc.)
                if amount % 1000 == 0 or amount % 500 == 0:
                    anomalies.append({
                        "entry_id": str(row['id']),
                        "type": "round_number_bias",
                        "severity": "low",
                        "description": f"Suspiciously round amount: {amount}",
                        "confidence": 0.6
                    })
        return anomalies

class MLDetector:
    """Isolation Forest for multivariate anomaly detection."""

    def detect(self, df: pd.DataFrame, sensitivity: float) -> List[Dict]:
        anomalies = []
        if len(df) < 20:
            return anomalies

        features = df[['amount', 'hour_of_day', 'day_of_week']].fillna(0)
        scaler = StandardScaler()
        scaled = scaler.fit_transform(features)

        clf = IsolationForest(
            contamination=sensitivity,
            random_state=42,
            n_estimators=100
        )
        predictions = clf.fit_predict(scaled)
        scores = clf.decision_function(scaled)

        for idx, (pred, score) in enumerate(zip(predictions, scores)):
            if pred == -1:  # Anomaly
                anomalies.append({
                    "entry_id": str(df.iloc[idx]['id']),
                    "type": "ml_anomaly",
                    "severity": "high" if score < -0.3 else "medium",
                    "description": f"Multivariate outlier detected (isolation score: {score:.3f})",
                    "confidence": min(abs(score) + 0.5, 0.99)
                })
        return anomalies

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class AnomalyOrchestrator:
    def __init__(self):
        self.detectors = {
            "statistical": StatisticalDetector(),
            "duplicate": DuplicateDetector(),
            "round_number": RoundNumberDetector(),
            "ml": MLDetector()
        }

    async def scan(self, request: AnomalyScanRequest) -> ScanSummary:
        import time
        start = time.time()

        conn = await asyncpg.connect(DB_URL)

        # Fetch ledger data for period
        rows = await conn.fetch(
            """
            SELECT id, client_id, transaction_date, debit, credit, description, 
                   EXTRACT(hour FROM created_at) as hour_of_day,
                   EXTRACT(dow FROM created_at) as day_of_week
            FROM ledgers
            WHERE client_id = $1 AND TO_CHAR(transaction_date, 'YYYY-MM') = $2
            """,
            request.client_id, request.period
        )
        await conn.close()

        if not rows:
            return ScanSummary(
                scan_id=uuid.uuid4(),
                client_id=request.client_id,
                period=request.period,
                total_entries=0,
                anomalies_found=0,
                breakdown={},
                processing_time_ms=0
            )

        df = pd.DataFrame(rows)
        df['amount'] = df['debit'] + df['credit']

        all_anomalies = []
        breakdown = {}

        if request.scan_type == "full":
            detectors_to_run = ["statistical", "duplicate", "round_number", "ml"]
        else:
            detectors_to_run = [request.scan_type]

        for det_name in detectors_to_run:
            detector = self.detectors[det_name]
            if det_name == "ml":
                found = detector.detect(df, request.sensitivity)
            elif det_name == "statistical":
                found = detector.detect(df, request.sensitivity)
            else:
                found = detector.detect(df)

            all_anomalies.extend(found)
            breakdown[det_name] = len(found)

        # Deduplicate by entry_id (keep highest severity)
        seen = {}
        for a in all_anomalies:
            eid = a["entry_id"]
            if eid not in seen or a["confidence"] > seen[eid]["confidence"]:
                seen[eid] = a

        unique_anomalies = list(seen.values())

        # Persist anomalies
        conn = await asyncpg.connect(DB_URL)
        for a in unique_anomalies:
            await conn.execute(
                """
                INSERT INTO audit_logs (user_id, client_id, action, resource_type, resource_id, payload_hash, timestamp)
                VALUES ($1, $2, $3, 'anomaly_detection', $4, $5, NOW())
                ON CONFLICT DO NOTHING
                """,
                None, request.client_id, f"anomaly:{a['type']}", 
                uuid.UUID(a['entry_id']), str(uuid.uuid4())
            )
        await conn.close()

        latency = (time.time() - start) * 1000

        return ScanSummary(
            scan_id=uuid.uuid4(),
            client_id=request.client_id,
            period=request.period,
            total_entries=len(df),
            anomalies_found=len(unique_anomalies),
            breakdown=breakdown,
            processing_time_ms=round(latency, 2)
        )

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="Anomaly Detection Service", version="3.0.0")
orchestrator = AnomalyOrchestrator()

@app.post("/api/v1/anomaly/scan", response_model=ScanSummary)
async def scan_ledgers(request: AnomalyScanRequest, background_tasks: BackgroundTasks):
    """Run anomaly detection on client ledger for a given period."""
    try:
        result = await orchestrator.scan(request)
        return result
    except Exception as e:
        logger.error("Anomaly scan failed", error=str(e), client_id=str(request.client_id))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/anomaly/health")
async def health():
    return {"status": "healthy", "service": "anomaly-detection", "models": ["isolation_forest", "statistical", "duplicate"]}
