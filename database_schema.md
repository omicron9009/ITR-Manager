# ITR Filing Management Platform — PostgreSQL Database Schema

**Version:** 1.0  
**Date:** May 2026  
**Source:** ITR_Filing_Platform_BRD v1.1  
**Target Database:** PostgreSQL 16+  
**Backend Framework:** FastAPI (Python)

---

## 1. BRD Understanding

### Business Problem
A CA practice needs an internal platform to manage the end-to-end ITR filing lifecycle for ~1,000–2,000 clients. The platform digitizes client onboarding, document collection/verification, computation approval, filing tracking, and payment acknowledgement — replacing manual spreadsheets and email-based coordination.

### Identified Modules (FastAPI Service Modules)

| Module | Description |
|--------|-------------|
| `auth` | Authentication integration with Authentik (JWT validation, role enforcement) |
| `users` | User management — Partner, Executive, Client accounts |
| `clients` | Client registration, verification, profile management |
| `executives` | Executive CRUD, assignment to clients |
| `onboarding` | Dynamic form builder, form submissions |
| `filings` | ITR filing lifecycle, state machine transitions |
| `documents` | Master document types, placeholder management, upload/review workflows |
| `computations` | Computation upload, client approval workflow |
| `notifications` | Email (Gmail API) and in-app notification delivery |
| `audit` | Audit log recording and HTML report generation |
| `storage` | MinIO integration — pre-signed URL generation, file metadata |

### Key Actors
- **Partner** — Single super-admin; full system access, account activation, executive management
- **Executive** — Staff accounts scoped to assigned clients; full filing actions on those clients
- **Client** — Self-registered taxpayers; restricted to own filings and documents

### Key Workflows
1. Client Registration → PAN Upload → Partner Verification → Account Activation
2. Filing Initiation → Executive Assignment → Document Placeholder Assignment → Document Upload/Review Loop → Computation Upload → Client Approval → ITR Filing → Payment → Completion
3. Multi-round document rejection/re-upload cycle
4. Multi-round computation revision cycle

---

## 2. Assumptions

1. **UUID v4 as primary keys** — chosen over BIGSERIAL to prevent ID enumeration attacks in REST APIs, support future horizontal scaling, and work cleanly with FastAPI/Pydantic serialization.
2. **Single-tenant system** — one CA practice, no multi-tenancy table needed.
3. **Authentik manages authentication credentials** — the `users` table stores profile/role data but NOT passwords (Authentik is the IdP). A `authentik_subject_id` links to the Authentik user.
4. **Financial year stored as VARCHAR(9)** format `YYYY-YYYY` (e.g., `2024-2025`) — normalized lookup table not needed since FY is a simple derivable value.
5. **One Executive per client** at any given time (BRD says "assigned Executive" singular). Re-assignment is allowed (history tracked via audit log).
6. **Document placeholder status is per-placeholder, not per-submission-round** — each placeholder independently tracks its status.
7. **Computation documents are versioned** — Executive/Partner can upload revised versions; only latest is shown to client.
8. **Soft delete via `is_active` flag** for master data (document types, form fields, executive accounts). Hard delete is not performed.
9. **Client profile stores both structured key fields AND dynamic JSONB** — hybrid approach for searchability + flexibility.
10. **Notification read status is per-user** — no shared notification instances.
11. **Audit log is append-only** — no updates or deletes permitted.
12. **Filed Documents folder holds exactly two document types: ITR Acknowledgement and Invoice** — modeled as separate columns/records.
13. **Form field definitions are NOT versioned in v1.0** — changes apply globally; existing submissions retain their data as JSONB snapshots.
14. **Partner account is seeded at deployment time** — not created via registration flow.

---

## 3. Entity Catalog

### Security / User Management

| Table | Purpose | Category |
|-------|---------|----------|
| `users` | All system users (Partner, Executive, Client) | security |
| `client_profiles` | Extended client data from onboarding form | master |
| `executive_client_assignments` | Maps executives to clients | junction |

### Master Data

| Table | Purpose | Category |
|-------|---------|----------|
| `master_document_types` | Editable list of all possible document types | master |
| `onboarding_form_fields` | Dynamic form field definitions (Partner-managed) | master |

### Filing / Transaction

| Table | Purpose | Category |
|-------|---------|----------|
| `itr_filings` | One record per client per financial year — core transaction | transaction |
| `filing_documents` | Document placeholders + upload tracking per filing | transaction |
| `filing_computations` | Computation document versions per filing | transaction |
| `filing_completed_docs` | Acknowledgement and invoice uploads | transaction |
| `filing_state_history` | State transition log per filing | audit |

