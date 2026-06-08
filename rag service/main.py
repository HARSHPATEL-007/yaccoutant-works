"""
RAG Service — Hybrid Search (Dense + Sparse) with Cross-Encoder Re-ranking
"""

import os
import uuid
import asyncio
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

import structlog
import httpx
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from elasticsearch import AsyncElasticsearch
from sentence_transformers import SentenceTransformer, CrossEncoder
from rank_bm25 import BM25Okapi

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
ES_URL = os.getenv("ES_URL", "http://localhost:9200")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
ALPHA = 0.7  # Weight for dense vs sparse

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
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    intent: Optional[str] = None
    client_id: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=20)
    jurisdiction: str = "india"
    doc_types: Optional[List[str]] = None

class SearchResult(BaseModel):
    id: str
    score: float
    chunk_text: str
    source_url: str
    doc_type: str
    section_reference: Optional[str] = None
    metadata: Dict[str, Any]

class SearchResponse(BaseModel):
    query: str
    results: List[SearchResult]
    total_found: int
    latency_ms: float
    search_strategy: str

# -----------------------------------------------------------------------------
# Search Engine
# -----------------------------------------------------------------------------
@dataclass
class HybridSearchEngine:
    dense_model: SentenceTransformer
    reranker: CrossEncoder
    qdrant: QdrantClient
    es: AsyncElasticsearch
    
    def __post_init__(self):
        self.collection_name = "accounting_knowledge"
        self._ensure_collection()
    
    def _ensure_collection(self):
        try:
            self.qdrant.get_collection(self.collection_name)
        except Exception:
            self.qdrant.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=384, distance=Distance.COSINE)
            )
            logger.info("Created Qdrant collection", collection=self.collection_name)

    async def hybrid_search(self, request: SearchRequest) -> SearchResponse:
        import time
        start = time.time()
        
        # 1. Generate dense embedding
        query_embedding = self.dense_model.encode(request.query).tolist()
        
        # 2. Dense retrieval from Qdrant
        dense_results = self.qdrant.search(
            collection_name=self.collection_name,
            query_vector=query_embedding,
            limit=50,
            with_payload=True,
            query_filter=self._build_filter(request)
        )
        
        # 3. Sparse retrieval from Elasticsearch (BM25)
        es_query = {
            "query": {
                "bool": {
                    "must": [
                        {"multi_match": {
                            "query": request.query,
                            "fields": ["chunk_text^3", "title^2", "metadata.tags"],
                            "type": "best_fields"
                        }}
                    ],
                    "filter": self._build_es_filter(request)
                }
            },
            "size": 50
        }
        
        es_results = await self.es.search(index="accounting_docs", body=es_query)
        
        # 4. Fuse results
        fused = self._fuse_results(dense_results, es_results, request.query)
        
        # 5. Re-rank top 20 with cross-encoder
        reranked = self._rerank(fused[:20], request.query)
        
        # 6. Build response
        results = []
        for r in reranked[:request.top_k]:
            results.append(SearchResult(
                id=r.get("id"),
                score=r.get("final_score", 0.0),
                chunk_text=r.get("text", ""),
                source_url=r.get("source_url", ""),
                doc_type=r.get("doc_type", "unknown"),
                section_reference=r.get("section"),
                metadata=r.get("metadata", {})
            ))
        
        latency = (time.time() - start) * 1000
        
        return SearchResponse(
            query=request.query,
            results=results,
            total_found=len(fused),
            latency_ms=round(latency, 2),
            search_strategy="hybrid_dense_sparse_rerank"
        )
    
    def _build_filter(self, request: SearchRequest):
        from qdrant_client.models import FieldCondition, MatchValue, Filter
        conditions = []
        if request.jurisdiction:
            conditions.append(
                FieldCondition(key="jurisdiction", match=MatchValue(value=request.jurisdiction))
            )
        if request.doc_types:
            conditions.append(
                FieldCondition(key="doc_type", match=MatchValue(value=request.doc_types[0]))
            )
        return Filter(must=conditions) if conditions else None
    
    def _build_es_filter(self, request: SearchRequest):
        filters = [{"term": {"jurisdiction": request.jurisdiction}}]
        if request.doc_types:
            filters.append({"terms": {"doc_type": request.doc_types}})
        return filters
    
    def _fuse_results(self, dense, es_results, query):
        """Reciprocal Rank Fusion (RRF) with alpha weighting."""
        dense_scores = {}
        for i, r in enumerate(dense):
            dense_scores[r.id] = {
                "id": str(r.id),
                "text": r.payload.get("chunk_text", ""),
                "source_url": r.payload.get("source_url", ""),
                "doc_type": r.payload.get("doc_type", ""),
                "section": r.payload.get("section_reference"),
                "metadata": r.payload.get("metadata", {}),
                "dense_score": r.score,
                "dense_rank": i + 1
            }
        
        sparse_scores = {}
        for i, hit in enumerate(es_results.get("hits", {}).get("hits", [])):
            src = hit["_source"]
            doc_id = hit["_id"]
            sparse_scores[doc_id] = {
                "id": doc_id,
                "text": src.get("chunk_text", ""),
                "source_url": src.get("source_url", ""),
                "doc_type": src.get("doc_type", ""),
                "section": src.get("section_reference"),
                "metadata": src.get("metadata", {}),
                "sparse_score": hit["_score"],
                "sparse_rank": i + 1
            }
        
        # Combine
        all_ids = set(dense_scores.keys()) | set(sparse_scores.keys())
        fused = []
        for doc_id in all_ids:
            d = dense_scores.get(doc_id, {})
            s = sparse_scores.get(doc_id, {})
            
            # RRF formula: sum(1 / (k + rank))
            k = 60
            dense_rrf = 1.0 / (k + d.get("dense_rank", 999)) if d else 0
            sparse_rrf = 1.0 / (k + s.get("sparse_rank", 999)) if s else 0
            
            # Weighted fusion
            final_score = ALPHA * (d.get("dense_score", 0) if d else 0) + \
                         (1 - ALPHA) * (sparse_rrf * 10)  # Normalize sparse
            
            merged = {
                "id": doc_id,
                "text": d.get("text") or s.get("text"),
                "source_url": d.get("source_url") or s.get("source_url"),
                "doc_type": d.get("doc_type") or s.get("doc_type"),
                "section": d.get("section") or s.get("section"),
                "metadata": {**(d.get("metadata") or {}), **(s.get("metadata") or {})},
                "final_score": final_score
            }
            fused.append(merged)
        
        fused.sort(key=lambda x: x["final_score"], reverse=True)
        return fused
    
    def _rerank(self, candidates, query):
        """Cross-encoder re-ranking on top-20 candidates."""
        if not candidates:
            return candidates
        
        pairs = [[query, c["text"]] for c in candidates]
        scores = self.reranker.predict(pairs)
        
        for c, score in zip(candidates, scores):
            c["final_score"] = float(score)
        
        candidates.sort(key=lambda x: x["final_score"], reverse=True)
        return candidates

