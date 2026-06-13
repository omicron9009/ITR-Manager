# Project Quotation — ITR Filing Platform

**Prepared by:** Aditya Jadon
**Prepared on:** 13 June 2026
**For:** ITR Manager — Client (`workpartners.co.in`)
**Engagement window:** 01 May 2026 → 13 June 2026 (~6.3 weeks)
**Engagement type:** Fixed-price, deliverable-based
**Scope:** Backend + DevOps / Cloud Deployment + Front-End (complete platform)

---

## 1. Project Overview

A custom multi-role SaaS platform for Income Tax Return filing has been
designed and delivered end-to-end during the engagement window. This document
sets out the **scope of work delivered** and the **fixed-price quotation** for
the complete platform.

The system covers:

- Five distinct user roles — Partner, Manager, Executive, Client, Dashboard-user
- A stateful filing lifecycle with auditable stage transitions
- Multi-level approval flows for computations and completed documents
- Dynamic onboarding and a drag-and-drop form builder
- Document lifecycle: upload, validation, approval, secure storage
- Text-field placeholder system tied to filings
- Role-aware action items and notifications
- Async email pipeline and document generation (engagement letter, declaration)
- Reports, analytics and dashboards
- Production-grade containerised deployment with metrics, logs, ingress, SSL and tunnelling
- Next.js 14 multi-portal front-end (50 routes, 5 portals)

---

## 2. Delivery Evidence (Proof of Work)

These figures are pulled directly from the project repository and document
what has actually shipped.

| Metric | Backend + DevOps | Front-End | Total |
|---|---:|---:|---:|
| Calendar duration | 44 days | 44 days | 44 days |
| Active commit days | 30 | 25 | — |
| Git commits | 86 | ~88 | **~174** |
| Lines of code shipped | ~17,011 (Python) | ~17,000 (TS/TSX/JS) | **~34,000** |
| Source files | — | 148 TS/TSX/JS | — |
| API modules | 19 | — | 19 |
| Service modules | 17 | — | 17 |
| SQLAlchemy models | 30 | — | 30 |
| Alembic migrations | 4 | — | 4 |
| Page routes | — | 50 | 50 |
| Reusable components | — | 40+ shadcn/ui + 8 shared | 48+ |
| Production containers | 11 | (frontend included) | 11 |

---

## 3. Technology Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, Python 3.12 |
| Database | PostgreSQL 16 (healthchecked, persistent volume) |
| Object storage | MinIO (presigned URLs, custom domain) |
| Cache | Redis 7 (LRU, memory-capped) |
| Front-end | Next.js 14 App Router, TypeScript, Tailwind CSS, shadcn/ui |
| Auth | Local JWT (bcrypt + jose), recovery codes |
| Ingress | Traefik v2.11 (rate-limit, CORS, IP allowlist) |
| Public exposure | Cloudflare Tunnel |
| Observability | Prometheus + Loki + Promtail + Grafana |
| Packaging | Docker + Docker Compose (11 services across 2 networks) |

---

## 4. Delivery Timeline

| Phase | Dates | Key deliverables |
|---|---|---|
| Bootstrap & auth | 01–05 May | Repo init, FastAPI + Next.js scaffolding, JWT, RBAC, login/register |
| Containerisation | 06–08 May | Per-service containers, MinIO networking, persistent volumes |
| Core domain | 11–17 May | Filing state machine, executive flow, document checklist, base portals |
| Manager role + tags | 19–24 May | Manager-Executive assignments, tag system, Manager portal |
| Recovery + async email | 25–29 May | Recovery codes, async email pipeline, branded templates |
| Approval flows | 30 May–04 Jun | Multi-tier computation approval, completed-doc approval, approval UIs |
| Action items + multi-doc | 05–08 Jun | Dynamic action-item engine, multi-document upload, dashboards |
| Hardening | 08–12 Jun | Encryption, vulnerability fixes, performance and caching, CORS |
| Stabilisation & deploy | 12–13 Jun | Approval flow polish, report download fix, full production deployment |

---

## 5. Scope Delivered — Module by Module

### 5.1 Backend