### File Storage

| Table | Purpose | Category |
|-------|---------|----------|
| `stored_files` | MinIO object metadata (all uploaded files) | attachment |

### Notifications

| Table | Purpose | Category |
|-------|---------|----------|
| `notifications` | In-app notifications per user | transaction |

### Audit

| Table | Purpose | Category |
|-------|---------|----------|
| `audit_logs` | Append-only event log for all auditable actions | audit |

---

## 4. Relationship Map

```
users 1:1 client_profiles                (client user → profile)
users 1:N itr_filings                    (client → filings)
users 1:N executive_client_assignments   (executive → assignments)
users 1:N executive_client_assignments   (client → assignments)
users 1:N notifications                  (user → notifications)
users 1:N audit_logs                     (actor → audit entries)
users 1:N stored_files                   (uploader → files)

itr_filings 1:N filing_documents         (filing → document placeholders)
itr_filings 1:N filing_computations      (filing → computation versions)
itr_filings 1:N filing_completed_docs    (filing → acknowledgement/invoice)
itr_filings 1:N filing_state_history     (filing → state transitions)

master_document_types 1:N filing_documents  (doc type → placeholders)

filing_documents N:1 stored_files        (placeholder → uploaded file)
filing_computations N:1 stored_files     (computation → file)
filing_completed_docs N:1 stored_files   (completed doc → file)
users 1:1 stored_files                   (PAN upload at registration)

executive_client_assignments links users(executive) M:N users(client)
```

---

## 5. Schema Design Notes

### Normalization Decisions
- **3NF maintained throughout.** No denormalized counters or aggregates stored — dashboard counters are computed via queries/views.
- `client_profiles.form_data` uses JSONB for the dynamic form submission snapshot, while key searchable fields (PAN, contact, income type) are extracted as indexed columns.
- Financial year is stored as a VARCHAR in `itr_filings` rather than a separate lookup table — it's a simple formatted string with a CHECK constraint.

### Enum vs Lookup Decisions
- **PostgreSQL ENUM types used for stable, code-level values:** `user_role`, `account_status`, `filing_status`, `document_status`, `computation_status`, `completed_doc_type`, `audit_event_type`, `form_field_type`.
- **Lookup table used for `master_document_types`** — this is business-managed (Partner adds/removes types), so it cannot be an enum.
- Rationale: Enums are used where values are defined by the system and change only with code deployments. Lookup tables are used where business users manage the values.

### Audit Strategy
- **`audit_logs` table** — append-only, records every document event, state transition, account activation/rejection, and document status change.
- **`filing_state_history` table** — dedicated state transition history for the filing state machine (subset of audit_logs but optimized for filing timeline display).
- **`created_at` / `updated_at` / `created_by` / `updated_by`** on all business tables for row-level traceability.
- Audit log HTML generation is a read-only report query — no materialized views needed at 2,000 client scale.

### Delete Strategy
- **Soft delete (`is_active = false`)** for: `users` (executive deactivation), `master_document_types`, `onboarding_form_fields`.
- **No delete** for: `itr_filings`, `filing_documents`, `audit_logs`, `notifications`, `stored_files` — BRD states indefinite retention.
- **Hard delete not implemented** in v1.0 — no purge policy defined.

### Indexing Strategy
- All foreign keys indexed (PostgreSQL does NOT auto-index FK columns).
- Composite unique indexes for business keys: `(client_id, financial_year)` on `itr_filings`.
- Status columns indexed where dashboard filtering occurs.
- `created_at` indexed on audit_logs and notifications for time-range queries.
- Partial indexes used where appropriate (e.g., active filings only).

### Partitioning
- Not required at 1,000–2,000 client scale. `audit_logs` could be range-partitioned by `created_at` in future if it grows beyond 10M rows.

### Compliance / Traceability
- Every state transition recorded with actor, timestamp, and optional remarks.
- Document uploads/downloads/approvals/rejections all logged.
- PAN documents stored with restricted access metadata.
- Audit log generation filters by client and date range per BRD §9.8.

---

## 6. PostgreSQL DDL

