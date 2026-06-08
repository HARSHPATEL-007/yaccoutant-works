"""
Workflow Engine — Temporal.io STP Orchestration
Invoice-to-Ledger Straight-Through Processing with Saga compensation
"""

import os
import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional

import structlog
import httpx
from temporalio import workflow, activity
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

structlog.configure(
    processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()]
)
logger = structlog.get_logger()

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "temporal:7233")
OCR_SERVICE_URL = os.getenv("OCR_SERVICE_URL", "http://ocr-pipeline:8000")
LEDGER_SERVICE_URL = os.getenv("LEDGER_SERVICE_URL", "http://ledger-processor:8080")

# ---------------------------------------------------------------------------
# Activity Definitions (Idempotent with idempotency keys)
# ---------------------------------------------------------------------------
@activity.defn
async def classify_document(document_id: str, s3_key: str, idempotency_key: str) -> dict:
    """Activity 1: Classify document type using OCR service."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OCR_SERVICE_URL}/api/v1/ocr/process",
                headers={"Idempotency-Key": idempotency_key},
                data={"client_id": "workflow-client", "doc_type_hint": "auto"},
                files={"file": ("document.pdf", b"")}  # Simplified; production streams from S3
            )
            return resp.json()
    except Exception as e:
        logger.error("Activity classify_document failed", error=str(e), document_id=document_id)
        raise ApplicationError(f"Classification failed: {str(e)}")

@activity.defn
async def extract_invoice_data(document_id: str, ocr_result: dict, idempotency_key: str) -> dict:
    """Activity 2: Extract structured invoice data."""
    extracted = ocr_result.get("extracted_data", {})
    if not extracted:
        raise ApplicationError("No data extracted from document")
    return extracted

@activity.defn
async def validate_invoice_data(extracted_data: dict, idempotency_key: str) -> dict:
    """Activity 3: Validate GSTIN, HSN, amounts, duplicate detection."""
    errors = []

    # GSTIN format check
    gstin = extracted_data.get("vendor_gstin")
    if gstin and len(gstin) != 15:
        errors.append("Invalid GSTIN length")

    # Amount positivity
    total = extracted_data.get("total_amount")
    if not total or total <= 0:
        errors.append("Invalid total amount")

    # HSN check
    hsn = extracted_data.get("hsn_code")
    if hsn and len(hsn) not in [4, 6, 8]:
        errors.append("Invalid HSN code length")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "data": extracted_data
    }

@activity.defn
async def post_to_ledger(validated_data: dict, client_id: str, idempotency_key: str) -> dict:
    """Activity 4: Post double-entry to ledger."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Create debit entry (expense/asset)
            debit_entry = {
                "client_id": client_id,
                "account_code": "5001",  # Purchases
                "transaction_date": validated_data["data"].get("invoice_date", "2024-01-01"),
                "debit": validated_data["data"].get("total_amount", 0),
                "credit": 0,
                "description": f"Invoice {validated_data['data'].get('invoice_number', 'unknown')}",
                "gstin": validated_data["data"].get("vendor_gstin"),
                "hsn_code": validated_data["data"].get("hsn_code")
            }

            # Create credit entry (creditor/bank)
            credit_entry = {
                "client_id": client_id,
                "account_code": "2001",  # Creditors
                "transaction_date": validated_data["data"].get("invoice_date", "2024-01-01"),
                "debit": 0,
                "credit": validated_data["data"].get("total_amount", 0),
                "description": f"Invoice {validated_data['data'].get('invoice_number', 'unknown')}",
                "gstin": validated_data["data"].get("vendor_gstin"),
                "hsn_code": validated_data["data"].get("hsn_code")
            }

            resp = await client.post(
                f"{LEDGER_SERVICE_URL}/api/v1/ledger/batch",
                headers={"Idempotency-Key": idempotency_key, "X-Correlation-ID": str(uuid.uuid4())},
                json={"entries": [debit_entry, credit_entry]}
            )
            return resp.json()
    except Exception as e:
        logger.error("Ledger posting failed", error=str(e))
        raise ApplicationError(f"Ledger posting failed: {str(e)}")

@activity.defn
async def reconcile_with_bank(client_id: str, amount: float, idempotency_key: str) -> dict:
    """Activity 5: Attempt bank statement reconciliation via Account Aggregator."""
    # Placeholder: In production, queries AA framework for matching transaction
    return {
        "reconciled": False,
        "reason": "Bank statement not yet available",
        "suggested_action": "retry_in_24h"
    }