# -----------------------------------------------------------------------------
# FastAPI Application
# -----------------------------------------------------------------------------
app = FastAPI(
    title="RAG Service — Hybrid Search",
    version="1.0.0",
    docs_url="/api/v1/rag/docs"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
)

# Global engine instance
search_engine: Optional[HybridSearchEngine] = None

@app.on_event("startup")
async def startup():
    global search_engine
    dense = SentenceTransformer(EMBEDDING_MODEL)
    reranker = CrossEncoder(RERANKER_MODEL)
    qdrant = QdrantClient(url=QDRANT_URL)
    es = AsyncElasticsearch([ES_URL])
    
    search_engine = HybridSearchEngine(
        dense_model=dense,
        reranker=reranker,
        qdrant=qdrant,
        es=es
    )
    logger.info("RAG service initialized")

@app.on_event("shutdown")
async def shutdown():
    if search_engine:
        await search_engine.es.close()

@app.post("/api/v1/rag/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    if not search_engine:
        raise HTTPException(status_code=503, detail="Search engine not initialized")
    
    try:
        return await search_engine.hybrid_search(request)
    except Exception as e:
        logger.error("Search failed", error=str(e), query=request.query)
        raise HTTPException(status_code=500, detail=f"Search operation failed: {str(e)}")

@app.post("/api/v1/rag/ingest")
async def ingest_document(doc: Dict[str, Any]):
    """Ingest a document chunk into both vector and keyword stores."""
    if not search_engine:
        raise HTTPException(status_code=503, detail="Search engine not initialized")
    
    chunk_id = str(uuid.uuid4())
    text = doc.get("chunk_text", "")
    embedding = search_engine.dense_model.encode(text).tolist()
    
    # Qdrant
    search_engine.qdrant.upsert(
        collection_name=search_engine.collection_name,
        points=[PointStruct(
            id=chunk_id,
            vector=embedding,
            payload={
                "chunk_text": text,
                "source_url": doc.get("source_url"),
                "doc_type": doc.get("doc_type"),
                "jurisdiction": doc.get("jurisdiction", "india"),
                "section_reference": doc.get("section_reference"),
                "metadata": doc.get("metadata", {})
            }
        )]
    )
    
    # Elasticsearch
    await search_engine.es.index(
        index="accounting_docs",
        id=chunk_id,
        document={
            "chunk_text": text,
            "title": doc.get("title", ""),
            "source_url": doc.get("source_url"),
            "doc_type": doc.get("doc_type"),
            "jurisdiction": doc.get("jurisdiction", "india"),
            "section_reference": doc.get("section_reference"),
            "metadata": doc.get("metadata", {}),
            "timestamp": "now"
        }
    )
    
    return {"status": "indexed", "id": chunk_id}

@app.get("/api/v1/rag/health")
async def health():
    return {"status": "healthy", "service": "rag-service"}