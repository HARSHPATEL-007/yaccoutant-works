"""
OCR Pipeline — Invoice-to-Ledger STP: Classification, Extraction, Validation
"""

import os
import io
import re
import json
import uuid
import hashlib
from typing import Optional, List, Dict, Any
from datetime import datetime

import structlog
import httpx
import redis.asyncio as redis
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from PIL import Image
import pytesseract
from pdf2image import convert_from_bytes

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://minio:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioadmin")
S3_BUCKET = os.getenv("S3_BUCKET", "accounting-documents")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

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
class ExtractedInvoice(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    vendor_gstin: Optional[str] = None
    buyer_gstin: Optional[str] = None
    total_amount: Optional[float] = None
    taxable_amount: Optional[float] = None
    cgst: Optional[float] = None
    sgst: Optional[float] = None
    igst: Optional[float] = None
    hsn_code: Optional[str] = None
    line_items: List[Dict[str, Any]] = Field(default_factory=list)
    
    @validator('vendor_gstin', 'buyer_gstin')
    def validate_gstin(cls, v):
        if v and not re.match(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$', v):
            raise ValueError('Invalid GSTIN format')
        return v

class OCRResult(BaseModel):
    document_id: uuid.UUID
    doc_type: str  # invoice, bank_statement, receipt, contract
    ocr_status: str
    raw_text: str
    extracted_data: Optional[Dict[str, Any]] = None
    confidence_score: float = Field(ge=0, le=1)
    validation_errors: List[str] = Field(default_factory=list)
    s3_key: str

# -----------------------------------------------------------------------------
# S3 Client
# -----------------------------------------------------------------------------
class S3Client:
    def __init__(self):
        import boto3
        self.client = boto3.client(
            's3',
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY
        )
        self.bucket = S3_BUCKET
    
    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream"):
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type
        )
        return f"{S3_ENDPOINT}/{self.bucket}/{key}"

# -----------------------------------------------------------------------------
# Document Classifier (Rule-based + Heuristic)
# -----------------------------------------------------------------------------
class DocumentClassifier:
    KEYWORDS = {
        "invoice": ["invoice", "tax invoice", "bill to", "ship to", "gstin", "hsn"],
        "bank_statement": ["statement", "account number", "transaction date", "debit", "credit", "balance"],
        "receipt": ["receipt", "acknowledgment", "payment received", "thank you"],
        "contract": ["agreement", "party", "whereas", "hereinafter", "clause", "witnesseth"]
    }
    
    def classify(self, text: str) -> tuple[str, float]:
        text_lower = text.lower()
        scores = {}
        for doc_type, keywords in self.KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower) / len(keywords)
            scores[doc_type] = score
        
        best = max(scores, key=scores.get)
        confidence = scores[best]
        return best, confidence

# -----------------------------------------------------------------------------
# Invoice Extractor (Regex + LLM fallback)
# -----------------------------------------------------------------------------
class InvoiceExtractor:
    GSTIN_PATTERN = re.compile(r'[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}')
    HSN_PATTERN = re.compile(r'HSN[:\s]+(\d{4,8})', re.IGNORECASE)
    AMOUNT_PATTERN = re.compile(r'(?:Total|Grand Total|Amount Payable)[:\s]*₹?\s*([\d,]+\.?\d{0,2})', re.IGNORECASE)
    DATE_PATTERN = re.compile(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})')
    
    def extract(self, text: str) -> ExtractedInvoice:
        gstins = self.GSTIN_PATTERN.findall(text)
        hsn = self.HSN_PATTERN.search(text)
        amount_match = self.AMOUNT_PATTERN.search(text)
        date_match = self.DATE_PATTERN.search(text)
        
        # Calculate amounts (CGST/SGST/IGST extraction)
        cgst = self._extract_tax(text, "CGST")
        sgst = self._extract_tax(text, "SGST|UTGST")
        igst = self._extract_tax(text, "IGST")
        
        total = None
        if amount_match:
            total = float(amount_match.group(1).replace(',', ''))
        
        return ExtractedInvoice(
            invoice_number=self._extract_field(text, ["Invoice No", "Invoice Number", "Inv No"]),
            invoice_date=date_match.group(1) if date_match else None,
            vendor_gstin=gstins[0] if len(gstins) > 0 else None,
            buyer_gstin=gstins[1] if len(gstins) > 1 else None,
            total_amount=total,
            taxable_amount=total - (cgst + sgst + igst) if total else None,
            cgst=cgst,
            sgst=sgst,
            igst=igst,
            hsn_code=hsn.group(1) if hsn else None
        )
    
    def _extract_field(self, text: str, field_names: List[str]) -> Optional[str]:
        for field in field_names:
            pattern = re.compile(rf'{field}[:\s]+([A-Za-z0-9/-]+)', re.IGNORECASE)
            match = pattern.search(text)
            if match:
                return match.group(1).strip()
        return None
    
    def _extract_tax(self, text: str, tax_name: str) -> Optional[float]:
        pattern = re.compile(rf'{tax_name}[:\s]+₹?\s*([\d,]+\.?\d{{0,2}})', re.IGNORECASE)
        match = pattern.search(text)
        if match:
            return float(match.group(1).replace(',', ''))
        return None