```sql
-- =============================================================
-- ITR Filing Management Platform — Database Schema
-- PostgreSQL 16+
-- Generated: May 2026
-- =============================================================

-- =============================================================
-- EXTENSIONS
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================
-- ENUM TYPES
-- =============================================================

CREATE TYPE user_role AS ENUM ('PARTNER', 'EXECUTIVE', 'CLIENT');

CREATE TYPE account_status AS ENUM (
    'PENDING_VERIFICATION',
    'ACTIVE',
    'REJECTED',
    'DEACTIVATED'
);

CREATE TYPE filing_status AS ENUM (
    'INITIATED',
    'ON_BOARDING',
    'PROCESSING',
    'COMPUTATION',
    'FILING',
    'PAYMENT',
    'COMPLETED',
    'HALTED'
);

CREATE TYPE document_status AS ENUM (
    'PENDING_UPLOAD',
    'UPLOADED',
    'REJECTED',
    'APPROVED'
);

CREATE TYPE computation_status AS ENUM (
    'UPLOADED',
    'APPROVED',
    'SUPERSEDED'
);

CREATE TYPE completed_doc_type AS ENUM (
    'ITR_ACKNOWLEDGEMENT',
    'INVOICE'
);

CREATE TYPE form_field_type AS ENUM (
    'TEXT',
    'NUMBER',
    'DATE',
    'DROPDOWN',
    'FILE'
);

CREATE TYPE audit_event_type AS ENUM (
    'ACCOUNT_REGISTERED',
    'ACCOUNT_ACTIVATED',
    'ACCOUNT_REJECTED',
    'ACCOUNT_DEACTIVATED',
    'ACCOUNT_REACTIVATED',
    'EXECUTIVE_CREATED',
    'EXECUTIVE_ASSIGNED',
    'EXECUTIVE_UNASSIGNED',
    'FILING_INITIATED',
    'FILING_STATE_CHANGED',
    'FILING_HALTED',
    'DOCUMENT_PLACEHOLDER_CREATED',
    'DOCUMENT_UPLOADED',
    'DOCUMENT_APPROVED',
    'DOCUMENT_REJECTED',
    'DOCUMENT_DOWNLOADED',
    'COMPUTATION_UPLOADED',
    'COMPUTATION_APPROVED',
    'COMPUTATION_SUPERSEDED',
    'ITR_FILED',
    'PAYMENT_RECEIVED',
    'INVOICE_UPLOADED',
    'FORM_FIELD_ADDED',
    'FORM_FIELD_UPDATED',
    'FORM_FIELD_REMOVED',
    'MASTER_DOC_TYPE_ADDED',
    'MASTER_DOC_TYPE_UPDATED',
    'MASTER_DOC_TYPE_REMOVED'
);

CREATE TYPE notification_channel AS ENUM (
    'IN_APP',
    'EMAIL',
    'BOTH'
);

-- =============================================================
-- TABLE: stored_files
-- Purpose: MinIO object metadata for all uploaded files
-- =============================================================

CREATE TABLE stored_files (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bucket          VARCHAR(63) NOT NULL,
    object_key      TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    content_type    VARCHAR(255) NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    uploaded_by     UUID,  -- FK added after users table created
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_file_size_positive CHECK (file_size_bytes > 0)
);

CREATE UNIQUE INDEX idx_stored_files_bucket_key ON stored_files (bucket, object_key);

-- =============================================================
-- TABLE: users
-- Purpose: All system users — Partner, Executive, Client
-- =============================================================

CREATE TABLE users (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    authentik_subject_id VARCHAR(255) NOT NULL,
    email               VARCHAR(255) NOT NULL,
    full_name           VARCHAR(255) NOT NULL,
    role                user_role NOT NULL,
    account_status      account_status NOT NULL DEFAULT 'PENDING_VERIFICATION',
    pan_document_id     UUID REFERENCES stored_files(id) ON DELETE SET NULL,
    rejection_reason    TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    activated_at        TIMESTAMPTZ,
    activated_by        UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT uq_users_authentik_subject UNIQUE (authentik_subject_id),
    CONSTRAINT chk_client_pending_or_active CHECK (
        role != 'CLIENT' OR account_status IN ('PENDING_VERIFICATION', 'ACTIVE', 'REJECTED')
    ),
    CONSTRAINT chk_executive_status CHECK (
        role != 'EXECUTIVE' OR account_status IN ('ACTIVE', 'DEACTIVATED')
    ),
    CONSTRAINT chk_partner_status CHECK (
        role != 'PARTNER' OR account_status = 'ACTIVE'
    )
);

-- Add FK for stored_files.uploaded_by now that users exists
ALTER TABLE stored_files
    ADD CONSTRAINT fk_stored_files_uploaded_by
    FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL;

-- Add self-referencing FK for activated_by
ALTER TABLE users
    ADD CONSTRAINT fk_users_activated_by
    FOREIGN KEY (activated_by) REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX idx_users_role ON users (role);
CREATE INDEX idx_users_account_status ON users (account_status);
CREATE INDEX idx_users_email ON users (email);
CREATE INDEX idx_users_pan_document_id ON users (pan_document_id);
CREATE INDEX idx_users_is_active ON users (is_active) WHERE is_active = true;

-- =============================================================
-- TABLE: client_profiles
-- Purpose: Extended client data from onboarding form submission
-- =============================================================

CREATE TABLE client_profiles (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pan_number      VARCHAR(10),
    aadhaar_number  VARCHAR(12),
    date_of_birth   DATE,
    contact_number  VARCHAR(15),
    address         TEXT,
    income_type     VARCHAR(50),
    bank_account_details TEXT,
    form_data       JSONB NOT NULL DEFAULT '{}',
    form_submitted_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_client_profiles_user UNIQUE (user_id),
    CONSTRAINT chk_pan_format CHECK (
        pan_number IS NULL OR pan_number ~ '^[A-Z]{5}[0-9]{4}[A-Z]{1}$'
    ),
    CONSTRAINT chk_aadhaar_format CHECK (
        aadhaar_number IS NULL OR aadhaar_number ~ '^\d{12}$'
    )
);

CREATE INDEX idx_client_profiles_pan ON client_profiles (pan_number);
CREATE INDEX idx_client_profiles_user_id ON client_profiles (user_id);

-- =============================================================
-- TABLE: executive_client_assignments
-- Purpose: Maps which Executive is assigned to which Client
-- =============================================================

CREATE TABLE executive_client_assignments (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    executive_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_by     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    unassigned_at   TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT true,

    CONSTRAINT uq_active_assignment_per_client UNIQUE (client_id) WHERE (is_active = true),
    CONSTRAINT chk_executive_not_client CHECK (executive_id != client_id)
);

-- PostgreSQL doesn't support WHERE in UNIQUE constraint via DDL; use partial unique index instead
-- Drop the invalid constraint and use index:
ALTER TABLE executive_client_assignments DROP CONSTRAINT IF EXISTS uq_active_assignment_per_client;

CREATE UNIQUE INDEX idx_unique_active_assignment_per_client
    ON executive_client_assignments (client_id) WHERE (is_active = true);

CREATE INDEX idx_eca_executive_id ON executive_client_assignments (executive_id);
CREATE INDEX idx_eca_client_id ON executive_client_assignments (client_id);
CREATE INDEX idx_eca_is_active ON executive_client_assignments (is_active) WHERE is_active = true;

-- =============================================================
-- TABLE: master_document_types
-- Purpose: Partner-managed list of all possible document types
-- =============================================================

CREATE TABLE master_document_types (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    display_order   INTEGER NOT NULL DEFAULT 0,
    created_by      UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_master_doc_type_name UNIQUE (name)
);

CREATE INDEX idx_master_doc_types_active ON master_document_types (is_active) WHERE is_active = true;

-- Seed suggestions:
-- INSERT INTO master_document_types (name, description, created_by) VALUES
-- ('Form 16', 'TDS certificate from employer', '<partner_uuid>'),
-- ('Bank Statement', 'Savings account statement for the FY', '<partner_uuid>'),
-- ('Capital Gains Statement', 'Statement from broker/DP', '<partner_uuid>'),
-- ('Interest Certificate', 'FD/RD interest certificate', '<partner_uuid>'),
-- ('Rent Receipts', 'For HRA exemption claim', '<partner_uuid>'),
-- ('Home Loan Statement', 'Principal and interest certificate', '<partner_uuid>'),
-- ('Investment Proofs', '80C/80D deduction proofs', '<partner_uuid>'),
-- ('Form 26AS / AIS', 'Annual Information Statement', '<partner_uuid>');

-- =============================================================
-- TABLE: onboarding_form_fields
-- Purpose: Dynamic form field definitions managed by Partner
-- =============================================================

CREATE TABLE onboarding_form_fields (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    field_label     VARCHAR(255) NOT NULL,
    field_key       VARCHAR(100) NOT NULL,
    field_type      form_field_type NOT NULL,
    field_options   JSONB,  -- For DROPDOWN: ["Option A", "Option B", ...]
    is_required     BOOLEAN NOT NULL DEFAULT false,
    display_order   INTEGER NOT NULL DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_by      UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_form_field_key UNIQUE (field_key),
    CONSTRAINT chk_dropdown_has_options CHECK (
        field_type != 'DROPDOWN' OR (field_options IS NOT NULL AND jsonb_array_length(field_options) > 0)
    )
);

CREATE INDEX idx_form_fields_active_order ON onboarding_form_fields (display_order) WHERE is_active = true;

-- =============================================================
-- TABLE: itr_filings
-- Purpose: Core transaction — one record per client per FY
-- =============================================================

CREATE TABLE itr_filings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id       UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    financial_year  VARCHAR(9) NOT NULL,  -- Format: '2024-2025'
    status          filing_status NOT NULL DEFAULT 'INITIATED',
    assigned_executive_id UUID REFERENCES users(id) ON DELETE SET NULL,
    initiated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    onboarding_completed_at TIMESTAMPTZ,
    documents_submitted_at TIMESTAMPTZ,
    documents_approved_at TIMESTAMPTZ,
    computation_uploaded_at TIMESTAMPTZ,
    computation_approved_at TIMESTAMPTZ,
    filed_at        TIMESTAMPTZ,
    payment_received_at TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    halted_at       TIMESTAMPTZ,
    halted_by       UUID REFERENCES users(id) ON DELETE SET NULL,
    halt_reason     TEXT,
    created_by      UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_client_fy UNIQUE (client_id, financial_year),
    CONSTRAINT chk_financial_year_format CHECK (
        financial_year ~ '^\d{4}-\d{4}$'
    )
);

CREATE INDEX idx_filings_client_id ON itr_filings (client_id);
CREATE INDEX idx_filings_status ON itr_filings (status);
CREATE INDEX idx_filings_financial_year ON itr_filings (financial_year);
CREATE INDEX idx_filings_assigned_executive ON itr_filings (assigned_executive_id);
CREATE INDEX idx_filings_created_at ON itr_filings (created_at);
CREATE INDEX idx_filings_active_status ON itr_filings (status) WHERE status NOT IN ('COMPLETED', 'HALTED');

-- =============================================================
-- TABLE: filing_documents
-- Purpose: Document placeholders and upload tracking per filing
-- =============================================================

CREATE TABLE filing_documents (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filing_id           UUID NOT NULL REFERENCES itr_filings(id) ON DELETE CASCADE,
    document_type_id    UUID NOT NULL REFERENCES master_document_types(id) ON DELETE RESTRICT,
    status              document_status NOT NULL DEFAULT 'PENDING_UPLOAD',
    file_id             UUID REFERENCES stored_files(id) ON DELETE SET NULL,
    rejection_reason    TEXT,
    uploaded_at         TIMESTAMPTZ,
    reviewed_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at         TIMESTAMPTZ,
    assigned_by         UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assigned_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_filing_doc_type UNIQUE (filing_id, document_type_id),
    CONSTRAINT chk_rejected_has_reason CHECK (
        status != 'REJECTED' OR rejection_reason IS NOT NULL
    ),
    CONSTRAINT chk_uploaded_has_file CHECK (
        status = 'PENDING_UPLOAD' OR file_id IS NOT NULL
    )
);

CREATE INDEX idx_filing_docs_filing_id ON filing_documents (filing_id);
CREATE INDEX idx_filing_docs_status ON filing_documents (status);
CREATE INDEX idx_filing_docs_doc_type ON filing_documents (document_type_id);
CREATE INDEX idx_filing_docs_file_id ON filing_documents (file_id);

-- =============================================================
-- TABLE: filing_computations
-- Purpose: Versioned computation documents per filing
-- =============================================================

CREATE TABLE filing_computations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filing_id       UUID NOT NULL REFERENCES itr_filings(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL DEFAULT 1,
    file_id         UUID NOT NULL REFERENCES stored_files(id) ON DELETE RESTRICT,
    status          computation_status NOT NULL DEFAULT 'UPLOADED',
    uploaded_by     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_filing_computation_version UNIQUE (filing_id, version),
    CONSTRAINT chk_version_positive CHECK (version > 0)
);

CREATE INDEX idx_filing_comp_filing_id ON filing_computations (filing_id);
CREATE INDEX idx_filing_comp_status ON filing_computations (status);
CREATE INDEX idx_filing_comp_latest ON filing_computations (filing_id, version DESC);

-- =============================================================
-- TABLE: filing_completed_docs
-- Purpose: ITR Acknowledgement and Invoice documents
-- =============================================================

CREATE TABLE filing_completed_docs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filing_id       UUID NOT NULL REFERENCES itr_filings(id) ON DELETE CASCADE,
    doc_type        completed_doc_type NOT NULL,
    file_id         UUID NOT NULL REFERENCES stored_files(id) ON DELETE RESTRICT,
    uploaded_by     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_filing_completed_doc_type UNIQUE (filing_id, doc_type)
);

CREATE INDEX idx_filing_completed_filing_id ON filing_completed_docs (filing_id);

-- =============================================================
-- TABLE: filing_state_history
-- Purpose: State transition log for filing state machine
-- =============================================================

CREATE TABLE filing_state_history (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    filing_id       UUID NOT NULL REFERENCES itr_filings(id) ON DELETE CASCADE,
    from_status     filing_status,
    to_status       filing_status NOT NULL,
    changed_by      UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    remarks         TEXT,

    CONSTRAINT chk_status_different CHECK (
        from_status IS NULL OR from_status != to_status
    )
);

CREATE INDEX idx_state_history_filing_id ON filing_state_history (filing_id);
CREATE INDEX idx_state_history_changed_at ON filing_state_history (changed_at);
CREATE INDEX idx_state_history_to_status ON filing_state_history (to_status);

-- =============================================================
-- TABLE: notifications
-- Purpose: In-app notification feed per user
-- =============================================================

CREATE TABLE notifications (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           VARCHAR(255) NOT NULL,
    message         TEXT NOT NULL,
    is_read         BOOLEAN NOT NULL DEFAULT false,
    channel         notification_channel NOT NULL DEFAULT 'BOTH',
    email_sent      BOOLEAN NOT NULL DEFAULT false,
    email_sent_at   TIMESTAMPTZ,
    related_filing_id UUID REFERENCES itr_filings(id) ON DELETE SET NULL,
    related_client_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_notifications_user_id ON notifications (user_id);
CREATE INDEX idx_notifications_unread ON notifications (user_id, is_read) WHERE is_read = false;
CREATE INDEX idx_notifications_created_at ON notifications (created_at DESC);
CREATE INDEX idx_notifications_related_filing ON notifications (related_filing_id);

-- =============================================================
-- TABLE: audit_logs
-- Purpose: Append-only audit trail for all system events
-- =============================================================

CREATE TABLE audit_logs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type      audit_event_type NOT NULL,
    actor_id        UUID REFERENCES users(id) ON DELETE SET NULL,
    client_id       UUID REFERENCES users(id) ON DELETE SET NULL,
    filing_id       UUID REFERENCES itr_filings(id) ON DELETE SET NULL,
    document_id     UUID,  -- Can reference filing_documents, filing_computations, or filing_completed_docs
    details         JSONB NOT NULL DEFAULT '{}',
    ip_address      INET,
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Audit logs are append-only; enforce via application-level permissions and/or triggers
CREATE INDEX idx_audit_logs_event_type ON audit_logs (event_type);
CREATE INDEX idx_audit_logs_actor_id ON audit_logs (actor_id);
CREATE INDEX idx_audit_logs_client_id ON audit_logs (client_id);
CREATE INDEX idx_audit_logs_filing_id ON audit_logs (filing_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs (created_at);
CREATE INDEX idx_audit_logs_client_date ON audit_logs (client_id, created_at);

-- =============================================================
-- TRIGGER: Auto-update updated_at on row modification
-- =============================================================

CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER trg_client_profiles_updated_at
    BEFORE UPDATE ON client_profiles
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER trg_master_document_types_updated_at
    BEFORE UPDATE ON master_document_types
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER trg_onboarding_form_fields_updated_at
    BEFORE UPDATE ON onboarding_form_fields
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER trg_itr_filings_updated_at
    BEFORE UPDATE ON itr_filings
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER trg_filing_documents_updated_at
    BEFORE UPDATE ON filing_documents
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================
-- TRIGGER: Prevent audit_log modifications (append-only)
-- =============================================================

CREATE OR REPLACE FUNCTION prevent_audit_log_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit logs are append-only. UPDATE and DELETE operations are not permitted.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_logs_no_update
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_modification();

CREATE TRIGGER trg_audit_logs_no_delete
    BEFORE DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_modification();

-- =============================================================
-- TRIGGER: Validate filing state transitions
-- =============================================================

CREATE OR REPLACE FUNCTION validate_filing_state_transition()
RETURNS TRIGGER AS $$
DECLARE
    valid_transitions JSONB := '{
        "INITIATED": ["ON_BOARDING", "HALTED"],
        "ON_BOARDING": ["PROCESSING", "HALTED"],
        "PROCESSING": ["COMPUTATION", "HALTED"],
        "COMPUTATION": ["FILING", "HALTED"],
        "FILING": ["PAYMENT", "HALTED"],
        "PAYMENT": ["COMPLETED", "HALTED"],
        "HALTED": ["INITIATED", "ON_BOARDING", "PROCESSING", "COMPUTATION", "FILING", "PAYMENT"]
    }'::JSONB;
    allowed JSONB;
BEGIN
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;

    IF OLD.status = 'COMPLETED' THEN
        RAISE EXCEPTION 'Cannot transition from COMPLETED state';
    END IF;

    allowed := valid_transitions -> OLD.status::TEXT;

    IF allowed IS NULL OR NOT allowed ? NEW.status::TEXT THEN
        RAISE EXCEPTION 'Invalid state transition from % to %', OLD.status, NEW.status;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_filing_transition
    BEFORE UPDATE OF status ON itr_filings
    FOR EACH ROW EXECUTE FUNCTION validate_filing_state_transition();

-- =============================================================
-- VIEW: Active filing summary (for dashboard counters)
-- =============================================================

CREATE OR REPLACE VIEW vw_filing_status_summary AS
SELECT
    status,
    COUNT(*) AS total_count
FROM itr_filings
WHERE status != 'HALTED'
GROUP BY status;

-- =============================================================
-- VIEW: Executive workload summary
-- =============================================================

CREATE OR REPLACE VIEW vw_executive_workload AS
SELECT
    u.id AS executive_id,
    u.full_name AS executive_name,
    COUNT(DISTINCT eca.client_id) AS assigned_clients,
    COUNT(DISTINCT f.id) FILTER (WHERE f.status NOT IN ('COMPLETED', 'HALTED')) AS active_filings
FROM users u
LEFT JOIN executive_client_assignments eca ON eca.executive_id = u.id AND eca.is_active = true
LEFT JOIN itr_filings f ON f.assigned_executive_id = u.id
WHERE u.role = 'EXECUTIVE' AND u.is_active = true
GROUP BY u.id, u.full_name;

-- =============================================================
-- VIEW: Client filing overview (for client list)
-- =============================================================

CREATE OR REPLACE VIEW vw_client_filing_overview AS
SELECT
    u.id AS client_id,
    u.full_name AS client_name,
    u.email,
    u.account_status,
    eca.executive_id AS assigned_executive_id,
    exec_user.full_name AS assigned_executive_name,
    ARRAY_AGG(DISTINCT f.financial_year) FILTER (WHERE f.status NOT IN ('COMPLETED', 'HALTED')) AS active_filing_years,
    MAX(f.updated_at) AS last_filing_update
FROM users u
LEFT JOIN executive_client_assignments eca ON eca.client_id = u.id AND eca.is_active = true
LEFT JOIN users exec_user ON exec_user.id = eca.executive_id
LEFT JOIN itr_filings f ON f.client_id = u.id
WHERE u.role = 'CLIENT'
GROUP BY u.id, u.full_name, u.email, u.account_status, eca.executive_id, exec_user.full_name;
```

