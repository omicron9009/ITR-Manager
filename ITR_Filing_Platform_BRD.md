# Business Requirements Document
## ITR Filing Management Platform

**Document Version:** 1.1  
**Prepared For:** Internal Development — CA Practice Management  
**Date:** May 2026  
**Status:** Draft — Pending Stakeholder Approval

---

## 1. Executive Summary

This document defines the business requirements for an in-house ITR (Income Tax Return) Filing Management Platform designed to manage and track the end-to-end ITR filing lifecycle for a CA/Tax practice. The platform serves three stakeholder roles — a Partner (CA / practice owner with full administrative rights), Executives (staff accounts created by the Partner, each scoped to their assigned clients), and registered Clients — each with a dedicated interface and role-based access. The system handles client onboarding, document collection and verification, computation approval, ITR filing tracking, and payment acknowledgement, with real-time status visibility for all parties.

The platform will be deployed as a containerised stack on an in-house server using Docker Compose, with Authentik handling authentication and reverse proxying, FastAPI as the backend, PostgreSQL as the primary database, MinIO for document storage, Next.js (TypeScript) as the frontend, and Google Gmail API for email notifications.

---

## 2. Scope

### 2.1 In Scope

- Client self-registration with PAN upload; account activation requires manual Partner verification before the client can proceed
- Partner-defined onboarding form (single, editable form applied to all clients)
- ITR filing initiation per financial year — **multiple financial year filings are supported per client** (e.g., FY 2023-24 and FY 2024-25 can both exist); only one filing per financial year per client is permitted
- Document placeholder management — Executive / Partner selects required documents from an editable master list; placeholders auto-created in the client's financial year directory
- Multi-round document submission and verification loop
- Computation document upload and client approval workflow
- ITR filing and payment state management
- Role-based dashboards (Client, Executive, and Partner)
- Email and in-app (bell icon) notifications
- Partner audit log (HTML, generated on request)
- Docker-based deployment on in-house server

### 2.2 Out of Scope

- Payment gateway integration (payments tracked manually) *(targeted for v2 — see Future Scope)*
- Automated reminder emails *(targeted for v2)*
- Additional role tiers beyond Partner and Executive *(targeted for v2)*
- Event-driven / message-queue architecture
- Tax computation engine (Executive / Partner uploads computation PDF manually)
- GST or any filing type other than ITR
- KYC / Aadhaar API integration *(targeted for v2 — PAN upload + manual Partner verification used in v1.0)*

---

## 3. Stakeholders

| Stakeholder | Role | Access Level |
|---|---|---|
| Partner | CA / practice owner | Full — all client data, all states, Executive management, document management, audit logs, client account activation |
| Executive | CA staff / assigned team member | Scoped — only clients assigned to them by the Partner; all filing actions on assigned clients |
| Client | Individual taxpayer | Restricted — own data, own filings, own documents only |

---

## 4. User Roles & Authentication

### 4.1 Authentication Stack

Authentication is managed entirely by **Authentik**, which also acts as the reverse proxy. Authentik enforces session management, OAuth2/OIDC token issuance, and access control for all three roles. FastAPI backend validates tokens via Authentik's JWKS endpoint.

### 4.2 Role Definitions

**Partner**
- Single account; the highest privilege role in the system.
- Full access to all client records, all financial year directories, all filing states, the document master list, onboarding form builder, and audit log generation.
- Creates and manages Executive accounts (create, deactivate).
- Assigns Executives to clients.
- **Manually verifies and activates client accounts** after reviewing the PAN upload submitted at registration.
- Can halt/block any client's filing progression at any stage.
- Receives all system email notifications (new registrations awaiting verification, new initiations, document submissions, computation approvals).

**Executive**
- Account created by the Partner; role assigned at creation.
- Scoped access: can only view and act on clients explicitly assigned to them by the Partner.
- Has full filing action permissions on assigned clients — assign document placeholders, review/approve documents, upload computation, mark filing states, mark payment received, upload invoice.
- Cannot access clients not assigned to them.
- Cannot access the audit log, form builder, Master Document List editor, or Executive management screens.
- Receives email and in-app notifications for events on their assigned clients only.

**Client**
- Self-registers on the platform by providing basic credentials and uploading their PAN card document.
- Account is placed in **Pending Verification** state immediately after registration.
- **Cannot proceed** with any filing activity until the Partner manually activates the account.
- Once activated, can immediately initiate ITR filing for any financial year.
- Can have multiple filings across different financial years simultaneously; only one filing per financial year is permitted.
- Access is restricted to their own profile, their own filings, and their own documents.
- The Partner can halt the filing process at any point if required (e.g., suspicious submission, incomplete KYC).