| # | Module | Highlights |
|---|---|---|
| 1 | Identity, Auth & RBAC | JWT auth, 5-role hierarchy, scoped permission helpers, recovery codes, account-activation gating |
| 2 | Client Onboarding & Profile | Dynamic onboarding form (TEXT/NUMBER/DATE/DROPDOWN/FILE), MinIO file uploads, JSONB profile, income-head confirmation flow |
| 3 | Filing Lifecycle | Full state machine (INITIATED → DOCUMENT_UPLOAD → PROCESSING → COMPUTATION → FILING → PAYMENT → COMPLETED + HALTED), state-history audit, 1,180-LOC filings API |
| 4 | Document Management | MasterDocumentType + income-head mapping, per-filing placeholders, presigned upload pattern, multi-doc flow, MIME/size validation, internal working docs |
| 5 | Computation Approval Pipeline | Three-tier state machine (Manager → Partner → Client), Partner bypass, rejection branches, versioning + SUPERSEDED, 937-LOC service logic |
| 6 | Completed Documents (Post-Filing) | Two-level approval with bypass, six doc types incl. ITR_JSON, auto FILING → PAYMENT transition, client visibility gating |
| 7 | Text Field Placeholder System | MasterTextFieldType + per-filing FilingTextField, FILLED/APPROVED/REJECTED workflow, income-head mapping, role-aware delete rules |
| 8 | Manager Role & Tag System | Manager + ManagerExecutiveAssignment, team scoping, MANAGER/LOCATION tags, 466-LOC managers API |
| 9 | Action Items, Notifications & Audit | 710-LOC dynamic action-item engine, in-app notifications, full audit log of state changes/approvals/rejections |
| 10 | Email & Document Generation | Async email pipeline (queue + retries), branded HTML templates, engagement letter, declaration |
| 11 | Reports, Dashboard & Feedback | 1,173-LOC role-aware analytics, 678-LOC report-generation service, filing feedback module |
| 12 | Cross-Cutting Hardening | Redis caching, N+1/eager-load fixes, sensitive-field encryption, CORS / MinIO / docs vulnerability fixes, custom exception hierarchy, migration discipline |

### 5.2 DevOps, Cloud & Deployment

| # | Module | Highlights |
|---|---|---|
| 1 | Backend Dockerfile + start.sh + supervisord | Multi-stage image, signal handling |
| 2 | Docker Compose stack | 11 services across 2 networks |
| 3 | Traefik v2.11 routing | Host rules, path prefixes, IP allowlist, rate-limit middlewares, CORS for MinIO |
| 4 | Cloudflare Tunnel | Zero-port public exposure |
| 5 | MinIO production setup | Buckets, presigned URLs, custom domain (`storage.workpartners.co.in`) |
| 6 | PostgreSQL 16 | Healthchecks + persistent volumes |
| 7 | Redis 7 | LRU policy, memory caps |
| 8 | Prometheus | Scraping + 15-day retention |
| 9 | Loki + Promtail | Centralised log aggregation from all containers |
| 10 | Grafana | Provisioned datasources/dashboards |
| 11 | Resource limits, restarts, healthchecks | Across the full stack |
| 12 | Production deployment | Domain wiring, SSL, smoke testing on `workpartners.co.in` |

### 5.3 Front-End (Next.js 14, 50 routes)

| # | Module | Pages | Highlights |
|---|---|---:|---|
| 1 | Project foundation | — | Next.js 14 App Router, TypeScript, Tailwind, shadcn/ui, Docker, middleware, `[[...path]]` API proxy |
| 2 | Landing site | — | Hero, Features, HowItWorks, RoleCards, CTA, Splash, Navbar, Footer, sitemap, privacy |
| 3 | Auth flow | — | Login, Register, Reset Password, Recovery Codes UI, AuthHeader, session/middleware guards |
| 4 | Shared component library | — | AppShell, NotificationBell, FileViewer, FilingProgressBar, EmptyState, StatusBadge, GlobalFooter |
| 5 | API & auth integration | — | `lib/api.ts`, `lib/auth.ts`, error handling, token refresh, proxy |
| 6 | Client Portal | 6 | Dashboard, documents, filings list & detail, notifications, onboarding wizard, profile |
| 7 | Executive Portal | 6 | Dashboard, clients list & detail, action items, notifications, profile |
| 8 | Manager Portal | 8 | Dashboard, clients (+detail), executives, document-types, form-builder consumer, action items, notifications, profile |
| 9 | Partner Portal | 15 | Dashboard, clients (+detail), managers, executives + tags, document-types, form-builder, email-config, recovery-codes, feedback, audit log, action items, notifications, profile |
| 10 | Summary Portal | 2 | Summary dashboard + leaderboard |
| 11 | Dynamic Form Builder | — | Drag-and-drop schema builder shared by Manager & Partner |
| 12 | Notification system | — | Bell dropdown, real-time updates, per-role notification pages |
| 13 | Responsive / accessibility / theming polish | — | Across all 50 routes |
| 14 | Bug fixes, integration testing & handover | — | Cross-cutting QA |

