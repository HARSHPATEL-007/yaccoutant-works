"""
Legal Document Ingestion Pipeline — CBIC, MCA, Income Tax Scrapers
Semantic chunking → Embedding → Dual-store (Qdrant + Elasticsearch)
"""

import os
import re
import uuid
import hashlib
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import structlog
import httpx
import asyncpg
from bs4 import BeautifulSoup
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Distance, VectorParams
from elasticsearch import AsyncElasticsearch
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
ES_URL = os.getenv("ES_URL", "http://elasticsearch:9200")
DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost/accounting_platform")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

structlog.configure(
    processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()]
)
logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class IngestRequest(BaseModel):
    source: str = Field(..., pattern=r"^(cbic|mca|incometax|custom)$")
    url: Optional[str] = None
    force_refresh: bool = False

class IngestStatus(BaseModel):
    job_id: str
    source: str
    status: str
    documents_found: int
    chunks_indexed: int
    errors: List[str]

# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------
@dataclass
class ScrapedDocument:
    url: str
    title: str
    content: str
    doc_type: str
    jurisdiction: str = "india"
    section_reference: Optional[str] = None
    date_published: Optional[str] = None
    source_domain: str = ""

class CBICScraper:
    BASE_URL = "https://cbic-gst.gov.in"
    CIRCULARS_PATH = "/htdocs-cbec/gst-circular"

    async def scrape(self, client: httpx.AsyncClient) -> List[ScrapedDocument]:
        docs = []
        try:
            resp = await client.get(f"{self.BASE_URL}{self.CIRCULARS_PATH}", timeout=30)
            soup = BeautifulSoup(resp.text, "lxml")
            links = soup.select("a[href*='pdf']")[:20]  # Limit to 20 per run

            for link in links:
                href = link.get("href")
                if not href:
                    continue
                full_url = urljoin(self.BASE_URL, href)
                title = link.get_text(strip=True) or "CBIC Circular"

                # Extract section/circular number from title
                section_match = re.search(r'Circular\s+No\.\s*(\d+/\d+)', title, re.I)
                section_ref = section_match.group(1) if section_match else None

                docs.append(ScrapedDocument(
                    url=full_url,
                    title=title,
                    content=f"[PDF_LINK]{title}[/PDF_LINK]",  # In production: download + OCR
                    doc_type="gst_circular",
                    section_reference=section_ref,
                    source_domain="cbic-gst.gov.in"
                ))
        except Exception as e:
            logger.error("CBIC scrape failed", error=str(e))
        return docs

class MCAScraper:
    BASE_URL = "https://www.mca.gov.in"

    async def scrape(self, client: httpx.AsyncClient) -> List[ScrapedDocument]:
        docs = []
        try:
            # MCA notifications / circulars
            resp = await client.get(f"{self.BASE_URL}/content/mca/global/en/notifications/circulars.html", timeout=30)
            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table tr")[:25]

            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 2:
                    title = cells[0].get_text(strip=True)
                    date_text = cells[1].get_text(strip=True)
                    link = cells[0].find("a")
                    if link:
                        docs.append(ScrapedDocument(
                            url=urljoin(self.BASE_URL, link.get("href", "")),
                            title=title,
                            content=title,
                            doc_type="mca_notification",
                            date_published=date_text,
                            source_domain="mca.gov.in"
                        ))
        except Exception as e:
            logger.error("MCA scrape failed", error=str(e))
        return docs

class IncomeTaxScraper:
    BASE_URL = "https://incometaxindia.gov.in"

    async def scrape(self, client: httpx.AsyncClient) -> List[ScrapedDocument]:
        docs = []
        try:
            resp = await client.get(f"{self.BASE_URL}/Pages/communications/circulars.aspx", timeout=30)
            soup = BeautifulSoup(resp.text, "lxml")
            items = soup.select(".comm-item")[:20]

            for item in items:
                title_tag = item.select_one(".comm-title a")
                if title_tag:
                    title = title_tag.get_text(strip=True)
                    href = title_tag.get("href", "")
                    section_match = re.search(r'Section\s+(\d+[A-Z]?)', title, re.I)
                    docs.append(ScrapedDocument(
                        url=urljoin(self.BASE_URL, href),
                        title=title,
                        content=title,
                        doc_type="income_tax_act",
                        section_reference=section_match.group(1) if section_match else None,
                        source_domain="incometaxindia.gov.in"
                    ))
        except Exception as e:
            logger.error("Income Tax scrape failed", error=str(e))
        return docs

