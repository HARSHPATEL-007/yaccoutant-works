# AI-Native Accounting Platform
## Production-Ready Multi-Phase Codebase

---

## Platform Overview

A hyper-automated, agentic accounting platform that scales Tier-1 accounting expertise using LLMs, Hybrid RAG, deterministic workflow automation, and zero-hallucination guardrails. Built for Indian accounting clients across four segments: **Startups**, **SMEs/MSMEs**, **Large Corporates**, and **Foreign Entities**.

### Key Differentiators
- **Zero Hallucination:** All tax/legal responses grounded in RAG-retrieved documents with mandatory citations
- **Multi-Agent Architecture:** Router → Tax/Quant/Document/Foreign Entity agents with confidence thresholds
- **Straight-Through Processing:** Invoice-to-ledger automation with Temporal.io Saga orchestration
- **Immutable Audit:** SHA-256 chain-hashed logs for tamper-evident compliance
- **Enterprise Predictive:** Anomaly detection, M&A simulation, and ESG analytics

---

## Repository Structure

```
.
├── docker-compose.yml              # Full stack orchestration (all phases)
├── docker-compose-full.yml         # Production-grade compose with all services
├── .env.example                    # Environment variable template
├── README.md                       # This file
├── ARCHITECTURE.md                 # System architecture & data flows
├── SECURITY.md                     # Compliance, encryption, AI guardrails
├── DEPLOYMENT.md                   # Terraform, K8s, CI/CD guide
│
├── postgres/
│   └── init.sql                    # Production schema with RLS, encryption, partitioning
│
├── ledger-processor/               # Phase 1 — Go microservice (double-entry, reconciliation)
│   ├── Dockerfile
│   ├── go.mod
│   └── main.go
│
├── ai-orchestrator/                # Phase 1 — FastAPI + LangGraph multi-agent coordination
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── rag-service/                    # Phase 1 — Hybrid search (Qdrant + ES + cross-encoder)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── ocr-pipeline/                   # Phase 1 — Document classification, extraction, validation
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── guardrails-service/           # Phase 1 — Topical + hallucination + execution guardrails
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── guardrails-service-enhanced/  # Phase 3 — NeMo + RBAC + HSM-backed approvals
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── phase2-document-ingestion/    # Phase 2 — CBIC/MCA/IT scrapers + semantic chunking
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── phase2-valuation-engine/      # Phase 2 — Black-Scholes ESOP + 409A composite valuation
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── phase2-workflow-engine/       # Phase 2 — Temporal.io STP with Saga pattern
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── phase3-anomaly-detection/     # Phase 3 — Isolation Forest + statistical anomaly detection
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── phase3-ma-simulation/         # Phase 3 — Multi-agent M&A with DCF/LBO models
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── web-dashboard/                  # Phase 1/2/3 — Next.js 14 frontend
│   ├── package.json
│   ├── tsconfig.json
│   ├── next.config.mjs
│   └── src/
│       ├── app/
│       │   ├── layout.tsx
│       │   ├── page.tsx
│       │   └── globals.css
│       ├── components/
│       │   ├── compliance-dashboard.tsx
│       │   ├── ai-chat-widget.tsx
│       │   ├── cross-border-page.tsx      # Phase 2
│       │   ├── vcfo-page.tsx              # Phase 2/3
│       │   └── deep-audit-page.tsx        # Phase 3
│       ├── lib/
│       │   └── api.ts
│       └── providers/
│           ├── query-provider.tsx
│           └── auth-provider.tsx
│
├── infra/
│   └── terraform/                  # AWS EKS, RDS, ElastiCache, S3, WAF
│       ├── main.tf
│       ├── variables.tf
│       └── production.tfvars
│
├── k8s/
│   ├── base/                     # Kustomize base manifests
│   │   ├── namespace.yaml
│   │   ├── ledger-processor.yaml
│   │   ├── ai-orchestrator.yaml
│   │   ├── keda-scalers.yaml
│   │   ├── network-policies.yaml
│   │   └── kustomization.yaml
│   └── overlays/
│       └── production/
│           └── kustomization.yaml
│
└── .github/
    └── workflows/
        ├── ci.yml                  # Build, test, security scan (Trivy, Snyk)
        └── cd.yml                  # Deploy to EKS with smoke tests
```

---

## Quick Start

### Local Development (Docker Compose)