---

## 7. Validation Checklist

The following items should be reviewed by the Product Owner, BA, and Engineering team before implementation:

### Business Logic
- [ ] Confirm financial year format: is `2024-2025` correct or should it be `2023-24` style?
- [ ] Confirm one Executive per client at a time (current assumption) vs. multiple Executives per client.
- [ ] Confirm whether a HALTED filing can be resumed (current schema allows it via state machine).
- [ ] Confirm whether rejected client registrations can re-register with the same email.
- [ ] Confirm whether document type removal from master list affects existing placeholders (current: soft delete, existing references preserved).

### Data Model
- [ ] Verify PAN format regex `^[A-Z]{5}[0-9]{4}[A-Z]{1}$` matches all valid PAN patterns.
- [ ] Confirm 1 GB max file size is enforced at application/MinIO level (not DB).
- [ ] Confirm whether onboarding form field versioning is needed (current: not versioned in v1.0).
- [ ] Confirm whether notification history should have a retention/purge policy.

### Security
- [ ] Verify Authentik `subject_id` format and length for `authentik_subject_id` column.
- [ ] Confirm PAN document access is restricted to Partner only at API level (not DB level).
- [ ] Confirm row-level security (RLS) is NOT needed since access control is enforced in FastAPI middleware.

### Performance
- [ ] Validate dashboard query performance with 2,000 clients × ~5 FY filings = ~10,000 filing records.
- [ ] Confirm JSONB `form_data` does not need GIN indexing for v1.0.
- [ ] Review audit_log growth projections — partitioning may be needed post-launch.

