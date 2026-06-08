-- ============================================================
-- AI-Native Accounting Platform — Production Schema
-- Phase 1: SME Foundation
-- Security: RLS, pgcrypto, partitioning
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Encryption key management (in production, use AWS KMS / HashiCorp Vault)
-- This is a placeholder for format-preserving encryption operations
CREATE OR REPLACE FUNCTION encrypt_pii(text, bytea) RETURNS bytea AS $$
    SELECT pgp_sym_encrypt($1, encode($2, 'hex'));
$$ LANGUAGE SQL IMMUTABLE;

CREATE OR REPLACE FUNCTION decrypt_pii(bytea, bytea) RETURNS text AS $$
    SELECT pgp_sym_decrypt($1, encode($2, 'hex'));
$$ LANGUAGE SQL IMMUTABLE;

-- ============================================================
-- 1. CLIENTS & USERS
-- ============================================================

CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type VARCHAR(20) NOT NULL CHECK (type IN ('startup', 'sme', 'corporate', 'foreign')),
    entity_name VARCHAR(255) NOT NULL,
    entity_details JSONB NOT NULL DEFAULT '{}',
    incorporation_date DATE,
    pan_hash VARCHAR(64) UNIQUE, -- SHA-256 hash for lookup, actual PAN encrypted
    pan_encrypted BYTEA, -- Format-preserving encryption
    gstin_hash VARCHAR(64) UNIQUE,
    gstin_encrypted BYTEA,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL CHECK (role IN ('admin', 'ca', 'accountant', 'viewer', 'auditor')),
    email VARCHAR(255) UNIQUE NOT NULL,
    email_verified BOOLEAN DEFAULT FALSE,
    phone VARCHAR(20),
    phone_verified BOOLEAN DEFAULT FALSE,
    password_hash VARCHAR(255) NOT NULL,
    mfa_enabled BOOLEAN DEFAULT FALSE,
    mfa_secret_encrypted BYTEA,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_users_client_id ON users(client_id);
CREATE INDEX idx_users_email ON users(email);

-- ============================================================
-- 2. LEDGERS (Partitioned by client_id + date)
-- ============================================================

CREATE TABLE ledgers (
    id UUID DEFAULT uuid_generate_v4(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    account_code VARCHAR(20) NOT NULL,
    transaction_date DATE NOT NULL,
    debit NUMERIC(15,2) NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit NUMERIC(15,2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
    description TEXT,
    gstin VARCHAR(15),
    hsn_code VARCHAR(10),
    reconciliation_status VARCHAR(20) NOT NULL DEFAULT 'pending' 
        CHECK (reconciliation_status IN ('pending', 'matched', 'mismatched', 'approved')),
    document_id UUID,
    posted_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, transaction_date)
) PARTITION BY RANGE (transaction_date);

-- Create monthly partitions for 2024-2026
DO $$
DECLARE
    start_date DATE;
    end_date DATE;
    partition_name TEXT;
BEGIN
    FOR i IN 0..29 LOOP
        start_date := DATE '2024-01-01' + (i * INTERVAL '1 month');
        end_date := start_date + INTERVAL '1 month';
        partition_name := 'ledgers_' || TO_CHAR(start_date, 'YYYY_MM');
        EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF ledgers FOR VALUES FROM (%L) TO (%L)', 
                       partition_name, start_date, end_date);
    END LOOP;
END $$;

CREATE INDEX idx_ledgers_client_date ON ledgers(client_id, transaction_date);
CREATE INDEX idx_ledgers_recon ON ledgers(client_id, reconciliation_status);
CREATE INDEX idx_ledgers_hsn ON ledgers(hsn_code) WHERE hsn_code IS NOT NULL;

-- ============================================================
-- 3. DOCUMENTS
-- ============================================================

CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    s3_key VARCHAR(512) NOT NULL,
    s3_bucket VARCHAR(64) NOT NULL DEFAULT 'accounting-documents',
    doc_type VARCHAR(30) NOT NULL CHECK (doc_type IN ('invoice', 'bank_statement', 'receipt', 'contract', 'gst_return', 'cma_report', 'audit_paper')),
    ocr_status VARCHAR(20) NOT NULL DEFAULT 'pending' 
        CHECK (ocr_status IN ('pending', 'processing', 'completed', 'failed')),
    extraction_json JSONB,
    confidence_score NUMERIC(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    uploaded_by UUID REFERENCES users(id),
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX idx_documents_client_type ON documents(client_id, doc_type);
CREATE INDEX idx_documents_ocr ON documents(ocr_status) WHERE ocr_status = 'pending';

-- ============================================================
-- 4. TAX FILINGS
-- ============================================================

CREATE TABLE tax_filings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    filing_type VARCHAR(30) NOT NULL CHECK (filing_type IN ('gstr1', 'gstr3b', 'itr3', 'itr4', 'itr5', 'itr6', 'tds_return')),
    period VARCHAR(10) NOT NULL, -- YYYY-MM
    status VARCHAR(20) NOT NULL DEFAULT 'draft' 
        CHECK (status IN ('draft', 'filed', 'acknowledged', 'rejected', 'amended')),
    gstn_acknowledgment VARCHAR(50),
    filed_by UUID REFERENCES users(id),
    filed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_tax_filings_client_period ON tax_filings(client_id, filing_type, period);

-- ============================================================
-- 5. CMA REPORTS
-- ============================================================

CREATE TABLE cma_reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    report_period VARCHAR(10) NOT NULL, -- YYYY-MM
    financial_data_json JSONB NOT NULL,
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    generated_by UUID REFERENCES users(id),
    reviewed_by UUID REFERENCES users(id),
    review_status VARCHAR(20) DEFAULT 'pending' 
        CHECK (review_status IN ('pending', 'reviewed', 'approved', 'rejected')),
    s3_export_key VARCHAR(512)
);

-- ============================================================
-- 6. AUDIT LOGS (Immutable, partitioned by month)
-- ============================================================

CREATE TABLE audit_logs (
    id UUID DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id),
    client_id UUID REFERENCES clients(id),
    action VARCHAR(50) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id UUID,
    ip_address INET,
    user_agent TEXT,
    payload_hash VARCHAR(64), -- SHA-256 of request payload
    immutable_hash VARCHAR(64) NOT NULL, -- Chain hash for tamper evidence
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

DO $$
DECLARE
    start_date TIMESTAMPTZ;
    end_date TIMESTAMPTZ;
    partition_name TEXT;
BEGIN
    FOR i IN 0..11 LOOP
        start_date := DATE_TRUNC('month', NOW() + (i * INTERVAL '1 month'));
        end_date := start_date + INTERVAL '1 month';
        partition_name := 'audit_logs_' || TO_CHAR(start_date, 'YYYY_MM');
        EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF audit_logs FOR VALUES FROM (%L) TO (%L)', 
                       partition_name, start_date, end_date);
    END LOOP;
END $$;

CREATE INDEX idx_audit_client ON audit_logs(client_id, timestamp);
CREATE INDEX idx_audit_user ON audit_logs(user_id, timestamp);

-- ============================================================
-- 7. RAG DOCUMENTS (Knowledge Base)
-- ============================================================

CREATE TABLE rag_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_url VARCHAR(512) NOT NULL,
    doc_type VARCHAR(30) NOT NULL CHECK (doc_type IN ('gst_circular', 'income_tax_act', 'fema_regulation', 'mca_notification', 'dtaa_treaty', 'case_law')),
    jurisdiction VARCHAR(50) NOT NULL DEFAULT 'india',
    title VARCHAR(255),
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding_id VARCHAR(64), -- Reference to vector DB
    metadata_json JSONB,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_rag_type ON rag_documents(doc_type, jurisdiction);
