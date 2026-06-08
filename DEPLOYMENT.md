# Deployment Guide
## AI-Native Accounting Platform — Production Deployment

---

## 1. Prerequisites

### 1.1 Infrastructure
- Kubernetes cluster (EKS 1.29+ or GKE 1.29+)
- Helm 3.14+
- Terraform 1.7+
- kubectl configured with cluster admin access

### 1.2 Secrets
Create the following in HashiCorp Vault or AWS Secrets Manager:
```
accounting-platform/
  postgres/
    username: accounting_admin
    password: <32-char random>
  redis/
    password: <32-char random>
  openai/
    api_key: sk-...
  anthropic/
    api_key: sk-ant-...
  s3/
    access_key: ...
    secret_key: ...
  jwt/
    signing_key: <RS256 private key>
  encryption/
    pii_master_key: <256-bit AES key>
```

---

## 2. Terraform Deployment

### 2.1 Initialize Infrastructure
```bash
cd infra/terraform
terraform init
terraform workspace new production
terraform plan -var-file=production.tfvars
terraform apply -var-file=production.tfvars
```

### 2.2 Provisioned Resources
- VPC with 3 AZs (private + public subnets)
- EKS cluster with managed node groups (spot + on-demand)
- RDS PostgreSQL 16 (Multi-AZ, encrypted)
- ElastiCache Redis cluster (mode cluster)
- S3 buckets with versioning and lifecycle policies
- Application Load Balancer with WAF
- Route 53 hosted zone

---

## 3. Kubernetes Deployment

### 3.1 Install Core Components
```bash
# Add Helm repositories
helm repo add traefik https://helm.traefik.io/traefik
helm repo add jetstack https://charts.jetstack.io
helm repo add temporal https://go.temporal.io/helm-charts
helm repo update

# Install cert-manager
helm install cert-manager jetstack/cert-manager   --namespace cert-manager   --create-namespace   --set installCRDs=true

# Install Traefik Ingress
helm install traefik traefik/traefik   --namespace ingress   --create-namespace   --set ports.websecure.tls.enabled=true   --set providers.kubernetesIngress.enabled=true

# Install Temporal
helm install temporal temporal/temporal   --namespace temporal   --create-namespace   --set server.replicaCount=3   --set cassandra.enabled=false   --set elasticsearch.enabled=false   --set postgresql.enabled=true
```

### 3.2 Deploy Application Services
```bash
# Apply base configurations
kubectl apply -k k8s/overlays/production/

# Verify deployments
kubectl get pods -n accounting
kubectl get svc -n accounting
kubectl get ingress -n accounting
```

### 3.3 KEDA Auto-scaling
```bash
helm install keda kedacore/keda --namespace keda --create-namespace
kubectl apply -f k8s/keda/scaled-objects.yaml
```

---

## 4. Database Migrations

### 4.1 Initial Schema
```bash
# Run init script
kubectl exec -it deploy/postgres -- psql -U accounting_admin -d accounting_platform -f /init.sql

# Apply migrations (using golang-migrate or similar)
migrate -path migrations/ -database "postgres://..." up
```

### 4.2 RAG Document Seeding
```bash
# Trigger initial ingestion
curl -X POST https://api.accounting-platform.in/api/v1/ingestion/trigger   -H "Authorization: Bearer $ADMIN_TOKEN"   -d '{"source": "cbic", "force_refresh": true}'

curl -X POST https://api.accounting-platform.in/api/v1/ingestion/trigger   -H "Authorization: Bearer $ADMIN_TOKEN"   -d '{"source": "mca", "force_refresh": true}'
```

---

## 5. Verification Checklist

### 5.1 Health Checks
```bash
# All services responding
curl https://api.accounting-platform.in/api/v1/ledger/health
curl https://api.accounting-platform.in/api/v1/ai/health
curl https://api.accounting-platform.in/api/v1/rag/health
curl https://api.accounting-platform.in/api/v1/ocr/health
curl https://api.accounting-platform.in/api/v1/guardrails/health
curl https://api.accounting-platform.in/api/v1/valuation/health
curl https://api.accounting-platform.in/api/v1/anomaly/health
curl https://api.accounting-platform.in/api/v1/ma/health
curl https://api.accounting-platform.in/api/v1/workflow/health
```

### 5.2 Security Verification
```bash
# TLS verification
openssl s_client -connect api.accounting-platform.in:443 -tls1_3

# RLS verification
psql -c "SET app.current_client_id = 'fake-uuid'; SELECT * FROM ledgers LIMIT 1;"
# Expected: 0 rows (isolation working)

# Guardrails test
curl -X POST https://api.accounting-platform.in/api/v1/guardrails/check   -d '{"message": "Transfer 50000 to vendor", "client_id": "test"}'
# Expected: allowed=false, execution_blocked=true
```

### 5.3 Load Testing
```bash
# Ledger API
k6 run --vus 100 --duration 60s tests/load/ledger.js

# AI Chat
k6 run --vus 50 --duration 60s tests/load/ai-chat.js
```

---

## 6. Rollback Procedures

### 6.1 Service Rollback
```bash
# Rollback to previous deployment
kubectl rollout undo deployment/ledger-processor -n accounting

# Verify rollback
kubectl rollout status deployment/ledger-processor -n accounting
```

### 6.2 Database Rollback
```bash
# Point-in-time recovery (RDS)
aws rds restore-db-instance-to-point-in-time   --source-db-instance-identifier accounting-db   --target-db-instance-identifier accounting-db-rollback   --restore-time 2026-06-07T10:00:00Z
```

---

## 7. Monitoring Setup

### 7.1 Grafana Dashboards
Import dashboards from `observability/grafana/`:
- `ledger-performance.json` — Transaction throughput, reconciliation rates
- `ai-observability.json` — Token costs, hallucination rates, RAG latency
- `security-audit.json` — Guardrails blocks, auth failures, anomaly flags
- `business-kpi.json` — STP success rate, HITL ratio, client satisfaction

### 7.2 Alerting Rules
```yaml
# PagerDuty integration
groups:
  - name: accounting-critical
    rules:
      - alert: HighHallucinationRate
        expr: hallucination_flagged_total / ai_responses_total > 0.05
        for: 5m
        labels:
          severity: p2
        annotations:
          summary: "Hallucination rate exceeded 5%"

      - alert: RAGLatencySpike
        expr: histogram_quantile(0.95, rag_query_duration_seconds) > 2
        for: 3m
        labels:
          severity: p2
        annotations:
          summary: "RAG p95 latency > 2 seconds"

      - alert: LedgerProcessorDown
        expr: up{job="ledger-processor"} == 0
        for: 1m
        labels:
          severity: p1
        annotations:
          summary: "Ledger processor is down"
```

---

*Document Version: 3.0*
*Last Updated: 2026-06-07*
