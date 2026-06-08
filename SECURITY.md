# Security & Compliance Documentation
## AI-Native Accounting Platform

---

## 1. Compliance Framework

### 1.1 Regulatory Compliance
| Regulation | Requirement | Implementation |
|-----------|-------------|---------------|
| DPDP Act 2023 | Consent management, data localization, breach notification | Row-level security, India-only data centers, 72-hour incident response |
| SOC 2 Type II | Security, availability, confidentiality | Quarterly audits, automated compliance checks via Vanta/Drata |
| ISO 27001 | ISMS, risk assessment, controls | Documented risk register, annual penetration testing |
| RBI (Account Aggregator) | Consent-based data sharing, encryption | AA framework integration, FIU registration |
| GSTN | Secure API access, audit trail | mTLS with GSTN, immutable transaction logs |

### 1.2 Data Residency
- **Primary:** AWS Mumbai (ap-south-1) / GCP Mumbai (asia-south1)
- **Backup:** AWS Hyderabad (ap-south-2) for disaster recovery
- **Cross-border:** EU client data processed under adequacy decisions; US clients under DPF (Data Privacy Framework)

---

## 2. Encryption Standards

### 2.1 At Rest
| Layer | Algorithm | Key Management |
|-------|-----------|---------------|
| PostgreSQL | AES-256-TDE | AWS KMS / HashiCorp Vault |
| S3 Objects | AES-256-GCM | SSE-KMS with customer-managed keys |
| Redis | AES-256 | Redis AUTH + TLS |
| Vector DB | AES-256 | Qdrant native encryption |

### 2.2 In Transit
- **External:** TLS 1.3 mandatory (TLS 1.2 fallback for legacy ERP connectors)
- **Internal:** mTLS via Istio/Linkerd service mesh
- **Database:** PostgreSQL SSL mode=require, certificate pinning

### 2.3 PII Handling
```python
# Format-Preserving Encryption for PAN
# Example: ABCDE1234F -> XXXXX1234X (preserves format for validation)

def encrypt_pan(pan: str, key: bytes) -> str:
    # FPE-FF1 (NIST SP 800-38G)
    from cryptography.hazmat.primitives.ciphers import Cipher
    # Preserves: 5 letters + 4 digits + 1 letter
    return fpe_encrypt(pan, key, alphabet=string.ascii_uppercase + string.digits)

def tokenize_for_llm(pan: str) -> str:
    # Replace with non-reversible token before sending to LLM
    return hashlib.sha256(pan.encode()).hexdigest()[:16]
```

---

## 3. Authentication & Authorization

### 3.1 Identity Architecture
```
[User] -> [OAuth 2.0 + OIDC] -> [Keycloak / Auth0]
    |
    v
[MFA] -> TOTP / Biometric (WebAuthn) / Push Notification
    |
    v
[JWT] -> RS256 signed, 15-min access / 7-day refresh
    |
    v
[RBAC] -> Role-based (admin, ca, accountant, viewer, auditor)
    |
    v
[ABAC] -> Attribute-based (client_id, department, clearance_level)
    |
    v
[Row-Level Security] -> PostgreSQL RLS policies
```

### 3.2 Role Permissions Matrix
| Action | Admin | CA | Accountant | Viewer | Auditor |
|--------|-------|-----|-----------|--------|---------|
| View ledger | Y | Y | Y | Y | Y |
| Create entry | Y | Y | Y | N | N |
| Modify entry | Y | Y | N | N | N |
| Delete entry | N | N | N | N | N |
| File GST | Y | Y | N | N | N |
| Export PII | Y | N | N | N | N |
| Run anomaly scan | Y | Y | N | N | Y |
| Approve 409A | Y | Y | N | N | N |

---

## 4. AI Security

### 4.1 Prompt Injection Defense
| Attack Vector | Defense |
|--------------|---------|
| Direct injection | Input sanitization + regex filtering |
| Indirect injection | Document sandboxing (isolated OCR processing) |
| Jailbreak attempts | NeMo Guardrails topical constraints |
| System prompt leak | Server-side prompt assembly only |

### 4.2 Hallucination Mitigation
1. **Retrieval Grounding:** All tax/legal responses must cite source document
2. **Self-Reflection:** Secondary model evaluates primary response against retrieved context
3. **Confidence Scoring:** < 0.85 confidence triggers human CPA review
4. **Rate Limiting:** 100 requests/minute per client to prevent brute-force probing

### 4.3 Model Security
- **PII Workloads:** vLLM with Llama-3-70B on private GPU instances (no data leaves VPC)
- **Public Workloads:** GPT-4o/Claude via LiteLLM proxy with token cost tracking
- **Model Versioning:** Pinned model versions; A/B testing via feature flags

---

## 5. Incident Response

### 5.1 Severity Levels
| Level | Criteria | Response Time | Action |
|-------|----------|--------------|--------|
| P1 | Data breach, unauthorized access, system down | 15 min | Page on-call, freeze deployments, preserve logs |
| P2 | Guardrails bypass, RAG poisoning, API abuse | 1 hour | Isolate affected service, rotate credentials |
| P3 | Performance degradation, non-critical bug | 4 hours | Ticket queue, scheduled fix |
| P4 | Cosmetic issues, documentation | 24 hours | Backlog |

### 5.2 AI-Specific Incident Playbooks
- **Hallucination in filed return:** Immediate rollback, CPA review, client notification within 4 hours
- **RAG context poisoning:** Purge vector DB, re-ingest from known-good sources, forensic analysis
- **Guardrails bypass:** Emergency model switch to conservative fallback, audit all recent responses

---

## 6. Audit & Logging

### 6.1 Immutable Audit Trail
- **Format:** Chain-hashed SHA-256 (each entry includes hash of previous)
- **Storage:** WORM S3 bucket + PostgreSQL partitioned table
- **Retention:** 7 years (regulatory requirement for Indian accounting firms)
- **Access:** Read-only via dedicated audit role; no DELETE permissions

### 6.2 AI Decision Logging
Every AI response logs:
```json
{
  "request_id": "uuid",
  "client_id": "uuid",
  "user_id": "uuid",
  "intent": "gst_reconciliation",
  "model": "gpt-4o-2024-05-13",
  "prompt_hash": "sha256",
  "retrieved_docs": ["doc_id_1", "doc_id_2"],
  "response_hash": "sha256",
  "confidence": 0.92,
  "guardrails_result": "allowed",
  "latency_ms": 1450,
  "timestamp": "2026-06-07T12:32:00Z"
}
```

---

*Document Version: 3.0*
*Classification: Confidential*