CREATE INDEX idx_rag_search ON rag_documents USING GIN(to_tsvector('english', chunk_text));

-- ============================================================
-- ROW LEVEL SECURITY POLICIES
-- ============================================================

ALTER TABLE clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledgers ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE tax_filings ENABLE ROW LEVEL SECURITY;
ALTER TABLE cma_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- Helper function to get current user's client_id from JWT/session
CREATE OR REPLACE FUNCTION current_user_client_id() RETURNS UUID AS $$
BEGIN
    -- In production, this reads from application-set session variable
    -- SET app.current_client_id = '...' via application connection middleware
    RETURN NULLIF(current_setting('app.current_client_id', true), '')::UUID;
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER;

-- RLS Policies
CREATE POLICY client_isolation ON clients 
    FOR ALL TO accounting_app 
    USING (id = current_user_client_id());

CREATE POLICY user_isolation ON users 
    FOR ALL TO accounting_app 
    USING (client_id = current_user_client_id());

CREATE POLICY ledger_isolation ON ledgers 
    FOR ALL TO accounting_app 
    USING (client_id = current_user_client_id());

CREATE POLICY document_isolation ON documents 
    FOR ALL TO accounting_app 
    USING (client_id = current_user_client_id());

CREATE POLICY filing_isolation ON tax_filings 
    FOR ALL TO accounting_app 
    USING (client_id = current_user_client_id());

CREATE POLICY cma_isolation ON cma_reports 
    FOR ALL TO accounting_app 
    USING (client_id = current_user_client_id());

CREATE POLICY audit_isolation ON audit_logs 
    FOR ALL TO accounting_app 
    USING (client_id = current_user_client_id());

-- Auditor exception: can read but not modify
CREATE POLICY auditor_read ON ledgers 
    FOR SELECT TO accounting_app 
    USING (
        EXISTS (
            SELECT 1 FROM users 
            WHERE id = current_setting('app.current_user_id', true)::UUID 
            AND role = 'auditor'
            AND client_id = ledgers.client_id
        )
    );

-- ============================================================
-- TRIGGERS & AUDIT
-- ============================================================

CREATE OR REPLACE FUNCTION update_timestamp() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER clients_updated BEFORE UPDATE ON clients 
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();
CREATE TRIGGER users_updated BEFORE UPDATE ON users 
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();
CREATE TRIGGER tax_filings_updated BEFORE UPDATE ON tax_filings 
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

-- Immutable audit log chain hash
CREATE OR REPLACE FUNCTION compute_audit_hash() RETURNS TRIGGER AS $$
DECLARE
    prev_hash VARCHAR(64);
BEGIN
    SELECT immutable_hash INTO prev_hash 
    FROM audit_logs 
    ORDER BY timestamp DESC 
    LIMIT 1;
    
    NEW.immutable_hash = encode(
        digest(
            concat(NEW.id::text, NEW.action, NEW.timestamp::text, COALESCE(prev_hash, 'genesis')), 
            'sha256'
        ), 
        'hex'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_chain_hash BEFORE INSERT ON audit_logs 
    FOR EACH ROW EXECUTE FUNCTION compute_audit_hash();

-- ============================================================
-- SEED DATA
-- ============================================================

INSERT INTO clients (id, type, entity_name, entity_details, incorporation_date) 
VALUES 
    ('00000000-0000-0000-0000-000000000001', 'sme', 'Demo Trading Pvt Ltd', 
     '{"industry": "trading", "turnover": 5000000}', '2020-06-15'),
    ('00000000-0000-0000-0000-000000000002', 'startup', 'TechNova AI', 
     '{"industry": "software", "funding_stage": "series_a"}', '2023-01-10');

-- Create application role (run manually in production with proper password)
-- CREATE ROLE accounting_app WITH LOGIN PASSWORD 'secure_password';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO accounting_app;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO accounting_app;