---

## 6. Quotation

### 6.1 Module-wise pricing (INR)

#### Backend

| Module | Amount (₹) |
|---|---:|
| Identity, Auth & RBAC | 18,000 |
| Onboarding & Form Builder (backend) | 14,000 |
| Filing Lifecycle State Machine | 16,000 |
| Document Management + MinIO | 18,000 |
| Computation Approval Pipeline | 18,000 |
| Completed Documents Approval | 10,000 |
| Text Field Placeholder System | 10,000 |
| Manager Role + Tag System | 12,000 |
| Action Items + Notifications + Audit | 12,000 |
| Email Pipeline + Engagement Letter + Declaration | 10,000 |
| Reports + Dashboard Analytics + Feedback | 12,000 |
| Hardening (caching, performance, encryption, security) | 10,000 |
| **Backend subtotal** | **₹1,60,000** |

#### DevOps & Cloud

| Module | Amount (₹) |
|---|---:|
| Docker / Traefik / Cloudflare Tunnel / MinIO / PG / Redis production setup | 18,000 |
| Observability (Prometheus + Loki + Promtail + Grafana) | 10,000 |
| Production deployment, domain wiring, SSL, smoke testing | 7,000 |
| **DevOps & Cloud subtotal** | **₹35,000** |

#### Front-End

| Module | Amount (₹) |
|---|---:|
| Project foundation (Next.js 14, TS, Tailwind, shadcn/ui, Docker, proxy) | 8,000 |
| Landing site (Hero/Features/Roles/CTA/Splash/Nav/Footer/sitemap/privacy) | 9,000 |
| Auth flow (login/register/reset/recovery-codes/guards) | 9,000 |
| Shared component library | 7,000 |
| API & auth integration layer | 8,000 |
| Client Portal (6 pages) | 9,000 |
| Executive Portal (6 pages) | 8,000 |
| Manager Portal (8 pages) | 9,000 |
| Partner Portal (15 pages — largest) | 14,000 |
| Summary Portal (2 pages) | 5,000 |
| Dynamic Form Builder (drag-and-drop, shared) | 9,000 |
| Notification system (bell, real-time, per-role pages) | 5,000 |
| Responsive / accessibility / theming polish across 50 routes | 5,000 |
| Bug fixes, integration testing & handover | 5,000 |
| **Front-End subtotal** | **₹1,10,000** |

#### Architecture & advisory

| Item | Amount (₹) |
|---|---:|
| Architecture, BRD analysis, schema design, ongoing client iteration | 15,000 |

### 6.2 Project total

| Component | Amount (₹) |
|---|---:|
| Backend | 1,60,000 |
| DevOps & Cloud | 35,000 |
| Front-End | 1,10,000 |
| Architecture & advisory | 15,000 |
| **Project total (fixed-price)** | **₹3,20,000** |

---

## 7. Package Options

The full delivered scope can be packaged in three ways. Pricing is **fixed and
inclusive** of all modules listed above; the difference between packages is
the **post-delivery support window** included.

| Package | What's included | Price (₹) |
|---|---|---:|
| **Essential** | Full delivered scope, source handover, deployment assets, **15-day** post-delivery bug-fix support | **₹2,50,000** |
| **Standard (recommended)** | Everything in Essential + **30-day** support window + 1 round of post-launch refinements + documentation walkthrough | **₹3,20,000** |
| **Extended** | Everything in Standard + **60-day** support window + monitoring tuning + 2 rounds of refinements | **₹3,75,000** |