### 4.3 Client Self-Registration Flow

1. Client visits the platform and clicks **Register**.
2. Provides basic credentials: full name, email, password.
3. **Uploads PAN card document** (PDF / JPG / PNG).
4. Account is created with status **PENDING_VERIFICATION**. Client sees a "Your account is under verification" message on their dashboard.
5. Partner receives an email notification and in-app notification: "New client registration pending verification — [Client Name]."
6. Partner reviews the PAN document and either:
   - **Activates the account** → Client receives email: "Your account has been verified. You may now initiate your ITR filing." Client dashboard unlocks.
   - **Rejects the registration** → Client receives email with a reason; account is not activated.
7. Once activated, the client can immediately proceed to initiate an ITR filing.

> **Note:** KYC / Aadhaar API integration is out of scope for v1.0. PAN upload with manual Partner verification serves as the access gate to prevent random account creation. Formal KYC integration is planned for v2.

---

## 5. Onboarding Form

### 5.1 Form Structure and Management

- There is **one global onboarding form** in the system. It is created and maintained by the Partner.
- The Partner uses a **Form Builder** within the Partner Dashboard to add, edit, reorder, or remove fields.
- Supported field types: text, number, date, dropdown (single-select), file reference (for PAN / Aadhaar capture at form level if needed).
- Typical fields expected (Partner-configurable): Full Name, PAN Number, Aadhaar Number, Date of Birth, Contact Number, Address, Income Type (Salaried / Business / Both / Other), Bank Account Details.

### 5.2 Form Submission Behaviour

- A client fills and submits the onboarding form **once**, at the time of their **first ITR filing initiation**.
- Submission saves the data to the client's profile.
- On subsequent filings (future or additional financial years), the stored profile is pre-loaded; the client may edit it before confirming.
- Form submission triggers:
  - **Partner receives an email notification** of the new initiation and onboarding form submission.
  - **Assigned Executive (if already assigned) receives the same email notification.**
  - **Client receives an onboarding confirmation email.**
  - Client's record is added to the Partner Dashboard and assigned Executive's Dashboard under the **Initiated** state.

---

## 6. Document Management

### 6.1 Master Document List

- The Partner maintains a **Master Document List** — an editable list of all possible document types (e.g., Form 16, Bank Statement, Capital Gains Statement, etc.).
- The Partner can add, rename, or remove entries from this list at any time.
- This list is the source from which document placeholders are created per client per financial year.

### 6.2 Document Placeholder Assignment

- After a client submits the onboarding form, the assigned Executive (or Partner) selects the relevant documents from the Master Document List for that client's current filing.
- Selected documents are instantiated as **placeholders** inside the client's `ITR-<Financial Year>` directory (e.g., `ITR-FY2024-25`).
- Placeholder states: **Pending Upload** (default), **Uploaded** (client submitted), **Rejected** (Executive / Partner marked incorrect — rendered as RED placeholder), **Approved** (Executive / Partner verified).

### 6.3 Supported File Formats and Size Limits

- Accepted formats: PDF, JPG, PNG, XLS, XLSX, CSV, DOC, DOCX and all standard legal file types.
- Maximum upload size: **1 GB per file**.
- Storage backend: **MinIO** (object storage).

### 6.4 Directory Structure

Each client has a top-level directory. Inside, one sub-directory **per financial year**. Multiple financial year directories can coexist simultaneously for the same client.

```
Client: Aditya Joshi
├── ITR-FY2023-24/                         ← completed prior year filing
│   ├── Documents Required/
│   ├── Computation/
│   └── Filed Documents/                   ← unlocked (COMPLETED)
│
└── ITR-FY2024-25/                         ← active filing
    ├── Documents Required/                ← placeholders assigned by Executive / Partner
    │   ├── Form-16 [PENDING]
    │   ├── Bank-Statement [UPLOADED]
    │   └── Capital-Gains [REJECTED - RED]
    ├── Computation/                       ← Executive / Partner uploads computation PDF here
    └── Filed Documents/                   ← ITR acknowledgement + invoice (locked until COMPLETED)
```

The directory and `Documents Required` folder are **auto-created** when the client clicks **Initiate ITR Filing** for a given financial year. The document placeholders within it are populated once the assigned Executive (or Partner) assigns them.