### Integration
- [ ] Confirm Authentik user provisioning flow — is the user created in PostgreSQL on first login (JIT) or via Authentik webhook?
- [ ] Confirm MinIO bucket naming strategy (per-client? per-FY? single bucket?).
- [ ] Confirm Gmail API quota against expected notification volume.

---

## 8. Open Questions

| # | Question | Impact |
|---|----------|--------|
| 1 | How is the Partner account initially created? Seeded in DB + Authentik during deployment? | Affects migration/seed scripts |
| 2 | When an Executive is re-assigned to a client mid-filing, does the old Executive lose all access immediately? | Affects assignment history tracking |
| 3 | Can a client edit their profile (onboarding data) after initial submission? BRD says "pre-loaded; client may edit before confirming" on subsequent filings — but can they edit independently? | Affects profile update API |
| 4 | Should the system track how many times documents were rejected per filing (rejection round count)? | May need a `filing_document_history` table |
| 5 | Is there a maximum number of computation revisions before escalation? | May need workflow rules |
| 6 | Should the notification email content differ from in-app notification content? | Affects notification schema (may need separate body fields) |
| 7 | For the audit log HTML report, is a PDF export also needed? | Affects report generation service |
| 8 | Should the system support bulk document placeholder assignment (same set for multiple clients)? | May need template/preset tables |
| 9 | Is there a minimum document set that must always be assigned (e.g., Form 16 for salaried)? | May need document rules engine |
| 10 | What happens to a filing if the assigned Executive is deactivated? | Needs business rule — auto-unassign or block deactivation? |
| 11 | Should clients receive notification when their Executive is changed? | Affects notification event matrix |
| 12 | Is concurrent access to the same filing by Executive and Partner expected? | Affects optimistic locking strategy |