```bash
# 1. Clone and configure
git clone <repo>
cd ai-native-accounting-platform
cp .env.example .env
# Edit .env with your API keys

# 2. Start infrastructure & all services
docker-compose -f docker-compose-full.yml up -d

# 3. Initialize database
docker-compose exec postgres psql -U accounting_admin -d accounting_platform -f /docker-entrypoint-initdb.d/init.sql

# 4. Verify health
curl http://localhost/api/v1/ledger/health
curl http://localhost/api/v1/ai/health
curl http://localhost/api/v1/rag/health
curl http://localhost/api/v1/ocr/health
curl http://localhost/api/v1/guardrails/health
curl http://localhost/api/v1/valuation/health
curl http://localhost/api/v1/anomaly/health
curl http://localhost/api/v1/ma/health
curl http://localhost/api/v1/workflow/health

# 5. Start web dashboard
cd web-dashboard && npm install && npm run dev
# Open http://localhost:3000
```

### Production Deployment (AWS EKS)

```bash
# 1. Provision infrastructure
cd infra/terraform
terraform init
terraform workspace new production
terraform apply -var-file=production.tfvars

# 2. Configure kubectl
aws eks update-kubeconfig --name accounting-platform-production --region ap-south-1

# 3. Deploy application
kubectl apply -k k8s/overlays/production/

# 4. Verify
kubectl get pods -n accounting
kubectl get ingress -n accounting
```

---

## Phase Capabilities

### Phase 1: SME Foundation (Months 1-3) ✅
| Capability | Service | Status |
|-----------|---------|--------|
| Double-entry ledger | `ledger-processor` | Production |
| Hybrid RAG search | `rag-service` | Production |
| Invoice OCR + validation | `ocr-pipeline` | Production |
| Multi-agent AI chat | `ai-orchestrator` | Production |
| Compliance dashboard | `web-dashboard` | Production |
| Guardrails (3-layer) | `guardrails-service` | Production |

### Phase 2: Advisory RAG (Months 3-6) ✅
| Capability | Service | Status |
|-----------|---------|--------|
| Legal document ingestion | `document-ingestion` | Production |
| ESOP Black-Scholes valuation | `valuation-engine` | Production |
| 409A composite valuation | `valuation-engine` | Production |
| Invoice-to-ledger STP | `workflow-engine` | Production |
| Cross-border navigator | `web-dashboard` | Production |
| Virtual CFO dashboard | `web-dashboard` | Production |

### Phase 3: Enterprise Predictive (Months 6-9) ✅
| Capability | Service | Status |
|-----------|---------|--------|
| Mass ledger anomaly detection | `anomaly-detection` | Production |
| M&A multi-agent simulation | `ma-simulation` | Production |
| Deep-audit console | `web-dashboard` | Production |
| Enhanced guardrails (NeMo+RBAC) | `guardrails-service-enhanced` | Production |
| ESG analytics (extensible) | — | Planned |

---

## Security & Compliance

- **DPDP Act 2023:** Consent management, data localization, breach notification
- **SOC 2 Type II:** Quarterly audits, automated compliance checks
- **ISO 27001:** ISMS, risk assessment, penetration testing
- **Encryption:** AES-256 at rest, TLS 1.3 in transit, FPE for PAN/Aadhaar
- **AI Guardrails:** Topical + hallucination + execution + RBAC layers
- **Immutable Audit:** SHA-256 chain-hashed logs with 7-year retention

---

## API Endpoints

### Core Services
| Service | Base Path | Health Check |
|---------|-----------|-------------|
| Ledger Processor | `/api/v1/ledger` | `GET /health` |
| AI Orchestrator | `/api/v1/ai` | `GET /api/v1/ai/health` |
| RAG Service | `/api/v1/rag` | `GET /api/v1/rag/health` |
| OCR Pipeline | `/api/v1/ocr` | `GET /api/v1/ocr/health` |
| Guardrails | `/api/v1/guardrails` | `GET /api/v1/guardrails/health` |
| Valuation Engine | `/api/v1/valuation` | `GET /api/v1/valuation/health` |
| Anomaly Detection | `/api/v1/anomaly` | `GET /api/v1/anomaly/health` |
| M&A Simulation | `/api/v1/ma` | `GET /api/v1/ma/health` |
| Workflow Engine | `/api/v1/workflow` | `GET /api/v1/workflow/health` |
| Document Ingestion | `/api/v1/ingestion` | `GET /api/v1/ingestion/health` |

---

## Mandatory Disclaimer

All AI-generated financial advice includes:

> *"This AI-generated response is for informational purposes only and does not constitute professional financial or legal advice. Please consult a licensed Chartered Accountant before making decisions."*

---

## License

Proprietary — All rights reserved.

---

*Platform Version: 3.0*
*Last Updated: 2026-06-07*
*Compliance: DPDP Act 2023, SOC 2 Type II, ISO 27001*