**Rule:** A client cannot initiate a second filing for the same financial year if one already exists (active or completed). Filings across different financial years are unrestricted.

---

## 7. Complete Client Flow

The following is the authoritative end-to-end flow. Each state corresponds to a trackable status in both the client status bar and the Partner / Executive dashboard. This flow applies **per financial year per client**.

### STATE 0 — Registration & Verification

- Client self-registers, provides credentials and uploads PAN card document.
- Account placed in **PENDING_VERIFICATION** state.
- Partner notified (email + in-app) to review and activate the account.
- Client cannot initiate any filing until activated.
- Once Partner activates: client account moves to **ACTIVE**; client is notified.

### STATE 1 — Initiated

**Trigger:** Client (with an ACTIVE account) clicks **Initiate ITR Filing** → selects financial year → fills onboarding form (first time) or confirms pre-filled profile → submits.

**System Actions:**
- System checks: no existing filing for the selected financial year for this client. If one exists, initiation is blocked.
- `ITR-<Financial Year>` directory auto-created in MinIO.
- Filing record created in DB with status `INITIATED`.
- Partner receives email: "New ITR Filing Initiated — [Client Name], [FY]."
- Assigned Executive (if assigned) receives the same email.
- Client receives email: "Your ITR Filing has been initiated successfully."
- Partner Dashboard and assigned Executive's Dashboard: filing appears under **Initiated** list.

### STATE 2 — On Boarding

**Trigger:** Partner assigns an Executive to the client (if not already assigned). The assigned Executive (or Partner) reviews the initiation and assigns document placeholders from the Master Document List.

**System Actions:**
- Document placeholders created inside `ITR-<FY>/Documents Required/`.
- Filing status updated to `ON_BOARDING`.
- Client receives in-app notification and email: "Your document checklist is ready. Please upload the required documents."
- Client dashboard shows the financial year directory with placeholders.

### STATE 3 — Processing (Document Collection Loop)

**Trigger:** Client uploads all assigned documents and clicks **Submit Documents**.

**System Actions:**
- Filing status updated to `PROCESSING`.
- Partner receives in-app notification and email: "Documents submitted by [Client Name]."
- Assigned Executive receives in-app notification and email: "Documents submitted by [Client Name]."
- Assigned Executive (or Partner) reviews each document:
  - **All documents verified and correct** → approved → proceed to STATE 4.
  - **One or more documents incorrect or incomplete** → assigned Executive (or Partner) selects the specific documents that need re-upload → those placeholders are marked **REJECTED** (RED) → client receives in-app notification and email listing which documents need correction → client re-uploads and re-submits → loop repeats until the Executive / Partner approves all documents.

*This verification loop can repeat any number of times until the Executive / Partner approves the full document set.*

### STATE 4 — Computation

**Trigger:** Assigned Executive (or Partner) approves all submitted documents.

**System Actions:**
- Filing status updated to `COMPUTATION`.
- Assigned Executive (or Partner) uploads the computation PDF to `ITR-<FY>/Computation/`.
- Client receives in-app notification and email: "Your tax computation is ready for review."
- Client views the computation document on their dashboard.
  - **Client Approves Computation** → clicks **Approve** button on dashboard → Partner and assigned Executive receive in-app notification and email: "Computation approved by [Client Name]" → proceed to STATE 5.
  - **Client does not approve / needs clarification** → Client is advised to contact their assigned Executive or the Partner directly. Status remains `COMPUTATION`. The Executive / Partner can upload a revised computation document. Loop repeats until the client approves.

### STATE 5 — Filing

**Trigger:** Client approves the computation.

**System Actions:**
- Filing status updated to `FILING`.
- Assigned Executive (or Partner) files the ITR externally.
- Once filed, marks the filing as filed on the platform and uploads the ITR Acknowledgement document to `ITR-<FY>/Filed Documents/`.
- Client receives in-app notification and email: "Your ITR has been filed successfully."

### STATE 6 — Payment

**Trigger:** Assigned Executive (or Partner) marks filing as **Filed**.

**System Actions:**
- Filing status updated to `PAYMENT`.
- Client is notified (in-app + email): "Your ITR has been filed. Please complete payment with your CA."
- Payment is tracked manually — received offline.
- Assigned Executive (or Partner) clicks **Mark Payment Received** on the platform.

### STATE 7 — Completed

**Trigger:** Assigned Executive (or Partner) marks payment as received.