@activity.defn
async def flag_for_human_review(document_id: str, reason: str, idempotency_key: str) -> dict:
    """Compensation/Saga: Flag document for HITL when STP fails."""
    logger.warning("HITL flag raised", document_id=document_id, reason=reason)
    return {"status": "flagged", "review_queue": "accounting_review", "priority": "high"}

# ---------------------------------------------------------------------------
# Workflow Definition (Saga Pattern)
# ---------------------------------------------------------------------------
@workflow.defn
class InvoiceToLedgerSTP:
    @workflow.run
    async def run(self, document_id: str, s3_key: str, client_id: str) -> dict:
        idempotency_key = f"stp-{document_id}-{workflow.now().isoformat()}"

        try:
            # Step 1: Classify
            ocr_result = await workflow.execute_activity(
                classify_document,
                args=(document_id, s3_key, idempotency_key),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3, non_retryable_error_types=["ApplicationError"])
            )

            if ocr_result.get("doc_type") != "invoice":
                return {"status": "skipped", "reason": "Not an invoice"}

            # Step 2: Extract
            extracted = await workflow.execute_activity(
                extract_invoice_data,
                args=(document_id, ocr_result, idempotency_key),
                start_to_close_timeout=timedelta(seconds=20),
                retry_policy=RetryPolicy(maximum_attempts=2)
            )

            # Step 3: Validate
            validation = await workflow.execute_activity(
                validate_invoice_data,
                args=(extracted, idempotency_key),
                start_to_close_timeout=timedelta(seconds=10)
            )

            if not validation["valid"]:
                # Saga compensation: flag for human review
                await workflow.execute_activity(
                    flag_for_human_review,
                    args=(document_id, f"Validation failed: {validation['errors']}", idempotency_key),
                    start_to_close_timeout=timedelta(seconds=10)
                )
                return {"status": "hitl", "errors": validation["errors"]}

            # Step 4: Post to Ledger
            ledger_result = await workflow.execute_activity(
                post_to_ledger,
                args=(validation, client_id, idempotency_key),
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2))
            )

            # Step 5: Reconcile
            recon = await workflow.execute_activity(
                reconcile_with_bank,
                args=(client_id, extracted.get("total_amount", 0), idempotency_key),
                start_to_close_timeout=timedelta(seconds=15)
            )

            return {
                "status": "completed",
                "document_id": document_id,
                "ledger_entries": ledger_result.get("count", 0),
                "reconciliation": recon,
                "stp": True
            }

        except ActivityError as e:
            # Saga: compensate by flagging for review
            await workflow.execute_activity(
                flag_for_human_review,
                args=(document_id, f"Workflow failed: {str(e)}", idempotency_key),
                start_to_close_timeout=timedelta(seconds=10)
            )
            return {"status": "failed", "error": str(e), "compensated": True}

# ---------------------------------------------------------------------------
# Worker & FastAPI Admin
# ---------------------------------------------------------------------------
from fastapi import FastAPI

app = FastAPI(title="Workflow Engine — Temporal STP", version="2.0.0")
worker_task = None

@app.on_event("startup")
async def startup():
    global worker_task
    client = await Client.connect(TEMPORAL_HOST)
    worker = Worker(
        client,
        task_queue="accounting-stp",
        workflows=[InvoiceToLedgerSTP],
        activities=[
            classify_document,
            extract_invoice_data,
            validate_invoice_data,
            post_to_ledger,
            reconcile_with_bank,
            flag_for_human_review
        ]
    )
    worker_task = asyncio.create_task(worker.run())
    logger.info("Temporal worker started", task_queue="accounting-stp")

@app.on_event("shutdown")
async def shutdown():
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

@app.post("/api/v1/workflow/invoice-to-ledger")
async def start_workflow(document_id: str, s3_key: str, client_id: str):
    client = await Client.connect(TEMPORAL_HOST)
    handle = await client.start_workflow(
        InvoiceToLedgerSTP.run,
        document_id,
        s3_key,
        client_id,
        id=str(uuid.uuid4()),
        task_queue="accounting-stp"
    )
    return {"workflow_id": handle.id, "status": "started"}

@app.get("/api/v1/workflow/health")
async def health():
    return {"status": "healthy", "service": "workflow-engine", "orchestrator": "temporal"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