# -----------------------------------------------------------------------------
# Validation Engine
# -----------------------------------------------------------------------------
class ValidationEngine:
    def validate_invoice(self, invoice: ExtractedInvoice) -> List[str]:
        errors = []
        
        if not invoice.vendor_gstin:
            errors.append("Missing vendor GSTIN")
        if not invoice.invoice_number:
            errors.append("Missing invoice number")
        if not invoice.total_amount or invoice.total_amount <= 0:
            errors.append("Invalid or missing total amount")
        
        # Tax validation: CGST + SGST + IGST should approximate total tax
        if invoice.total_amount and invoice.taxable_amount:
            expected_tax = invoice.total_amount - invoice.taxable_amount
            actual_tax = (invoice.cgst or 0) + (invoice.sgst or 0) + (invoice.igst or 0)
            if abs(expected_tax - actual_tax) > 1.0:
                errors.append(f"Tax mismatch: expected {expected_tax}, got {actual_tax}")
        
        # HSN code validation (basic length check)
        if invoice.hsn_code and len(invoice.hsn_code) not in [4, 6, 8]:
            errors.append(f"Invalid HSN code length: {invoice.hsn_code}")
        
        return errors

# -----------------------------------------------------------------------------
# FastAPI Application
# -----------------------------------------------------------------------------
app = FastAPI(
    title="OCR Pipeline — Document Intelligence",
    version="1.0.0",
    docs_url="/api/v1/ocr/docs"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
)

s3_client = S3Client()
classifier = DocumentClassifier()
extractor = InvoiceExtractor()
validator = ValidationEngine()
redis_client: Optional[redis.Redis] = None

@app.on_event("startup")
async def startup():
    global redis_client
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

@app.on_event("shutdown")
async def shutdown():
    if redis_client:
        await redis_client.close()

@app.post("/api/v1/ocr/process", response_model=OCRResult)
async def process_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    client_id: str = "unknown",
    doc_type_hint: Optional[str] = None
):
    """Process uploaded document: classify, OCR, extract, validate."""
    doc_id = uuid.uuid4()
    content = await file.read()
    
    # Store raw file to S3
    s3_key = f"raw/{client_id}/{doc_id}/{file.filename}"
    await s3_client.upload(s3_key, content, file.content_type or "application/octet-stream")
    
    # Convert to images
    images = []
    if file.content_type == "application/pdf":
        images = convert_from_bytes(content, dpi=300, fmt="png")
    else:
        images = [Image.open(io.BytesIO(content))]
    
    # OCR all pages
    full_text = ""
    for i, img in enumerate(images):
        page_text = pytesseract.image_to_string(img, lang='eng')
        full_text += f"\n--- Page {i+1} ---\n{page_text}"
    
    # Classify
    detected_type, confidence = classifier.classify(full_text)
    if doc_type_hint:
        detected_type = doc_type_hint
    
    # Extract based on type
    extracted_data = None
    validation_errors = []
    
    if detected_type == "invoice":
        extracted = extractor.extract(full_text)
        validation_errors = validator.validate_invoice(extracted)
        extracted_data = extracted.model_dump()
    
    # Determine status
    status = "completed"
    if validation_errors:
        status = "flagged"  # Requires human review
    
    # Update database via background task
    background_tasks.add_task(
        save_to_database,
        doc_id,
        client_id,
        detected_type,
        status,
        s3_key,
        extracted_data,
        validation_errors
    )
    
    return OCRResult(
        document_id=doc_id,
        doc_type=detected_type,
        ocr_status=status,
        raw_text=full_text[:1000] + "...",  # Truncated for response
        extracted_data=extracted_data,
        confidence_score=confidence,
        validation_errors=validation_errors,
        s3_key=s3_key
    )

@app.get("/api/v1/ocr/health")
async def health():
    return {"status": "healthy", "tesseract_version": pytesseract.get_tesseract_version()}

async def save_to_database(
    doc_id: uuid.UUID,
    client_id: str,
    doc_type: str,
    status: str,
    s3_key: str,
    extracted_data: Optional[Dict],
    errors: List[str]
):
    """Persist OCR result to PostgreSQL."""
    import asyncpg
    try:
        conn = await asyncpg.connect(os.getenv("DATABASE_URL"))
        await conn.execute(
            """
            INSERT INTO documents (id, client_id, s3_key, doc_type, ocr_status, extraction_json, uploaded_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            ON CONFLICT (id) DO UPDATE SET
                ocr_status = EXCLUDED.ocr_status,
                extraction_json = EXCLUDED.extraction_json
            """,
            doc_id, client_id, s3_key, doc_type, status,
            json.dumps({"data": extracted_data, "errors": errors}) if extracted_data else None
        )
        await conn.close()
    except Exception as e:
        logger.error("Failed to save OCR result", error=str(e), doc_id=str(doc_id))