---

## FastAPI Module → Table Mapping

| FastAPI Module | Primary Tables |
|----------------|----------------|
| `app.modules.auth` | `users` (read for JWT validation) |
| `app.modules.users` | `users`, `client_profiles` |
| `app.modules.clients` | `users`, `client_profiles`, `executive_client_assignments` |
| `app.modules.executives` | `users`, `executive_client_assignments` |
| `app.modules.onboarding` | `onboarding_form_fields`, `client_profiles` |
| `app.modules.filings` | `itr_filings`, `filing_state_history` |
| `app.modules.documents` | `master_document_types`, `filing_documents`, `stored_files` |
| `app.modules.computations` | `filing_computations`, `stored_files` |
| `app.modules.completed` | `filing_completed_docs`, `stored_files` |
| `app.modules.notifications` | `notifications` |
| `app.modules.audit` | `audit_logs` |
| `app.modules.storage` | `stored_files` (MinIO pre-signed URL generation) |

---

## Entity-Relationship Diagram (Mermaid)

```mermaid
erDiagram
    users ||--o| client_profiles : "has profile"
    users ||--o{ executive_client_assignments : "executive assigns"
    users ||--o{ executive_client_assignments : "client assigned to"
    users ||--o{ itr_filings : "client files"
    users ||--o{ notifications : "receives"
    users ||--o{ audit_logs : "performs action"
    users ||--o| stored_files : "PAN document"

    itr_filings ||--o{ filing_documents : "has placeholders"
    itr_filings ||--o{ filing_computations : "has computations"
    itr_filings ||--o{ filing_completed_docs : "has final docs"
    itr_filings ||--o{ filing_state_history : "state transitions"
    itr_filings ||--o{ notifications : "related to"

    master_document_types ||--o{ filing_documents : "type of"

    filing_documents ||--o| stored_files : "uploaded file"
    filing_computations ||--|| stored_files : "computation file"
    filing_completed_docs ||--|| stored_files : "completed file"

    users {
        uuid id PK
        varchar email UK
        varchar full_name
        user_role role
        account_status account_status
        uuid pan_document_id FK
        boolean is_active
    }

    client_profiles {
        uuid id PK
        uuid user_id FK_UK
        varchar pan_number
        jsonb form_data
    }

    itr_filings {
        uuid id PK
        uuid client_id FK
        varchar financial_year
        filing_status status
        uuid assigned_executive_id FK
    }

    filing_documents {
        uuid id PK
        uuid filing_id FK
        uuid document_type_id FK
        document_status status
        uuid file_id FK
    }

    filing_computations {
        uuid id PK
        uuid filing_id FK
        integer version
        uuid file_id FK
        computation_status status
    }

    filing_completed_docs {
        uuid id PK
        uuid filing_id FK
        completed_doc_type doc_type
        uuid file_id FK
    }

    master_document_types {
        uuid id PK
        varchar name UK
        boolean is_active
    }

    notifications {
        uuid id PK
        uuid user_id FK
        varchar title
        boolean is_read
    }

    audit_logs {
        uuid id PK
        audit_event_type event_type
        uuid actor_id FK
        uuid client_id FK
        timestamptz created_at
    }

    stored_files {
        uuid id PK
        varchar bucket
        text object_key
        bigint file_size_bytes
    }
```