**System Actions:**
- Filing status updated to `COMPLETED`.
- Assigned Executive (or Partner) uploads the **Invoice** document.
- Invoice is sent to the client via email.
- Invoice and all filed return documents become **accessible** to the client inside `ITR-<FY>/Filed Documents/` (documents in this folder are locked / not visible to the client until the `COMPLETED` state is reached).
- Client receives in-app notification and email with invoice attached.
- **End of filing lifecycle for this financial year.**

---

## 8. Filing State Machine

```
[Registration]
PENDING_VERIFICATION  (Partner reviews PAN upload)
    │
    ▼
ACCOUNT ACTIVE  (Client can now initiate filings)
    │
    ▼ (Per financial year — multiple FYs can run in parallel)

INITIATED
    │
    ▼
ON_BOARDING  (Executive / Partner assigns document placeholders)
    │
    ▼
PROCESSING   ◄─────────────────────────────────────────┐
    │  (Executive / Partner reviews documents)          │
    ├── Documents Rejected? → Client re-uploads ────────┘
    │
    ▼
COMPUTATION  ◄─────────────────────────────────────────┐
    │  (Executive / Partner uploads computation)        │
    ├── Client does not approve? → Exec/Partner resends ┘
    │
    ▼
FILING       (Executive / Partner files ITR, uploads acknowledgement)
    │
    ▼
PAYMENT      (Executive / Partner marks payment received, uploads invoice)
    │
    ▼
COMPLETED    (Docs + invoice unlocked for client)
```

---

## 9. Partner & Executive Interface Requirements

### 9.1 Partner Dashboard

The Partner Dashboard is the top-level command surface with full visibility across all clients and all Executives. It displays:

**Account Activation Queue**
- List of client registrations in `PENDING_VERIFICATION` state, with the uploaded PAN document available for review. Partner can Activate or Reject each registration from this queue.

**Filing State Counters (with drill-down lists):**

| Counter | Description |
|---|---|
| Initiated | Total filings in `INITIATED` state — Number + list of client names with FY |
| On Boarding | Total in `ON_BOARDING` — Number + list |
| Processing | Total in `PROCESSING` — Number + list |
| Filing | Total in `FILING` — Number + list |
| Payment | Total in `PAYMENT` — Number + list |
| Completed | Total in `COMPLETED` — Number + list |

- Each counter card links to its respective filtered list.
- Each client name in the list links to that **Client's Admin View** (see §9.4).
- Partner can see all clients regardless of Executive assignment.

### 9.2 Executive Management (Partner Only)

- Partner can create Executive accounts (name, email, password).
- Partner can deactivate / reactivate Executive accounts.
- Partner can view a list of all Executives and the clients assigned to each.
- Partner assigns an Executive to a client from the client record (dropdown of active Executives).
- A client must have an assigned Executive before moving from `INITIATED` to `ON_BOARDING`.

### 9.3 Client List

- Searchable and filterable table of all registered clients (Partner sees all; Executive sees only assigned clients).
- Search by: Client Name, Financial Year.
- Columns: Client Name, Email, Account Status (Pending / Active), Assigned Executive, Active Filing FY(s), Current State, Last Updated.
- Clicking a row opens the **Client's Admin View**.

### 9.4 Client Admin View (per client)

Identical in structure to the Client Dashboard (§10.1) but accessible to the Partner (all clients) and the assigned Executive (their clients only). Contains:

- Client profile and onboarding data.
- PAN document (viewable by Partner).
- Account status and activation controls (Partner only).
- Status bar showing current filing state per financial year.
- Account details.
- Financial year directory with all documents (all active and past FY directories listed).
- Notifications log for this client.
- Action buttons appropriate to the current state (e.g., Assign Executive, Assign Documents, Approve Documents, Upload Computation, Mark Filed, Mark Payment Received).
- Link to this client's Document View.
- **Executive scope:** all action buttons are available to the assigned Executive. View is restricted to assigned clients only.

### 9.5 Executive Dashboard

The Executive Dashboard mirrors the Partner Dashboard in layout but is scoped exclusively to the Executive's assigned clients. It displays:

- Filing State Counters (same 6 counters) — filtered to assigned clients only.
- Client List — assigned clients only, searchable by name and financial year.
- Client Admin View — for assigned clients only; same action capabilities as Partner on those clients.
- No access to: Account Activation Queue, Executive Management, Form Builder, Master Document List editor, Audit Log.

### 9.6 Document Management View