# ---------------------------------------------------------------------------
# Chunking & Embedding Pipeline
# ---------------------------------------------------------------------------
class IngestionPipeline:
    def __init__(self):
        self.embedder = SentenceTransformer(EMBED_MODEL)
        self.qdrant = QdrantClient(url=QDRANT_URL)
        self.es = AsyncElasticsearch([ES_URL])
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            separators=["\n\n", "\n", ". ", " ", ""]
        )
        self._ensure_collection()

    def _ensure_collection(self):
        try:
            self.qdrant.get_collection("accounting_knowledge")
        except Exception:
            self.qdrant.create_collection(
                collection_name="accounting_knowledge",
                vectors_config=VectorParams(size=384, distance=Distance.COSINE)
            )

    async def process_documents(self, docs: List[ScrapedDocument]) -> IngestStatus:
        job_id = str(uuid.uuid4())[:8]
        total_chunks = 0
        errors = []

        for doc in docs:
            try:
                # Semantic chunking
                chunks = self.splitter.split_text(doc.content)

                for idx, chunk in enumerate(chunks):
                    chunk_id = hashlib.sha256(f"{doc.url}:{idx}".encode()).hexdigest()
                    embedding = self.embedder.encode(chunk).tolist()

                    # Qdrant upsert
                    self.qdrant.upsert(
                        collection_name="accounting_knowledge",
                        points=[PointStruct(
                            id=chunk_id,
                            vector=embedding,
                            payload={
                                "chunk_text": chunk,
                                "source_url": doc.url,
                                "doc_type": doc.doc_type,
                                "jurisdiction": doc.jurisdiction,
                                "section_reference": doc.section_reference,
                                "title": doc.title,
                                "date_published": doc.date_published,
                                "chunk_index": idx,
                                "metadata": {
                                    "source_domain": doc.source_domain,
                                    "ingested_at": datetime.utcnow().isoformat()
                                }
                            }
                        )]
                    )

                    # Elasticsearch index
                    await self.es.index(
                        index="accounting_docs",
                        id=chunk_id,
                        document={
                            "chunk_text": chunk,
                            "title": doc.title,
                            "source_url": doc.url,
                            "doc_type": doc.doc_type,
                            "jurisdiction": doc.jurisdiction,
                            "section_reference": doc.section_reference,
                            "date_published": doc.date_published,
                            "metadata": {"source_domain": doc.source_domain}
                        }
                    )
                    total_chunks += 1

            except Exception as e:
                logger.error("Chunk indexing failed", url=doc.url, error=str(e))
                errors.append(f"{doc.url}: {str(e)}")

        return IngestStatus(
            job_id=job_id,
            source=docs[0].doc_type if docs else "unknown",
            status="completed" if not errors else "partial",
            documents_found=len(docs),
            chunks_indexed=total_chunks,
            errors=errors
        )

# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------
app = FastAPI(title="Legal Document Ingestion Pipeline", version="2.0.0")
pipeline = IngestionPipeline()
scheduler = AsyncIOScheduler()

@app.on_event("startup")
async def startup():
    scheduler.add_job(
        scheduled_ingestion,
        "cron",
        hour=2,
        minute=0,
        id="nightly_ingestion",
        replace_existing=True
    )
    scheduler.start()
    logger.info("Ingestion scheduler started")

@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()
    await pipeline.es.close()

@app.post("/api/v1/ingestion/trigger", response_model=IngestStatus)
async def trigger_ingestion(request: IngestRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_ingestion, request.source)
    return IngestStatus(
        job_id=str(uuid.uuid4())[:8],
        source=request.source,
        status="started",
        documents_found=0,
        chunks_indexed=0,
        errors=[]
    )

@app.get("/api/v1/ingestion/status/{job_id}")
async def get_status(job_id: str):
    # In production: query Redis/DB for job status
    return {"job_id": job_id, "status": "unknown"}

@app.get("/api/v1/ingestion/health")
async def health():
    return {"status": "healthy", "service": "document-ingestion"}

async def run_ingestion(source: str):
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        if source == "cbic":
            docs = await CBICScraper().scrape(client)
        elif source == "mca":
            docs = await MCAScraper().scrape(client)
        elif source == "incometax":
            docs = await IncomeTaxScraper().scrape(client)
        else:
            docs = []

        if docs:
            result = await pipeline.process_documents(docs)
            logger.info("Ingestion completed", job_id=result.job_id, chunks=result.chunks_indexed)

async def scheduled_ingestion():
    for source in ["cbic", "mca", "incometax"]:
        await run_ingestion(source)
        await asyncio.sleep(5)  # Rate limiting