> **Recommended package: Standard — ₹3,20,000.**
> Pricing is **negotiable** within reasonable limits based on the support window and refinement rounds the client actually needs.

---

## 8. Why This Pricing Is Fair

The pricing is anchored to **delivered output**, not hours. The numbers
above reflect:

- **~34,000 lines of production code** across backend + front-end
- **~174 commits** across the engagement window
- **30 SQLAlchemy models, 19 API modules, 17 services, 4 migrations**
- **50 front-end routes across 5 role portals**
- An **11-container production stack** with full observability, ingress, SSL and zero-port public exposure
- **End-to-end ownership** — design, build, deploy, hand over — by a single accountable engineer

Modern AI-assisted development (Claude) was used to accelerate scaffolding
and boilerplate. The savings from that acceleration are already passed on
in this quotation, which is why the price is well below what a comparable
custom multi-role SaaS platform would command at standard agency rates.

---

## 9. What's Included

- Full backend source code (FastAPI, SQLAlchemy, Alembic migrations)
- Full front-end source code (Next.js 14 App Router, TypeScript, Tailwind, shadcn/ui)
- Dockerfiles + `entrypoint.sh` + `start.sh` + `supervisord.conf`
- Docker Compose stack (Postgres / MinIO / Redis / Traefik / Cloudflare Tunnel / Prometheus / Loki / Promtail / Grafana / backend / frontend)
- Tailwind theme + customised shadcn/ui component library
- Mobile-responsive design across all 50 routes
- Privacy policy, sitemap and SEO basics
- Handover docs: `README.md`, `database_schema.md`, `ITR_Filing_Platform_BRD.md`
- Transferable rights to the delivered codebase
- Post-delivery support per the chosen package above

---

## 10. Out of Scope (priced separately)

- Mobile native apps (iOS / Android)
- ITR computation rules engine / direct tax-engine logic
- Third-party tax-portal integration (e.g. Income Tax Dept e-filing API)
- Penetration testing & formal security audit
- SLA support beyond the included window
- Long-term hosting & infra running costs (cloud bills, domains, certificates)
- Content writing, copywriting, brand assets
- New feature requests after sign-off

---

## 11. Payment Terms

| Milestone | % | Amount (₹) (Standard package) |
|---|---:|---:|
| Mobilisation / kickoff | 30% | 96,000 |
| Mid-delivery (core modules + portals live in staging) | 30% | 96,000 |
| Production deployment & handover | 30% | 96,000 |
| Close-out after support window | 10% | 32,000 |
| **Total** | **100%** | **₹3,20,000** |

- Payments via UPI / NEFT / IMPS to the registered account
- Invoices raised against this quotation
- GST extra at applicable rates if claimed under client's GSTIN
- For an already-delivered scope, the schedule can be re-mapped to a single
  on-handover invoice net of any advances already received

---

## 12. Assumptions

1. Source code, migrations, and deployment configs are handed over as-is in the current Git repository.
2. Cloud infrastructure (Postgres data dir, MinIO data dir, Grafana storage on `F:\PlatformData`) is owned and operated by the client; this quote does **not** include monthly infra costs.
3. Client supplies the Cloudflare account, the domain (`workpartners.co.in`) and the `CLOUDFLARE_TUNNEL_TOKEN`.
4. Email SMTP credentials, MinIO keys and JWT secrets are provisioned by the client.
5. Post-delivery support is bug-fix only; new features are quoted separately.
6. GST (if applicable) is charged extra under the client's GSTIN.

---

## 13. Validity

This quotation is valid for **30 days** from the date of issue. Pricing is
**negotiable in good faith** based on package selection and final scope
confirmation.

---

**Bottom-line ask: ₹3,20,000 (Standard package) for the complete ITR Filing Platform — Backend + DevOps/Cloud + Front-End — as delivered between 01 May 2026 and 13 June 2026.**

Range: **₹2,50,000 – ₹3,75,000** depending on the chosen support package.

---

**Prepared:** 13 June 2026
**By:** Aditya Jadon
**For:** ITR Manager — Client (`workpartners.co.in`)
**Engagement type:** Fixed-price, deliverable-based