- Partner: global document view across all clients.
- Executive: scoped document view — only their assigned clients.
- Filters: Client, Financial Year, Document Status (Pending / Uploaded / Rejected / Approved).
- Partner and Executive can select documents and change their status for accessible clients.
- Master Document List editor is available to the **Partner only**.

### 9.7 Form Builder (Partner Only)

- Partner can create and edit the single global onboarding form.
- Supports: add field, remove field, reorder fields, change field type.
- Changes to the form apply to new submissions only; existing submitted profiles are unaffected unless the client edits their profile.

### 9.8 Audit Log (Partner Only)

- Available to the Partner only. Executives do not have access.
- Generated as an HTML file on demand (via a "Generate Audit Log" button).
- Scope:
  - Document events: upload, download, rejection, approval.
  - Status change events: each state transition with timestamp and actor.
  - Account activation / rejection events.
  - Login events are excluded from scope.
- Output: downloadable HTML report, filterable by client and date range at generation time.

---

## 10. Client Interface Requirements

### 10.1 Client Dashboard

**Account Verification Banner**
Displayed when the account is in `PENDING_VERIFICATION` state. Shows a message: "Your account is under verification by our team. You will be notified once it is activated." All filing actions are disabled until the account is activated.

**Status Bar / Progress Tracker**
Visual progress bar mapping the current filing state across all 7 states. States are labelled and highlighted to show completed, current, and upcoming steps. Displayed per financial year — if multiple FY filings exist, each has its own status bar.

**Account Details Panel**
Displays: Name, Email, PAN (from onboarding form), Account Status, registration date.

**Notifications Feed (Bell Icon)**
In-app notification feed showing all system notifications in reverse chronological order. Unread count badge on the bell icon.

**Directory View**
Financial year directory structure. All FY folders are listed (active and historical). Each FY directory is expandable to show:
- Document placeholders with their current status (colour-coded: grey = pending, green = uploaded/approved, red = rejected).
- Computation document (visible once Executive / Partner uploads, in `COMPUTATION` state+).
- Filed Documents folder (content locked until `COMPLETED` state).

**Returns Tracking View**
A per-financial-year tracking view listing all past and active filings with their current / final state, useful for historical reference and multi-year tracking.

### 10.2 Client Actions by State

| State | Available Client Actions |
|---|---|
| PENDING_VERIFICATION | View verification status only |
| INITIATED | View status, edit profile |
| ON_BOARDING | View assigned document placeholders |
| PROCESSING | Upload documents to placeholders, Submit Documents |
| COMPUTATION | View computation PDF, Approve Computation |
| FILING | View status only |
| PAYMENT | View status only |
| COMPLETED | Download filed documents, Download invoice |

---

## 11. Notifications

All notifications are delivered via two channels simultaneously:

1. **Email** — via Google Gmail API (authenticated with service account / OAuth2 credentials).
2. **In-App** — Bell icon feed visible on the client, Executive, and Partner dashboards.

### 11.1 Notification Event Matrix

| Trigger Event | Recipient | Channel |
|---|---|---|
| Client registers (PAN uploaded, pending verification) | Partner | Email + In-App |
| Partner activates client account | Client (confirmation) | Email + In-App |
| Partner rejects client registration | Client | Email |
| Client initiates ITR filing | Partner + Assigned Executive | Email + In-App |
| Client submits onboarding form | Client (confirmation) | Email + In-App |
| Executive / Partner assigns document placeholders | Client | Email + In-App |
| Client submits documents | Partner + Assigned Executive | Email + In-App |
| Executive / Partner rejects one or more documents | Client | Email + In-App |
| Executive / Partner approves all documents | Client | Email + In-App |
| Executive / Partner uploads computation | Client | Email + In-App |
| Client approves computation | Partner + Assigned Executive | Email + In-App |
| Executive / Partner marks ITR as filed | Client | Email + In-App |
| Executive / Partner marks payment received | Client | Email + In-App |
| Invoice sent on completion | Client | Email + In-App |

Reminder emails are out of scope for v1.0 (future scope).

---

## 12. Technical Architecture

### 12.1 Technology Stack

| Layer | Technology | Role |
|---|---|---|
| Authentication & Reverse Proxy | Authentik | SSO, OAuth2/OIDC, session management, TLS termination, reverse proxy to backend and frontend |
| Backend API | FastAPI (Python) | REST API, business logic, state machine transitions, JWT validation |
| Frontend | Next.js (TypeScript) | Client, Executive, and Partner web interfaces; role-based routing |
| Primary Database | PostgreSQL | Relational data — users, filings, states, notifications, form definitions, document metadata |
| Object Storage | MinIO | Document and file storage; pre-signed URLs for uploads and downloads |
| Email Notification | Google Gmail API | Transactional email delivery |
| Containerisation | Docker + Docker Compose | All services containerised; deployed on in-house server |

### 12.2 Service Topology (Docker Compose)

```
[Browser / Client]
       │
       ▼
  [Authentik]  ← Reverse Proxy + Auth Gateway (Port 443)
  /           \
 ▼             ▼
[Next.js]   [FastAPI]
  (Frontend)  (Backend API)
                │         \
                ▼          ▼
          [PostgreSQL]   [MinIO]
```

All services run as Docker containers on a single in-house server. Authentik handles TLS and routes traffic to the Next.js frontend and FastAPI backend. MinIO runs as a separate object storage container. PostgreSQL is a standard containerised Postgres instance with a persistent volume.

---

## 13. Deployment Requirements

- All services deployed via **Docker Compose** on a single in-house server.
- **Authentik** acts as both the identity provider and the reverse proxy (Nginx functionality is covered by Authentik's embedded proxy).
- **TLS** is handled at the Authentik layer.
- **Persistent volumes** are required for: PostgreSQL data, MinIO data, Authentik data.
- **Environment configuration** via `.env` files per service (never committed to version control).
- Expected capacity: **1,000 – 2,000 clients**; PostgreSQL and MinIO sizing should be planned accordingly.
  - Estimated PostgreSQL DB size: ~5–20 GB (metadata, notifications, audit logs at scale).
  - Estimated MinIO storage: provisioned based on estimated 1 GB max file size × average documents per client × number of clients — recommend minimum **10 TB** raw storage for comfortable headroom.
- No event-driven or message-queue architecture; all operations are synchronous REST API calls.

---

## 14. Non-Functional Requirements

| Category | Requirement |
|---|---|
| Security | Role-based access enforced at API layer (FastAPI dependency); file access via pre-signed MinIO URLs only; no direct MinIO exposure; PAN documents stored securely in MinIO with restricted access (Partner only) |
| Availability | Best-effort on in-house server; no SLA defined for v1.0 |
| Performance | Dashboard and list views should load within 3 seconds for up to 2,000 clients |
| Scalability | Vertical scaling on the in-house server is the primary scaling path for v1.0 |
| Audit | All document and state-change events logged to `audit_log` table; Partner can generate an HTML audit report on demand |
| Data Retention | Documents and records are retained indefinitely; no automated purge policy in v1.0 |
| Browser Support | Latest 2 versions of Chrome, Firefox, Safari, Edge |

---

## 15. Assumptions and Constraints

1. There is one Partner account in the system. Multiple Executive accounts can be created by the Partner. Executive accounts are scoped to assigned clients only.
2. Clients can have **multiple active filings simultaneously, one per financial year**. Initiating a second filing for the same financial year is blocked at the UI and API level.
3. The Partner or assigned Executive is responsible for all external ITR filing activity; the platform tracks states but does not connect to the Income Tax portal.
4. No automated computation or tax calculation is performed by the platform.
5. Invoice generation is manual — the assigned Executive or Partner uploads the invoice document.
6. All email delivery depends on Google Gmail API availability and quota.
7. No mobile native app is in scope; the web application should be responsive.
8. The platform is internal and not intended for public internet access without VPN or network-level access controls beyond what Authentik provides.
9. PAN upload at registration is the v1.0 mechanism to gate account creation. Manual Partner verification replaces formal KYC in this version.

---

## 16. Open Items / Future Scope

| Item | Priority |
|---|---|
| KYC / Aadhaar API integration to automate identity verification | High (v2) |
| Payment gateway integration (e.g., Razorpay) | Medium (v2) |
| Automated document reminder emails to clients after X days of inaction | Low (v2) |
| Additional Executive sub-roles or permission granularity | Low (v2) |
| System-generated invoice (from template) | Medium (v2) |
| Income Tax portal API integration for direct filing status | High (v3) |
| Mobile app (React Native or PWA) | Low (v3) |
| Bulk client import / CSV onboarding | Low (v2) |

---

## 17. Approval

| Role | Name | Signature | Date |
|---|---|---|---|
| Product Owner / Partner | | | |
| Lead Developer | | | |
| Reviewer | | | |

*This document is pending stakeholder sign-off before development commences.*

