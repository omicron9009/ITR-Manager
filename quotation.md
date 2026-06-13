# Project Quotation — ITR Filing Platform (Full-Stack)

**Prepared by:** Aditya Jadon
**Prepared on:** 13 June 2026
**For:** ITR Manager — Client (workpartners.co.in)
**Engagement window:** 01 May 2026 → 13 June 2026 (~6.3 weeks)
**Engagement type:** Fixed-price, deliverable-based
**Scope of this quotation:** Backend + DevOps/Cloud + Front-End (complete platform)

---

## 1. Executive Summary

A custom, multi-role SaaS platform was designed and delivered end-to-end for an
Income Tax Return (ITR) filing workflow. The system supports five distinct user
roles (Partner, Manager, Executive, Client, Dashboard-user), a stateful filing
pipeline with multi-level approval gates, document/text-field/computation
lifecycle management, dynamic onboarding, audit logging, in-app + email
notifications, analytics, and a production-grade containerised deployment
(metrics, logs, ingress, SSL, Cloudflare Tunnel) — paired with a polished
Next.js 14 multi-portal front-end.

### Combined delivery metrics

| Metric | Backend + DevOps | Front-End | Total |
|---|---:|---:|---:|
| Calendar duration | 44 days | 44 days | **44 days (1 May → 13 Jun)** |
| Active commit days | 30 | 25 | — |
| Git commits | 86 | ~88 | **~174** |
| Lines of code shipped | ~17,011 (Python) | ~17,000 (TS/TSX/JS) | **~34,000** |
| Modules / route-files | 19 API modules + 17 services | 50 page routes + 48 components | — |
| Models / data shapes | 30 SQLAlchemy models | — | — |
| Migrations | 4 Alembic | — | — |
| Containers in production | 11 (frontend container included) | — | — |

---

## 2. Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, Python 3.12 |
| Database | PostgreSQL 16 (healthchecked, persistent volume) |
| Object storage | MinIO (presigned URLs, custom domain) |
| Cache | Redis 7 (LRU, memory-capped) |
| Front-end | Next.js 14 (App Router), TypeScript, Tailwind CSS, shadcn/ui |
| Auth | Local JWT (bcrypt + jose), recovery codes |
| Ingress | Traefik v2.11 (rate-limit, CORS, IP allowlist) |
| Tunnelling | Cloudflare Tunnel (zero-port public exposure) |
| Observability | Prometheus + Loki + Promtail + Grafana |
| Packaging | Docker + Docker Compose (11 services, 2 networks) |

---

## 3. Commit Timeline (high-level milestones)

| Phase | Approx. dates | Highlights |
|---|---|---|
| Bootstrap & auth | 01–05 May | Repo init, FastAPI + Next.js scaffolding, JWT + RBAC, login/register |
| Containerisation | 06–08 May | Per-service containers, MinIO networking, Postgres volumes |
| Core domain | 11–17 May | Filing state machine, executive flow, document checklist, base portals |
| Manager role + tags | 19–24 May | Manager-Executive assignments, Tag system, location tags, Manager portal |
| Recovery + async email | 25–29 May | Recovery codes, async email pipeline, branded templates |
| Approval flows | 30 May–04 Jun | Two-step computation approval, completed doc approval, approval UIs |
| Action items + multi-doc | 05–08 Jun | Dynamic action-item engine, multi-document upload, dashboards |
| Hardening | 08–12 Jun | Encryption, vulnerability fixes, performance & caching, CORS |
| Stabilisation & deploy | 12–13 Jun | Approval flow polish, report download fix, full production deployment |

---

## 4. Feature-by-Feature Complexity Breakdown

Effort is measured in **Engineering Days (ED)**: 1 ED ≈ 1 senior developer day, including design, implementation, manual testing, debugging, and migration writing. AI-assistance (Claude) was used as a force multiplier; the items below represent *delivered* engineering value, not raw hours.

### 4.1 Backend — Identity, Auth & RBAC

| Feature | Complexity | ED |
|---|---|---:|
| JWT auth (bcrypt + jose), login/register, password reset | Medium | 2.5 |
| Role hierarchy: Partner / Manager / Executive / Client / Dashboard-user | High | 2.0 |
| `permissions.py` — `enforce_client_access`, `check_manager_*_access`, `get_current_*_or_partner` helpers | High | 1.5 |
| Recovery codes (generation, hashing, single-use, replenishment) | Medium | 1.0 |
| Account activation / Partner approval gating | Medium | 1.0 |
| **Subtotal** | | **8.0** |

### 4.2 Backend — Client Onboarding & Profile

| Feature | Complexity | ED |
|---|---|---:|
| Dynamic onboarding form builder (`OnboardingFormField`: TEXT/NUMBER/DATE/DROPDOWN/FILE) | High | 2.5 |
| FILE-type upload to MinIO with structured key (`clients/{name}_{id}/onboarding/...`) | Medium | 1.0 |
| Client profile model + form-data JSONB + submission gating | Medium | 1.0 |
| Income-heads confirmation flow (`income_heads_snapshot`, BASE doc auto-resolution, auto-state transition) | High | 2.0 |
| **Subtotal** | | **6.5** |

### 4.3 Backend — Filing Lifecycle

| Feature | Complexity | ED |
|---|---|---:|
| Filing state machine: INITIATED → DOCUMENT_UPLOAD → PROCESSING → COMPUTATION → FILING → PAYMENT → COMPLETED + HALTED | High | 3.0 |
| `FilingStateHistory` audit trail per transition | Low–Medium | 0.5 |
| Auto-assignment of executive on filing creation | Medium | 0.5 |
| Filing endpoints (1,180 LOC) — list/filter/details/transitions | High | 2.5 |
| **Subtotal** | | **6.5** |

### 4.4 Backend — Document Management

| Feature | Complexity | ED |
|---|---|---:|
| `MasterDocumentType` + income-head M:N mapping (BASE/INCREMENTAL) | High | 2.0 |
| Per-filing `FilingDocument` placeholders, status flow PENDING → UPLOADED → APPROVED/REJECTED | Medium | 1.5 |
| MinIO presigned upload + confirm-upload pattern | Medium | 1.5 |
| Multi-document upload flow | Medium | 1.0 |
| File validation (`file_validation.py` — MIME, size, extension) | Medium | 1.0 |
| Internal Working Docs (separate top-level MinIO dir, no approval) | Medium | 1.5 |
| Other docs / supporting docs flow | Medium | 1.0 |
| **Subtotal** | | **9.5** |

### 4.5 Backend — Computation Approval Pipeline (the hardest part)

| Feature | Complexity | ED |
|---|---|---:|
| Three-tier approval state machine (Manager → Partner → Client) with Partner bypass and rejection branches | Very High | 3.0 |
| Versioning + SUPERSEDED state when re-uploaded | High | 1.0 |
| Rejection feedback + reasons + re-upload loop | High | 1.5 |
| 937-LOC `computations.py` API + service logic | High | 2.0 |
| Tax-paid gating before FILING transition | Medium | 0.5 |
| **Subtotal** | | **8.0** |

### 4.6 Backend — Completed Documents (Post-Filing)

| Feature | Complexity | ED |
|---|---|---:|
| Two-level approval (Manager → Partner) with bypass | High | 2.0 |
| Six doc types incl. ITR_JSON migration | Medium | 1.0 |
| Auto FILING → PAYMENT transition when all required docs PARTNER_APPROVED | High | 1.0 |
| Client visibility gating to PARTNER_APPROVED only | Medium | 0.5 |
| **Subtotal** | | **4.5** |

### 4.7 Backend — Text Field Placeholder System

| Feature | Complexity | ED |
|---|---|---:|
| `MasterTextFieldType` + per-filing `FilingTextField` parallel to doc checklist | High | 2.0 |
| Status flow PENDING → FILLED → APPROVED/REJECTED with revert-on-edit | Medium | 1.0 |
| Income-head mapping (BASE/INCREMENTAL) auto-assignment | Medium | 1.0 |
| Role-aware delete rules + at-least-one guard | High | 1.5 |
| **Subtotal** | | **5.5** |

### 4.8 Backend — Manager Role & Tag System

| Feature | Complexity | ED |
|---|---|---:|
| Manager model + `ManagerExecutiveAssignment` + reassignment logic | High | 2.0 |
| Manager team scoping across all queries | High | 1.5 |
| Tag system (MANAGER/LOCATION), multi-tag executives | Medium | 1.5 |
| `/managers/*` endpoints (466 LOC) | Medium | 1.5 |
| **Subtotal** | | **6.5** |

### 4.9 Backend — Action Items, Notifications, Audit

| Feature | Complexity | ED |
|---|---|---:|
| Dynamic action-item engine (710 LOC) — computed from filing/doc/computation state, role-scoped | Very High | 3.5 |
| In-app notifications + read tracking | Medium | 1.0 |
| Audit log: every state change, approval, rejection, system event | Medium | 1.5 |
| **Subtotal** | | **6.0** |

### 4.10 Backend — Email & Document Generation

| Feature | Complexity | ED |
|---|---|---:|
| Async email pipeline (queue + retries) | Medium | 1.5 |
| Branded HTML email templates | Medium | 1.0 |
| Engagement letter generation | Medium | 1.5 |
| Declaration generation | Medium | 1.0 |
| **Subtotal** | | **5.0** |

### 4.11 Backend — Reports, Dashboard, Feedback

| Feature | Complexity | ED |
|---|---|---:|
| Dashboard analytics (1,173 LOC) — role-aware aggregations | High | 2.5 |
| Report generation + download (678 LOC service) | High | 2.0 |
| Filing feedback module + migration | Medium | 1.0 |
| **Subtotal** | | **5.5** |

### 4.12 Backend — Cross-Cutting Hardening

| Feature | Complexity | ED |
|---|---|---:|
| Redis caching layer (`core/cache.py`) for hot reads | Medium | 1.5 |
| Performance pass (N+1 fixes, eager loads, indexes) | Medium | 1.5 |
| Encryption of sensitive fields | Medium | 1.0 |
| Vulnerability fixes (CORS, MinIO exposure, docs blocking) | Medium | 1.0 |
| Custom exception hierarchy + global error handler | Low | 0.5 |
| Alembic migration discipline (4 migrations) | Low | 0.5 |
| **Subtotal** | | **6.0** |

### 4.13 DevOps, Cloud & Deployment

| Feature | Complexity | ED |
|---|---|---:|
| Multi-stage backend Dockerfile + supervisord + start.sh | Medium | 1.0 |
| Docker Compose orchestration of 11 services across 2 networks | High | 2.0 |
| **Traefik v2.11** routing — host rules, path-prefixes, docs blocking via IP allowlist, rate-limit middlewares, CORS for MinIO | High | 2.0 |
| **Cloudflare Tunnel** integration (zero-port public exposure) | Medium | 1.0 |
| **MinIO** production setup — buckets, presigned URLs, custom domain (`storage.workpartners.co.in`) | Medium | 1.5 |
| **PostgreSQL 16** with healthchecks + persistent volumes on F:\ | Low | 0.5 |
| **Redis** with LRU policy + memory caps | Low | 0.5 |
| **Prometheus** scraping + 15-day retention | Medium | 1.0 |
| **Loki + Promtail** centralised log aggregation from all containers | Medium | 1.5 |
| **Grafana** with provisioned datasources/dashboards | Medium | 1.0 |
| Resource limits, restart policies, healthchecks across stack | Medium | 1.0 |
| Environment management (`.env`, secrets, `NEXT_PUBLIC_API_URL` wiring) | Low | 0.5 |
| Production deployment + smoke testing on `workpartners.co.in` | Medium | 1.5 |
| **Subtotal** | | **15.0** |

### 4.14 Front-End — Foundation, Landing & Auth

| Feature | Complexity | ED |
|---|---|---:|
| Next.js 14 App Router setup, TypeScript, Tailwind, shadcn/ui, Docker, `entrypoint.sh`, middleware, `[[...path]]` API proxy | Medium | 2.0 |
| Landing site — Hero, Features, HowItWorks, RoleCards, CTA Banner, Splash, Navbar, Footer, sitemap, privacy policy | Medium | 3.0 |
| Auth flow — Login, Register, Reset Password, Recovery Codes UI, AuthHeader, session/middleware guards | Medium–High | 3.0 |
| Shared component library — AppShell, NotificationBell, FileViewer, FilingProgressBar, EmptyState, StatusBadge, GlobalFooter | Medium | 2.0 |
| API & auth integration layer — `lib/api.ts`, `lib/auth.ts`, error handling, token refresh, `[[...path]]` proxy | Medium–High | 2.0 |
| **Subtotal** | | **12.0** |

### 4.15 Front-End — Role Portals

| Portal | Pages | Complexity | ED |
|---|---:|---|---:|
| Client Portal — dashboard, documents, filings list, filing detail, notifications, onboarding wizard, profile | 6 | High | 4.0 |
| Executive Portal — dashboard, clients list, client detail, action items, notifications, profile | 6 | Medium–High | 3.0 |
| Manager Portal — dashboard, clients (+detail), executives, document-types, form-builder consumer, action items, notifications, profile | 8 | High | 4.0 |
| Partner Portal (largest) — dashboard, clients (+detail), managers, executives + tags (location/manager/partner summaries), document-types, form-builder, email-config, recovery-codes, feedback, audit log, action items, notifications, profile | 15 | Very High | 6.0 |
| Summary Portal — summary dashboard + leaderboard | 2 | Medium | 1.5 |
| **Subtotal** | | | **18.5** |

### 4.16 Front-End — Cross-Cutting

| Feature | Complexity | ED |
|---|---|---:|
| Dynamic Form Builder — drag-and-drop schema builder shared by Manager & Partner | Very High | 3.0 |
| Notification system — bell dropdown, real-time updates, per-role notification pages | Medium–High | 1.5 |
| Responsive design, accessibility, theming, UX polish across 50 routes | Medium | 2.0 |
| Bug fixes, integration testing, deployment & handover | Medium | 2.0 |
| **Subtotal** | | **8.5** |

---

## 5. Effort Roll-Up

| Bucket | Engineering Days |
|---|---:|
| Backend — Identity, Auth & RBAC | 8.0 |
| Backend — Onboarding & Profile | 6.5 |
| Backend — Filing Lifecycle | 6.5 |
| Backend — Document Management | 9.5 |
| Backend — Computation Approval | 8.0 |
| Backend — Completed Documents | 4.5 |
| Backend — Text Field Placeholders | 5.5 |
| Backend — Manager & Tags | 6.5 |
| Backend — Action Items / Notifications / Audit | 6.0 |
| Backend — Email & Document Generation | 5.0 |
| Backend — Reports / Dashboard / Feedback | 5.5 |
| Backend — Cross-Cutting Hardening | 6.0 |
| DevOps, Cloud & Deployment | 15.0 |
| **Backend + DevOps subtotal** | **92.5** |
| Front-End — Foundation, Landing & Auth | 12.0 |
| Front-End — Role Portals | 18.5 |
| Front-End — Cross-Cutting | 8.5 |
| **Front-End subtotal** | **39.0** |
| Architecture, BRD analysis, schema design, client meetings, API contract iteration (≈ +20% of backend) | 18.5 |
| **Grand total — engineering value** | **~150 ED** |

This compresses into the 30-active-day calendar window because of:
- AI-assisted code generation (Claude) accelerating boilerplate by ~2–3×
- Long working days (10–14 hrs) during the build
- Single-developer ownership eliminating coordination overhead

The **delivered value**, however, is what is being quoted — not raw hours.

---

## 6. Pricing

Pricing reflects:
- a custom multi-role SaaS platform built ground-up (not a CRUD admin panel),
- production-grade observability + zero-trust ingress (Cloudflare Tunnel),
- domain complexity (Indian ITR workflows, multi-step approvals, compliance audit trail),
- 5 distinct role portals on the front-end with deep state interactions,
- single point of accountability,
- working source code + migrations + deployment scripts handed over.

### 6.1 Backend line-item pricing (INR)

| Module | Amount (₹) |
|---|---:|
| Identity, Auth & RBAC | 40,000 |
| Onboarding & Dynamic Form Builder (backend) | 35,000 |
| Filing Lifecycle State Machine | 40,000 |
| Document Management + MinIO integration | 55,000 |
| Computation Approval Pipeline (multi-tier) | 50,000 |
| Completed Documents Approval | 25,000 |
| Text Field Placeholder System | 30,000 |
| Manager Role + Tag System | 35,000 |
| Action Items + Notifications + Audit | 35,000 |
| Email Pipeline + Engagement Letter + Declaration | 30,000 |
| Reports + Dashboard Analytics + Feedback | 35,000 |
| Hardening (caching, perf, encryption, CORS, security fixes) | 30,000 |
| **Backend subtotal** | **₹4,40,000** |

### 6.2 DevOps & Cloud line-item pricing (INR)

| Module | Amount (₹) |
|---|---:|
| Docker / Traefik / Cloudflare Tunnel / MinIO / PG / Redis production setup | 55,000 |
| Observability (Prometheus + Loki + Promtail + Grafana, dashboards & provisioning) | 35,000 |
| Production deployment, domain wiring, SSL, smoke testing | 20,000 |
| **DevOps & Cloud subtotal** | **₹1,10,000** |

### 6.3 Front-End line-item pricing (INR)

| Module | Amount (₹) |
|---|---:|
| Project foundation (Next 14, TS, Tailwind, shadcn/ui, Docker, proxy) | 30,000 |
| Landing site (Hero/Features/HowItWorks/RoleCards/CTA/Splash/Nav/Footer/sitemap/privacy) | 40,000 |
| Auth flow (login, register, reset, recovery-codes, guards) | 45,000 |
| Shared component library (AppShell, NotificationBell, FileViewer, ProgressBar, EmptyState, StatusBadge, GlobalFooter) | 30,000 |
| API & auth integration layer (`lib/api.ts`, `lib/auth.ts`, proxy, token refresh) | 35,000 |
| Client Portal (6 pages) | 55,000 |
| Executive Portal (6 pages) | 40,000 |
| Manager Portal (8 pages) | 55,000 |
| Partner Portal (15 pages — largest) | 90,000 |
| Summary Portal (dashboard + leaderboard) | 20,000 |
| Dynamic Form Builder (drag-and-drop, shared) | 50,000 |
| Notification system (bell, real-time, per-role pages) | 22,000 |
| Responsive / accessibility / theming / UX polish across 50 routes | 25,000 |
| Bug fixes, integration testing, deployment & handover | 25,000 |
| **Front-End subtotal (gross)** | **₹5,62,000** |
| Goodwill / loyalty discount (~5%) | − 27,000 |
| **Front-End subtotal (net)** | **₹5,35,000** |

### 6.4 Architecture & advisory

| Item | Amount (₹) |
|---|---:|
| Architecture, BRD analysis, schema design, ongoing client iteration | 50,000 |

### 6.5 Grand total

| Component | Amount (₹) |
|---|---:|
| Backend | 4,40,000 |
| DevOps & Cloud | 1,10,000 |
| Front-End (after discount) | 5,35,000 |
| Architecture & advisory | 50,000 |
| **Project Grand Total** | **₹11,35,000** |

> Approx. **USD equivalent: ~$13,600** at ₹83.5/USD; final FX at invoice date.

### 6.6 Recommended quotation tiers (combined)

| Tier | What client gets | Price (₹) |
|---|---|---:|
| **Lean** | All deliverables as-shipped, source handed over, 15-day post-deploy bug-fix support | **9,75,000** |
| **Standard (recommended)** | Lean + 30-day support + 1 round of post-launch enhancements + documentation handoff | **11,35,000** |
| **Premium** | Standard + 60-day SLA support + monitoring tuning + 2 enhancement rounds + on-call deploy assistance | **13,50,000** |

> **Recommended ask: ₹11,35,000 (Standard tier).**

### 6.7 Justification vs market rates

- Indian market rate for senior FastAPI/Postgres engineers: **₹3,500–₹5,500 / ED**
- Indian market rate for senior Next.js engineers: **₹20,000–₹30,000 / day** (greenfield)
- 150 ED × ₹4,500 (mid blended) = **₹6,75,000** baseline engineering
- Deployment stack premium (observability + Cloudflare Tunnel) adds **₹50,000–₹1,00,000**
- Domain-specific complexity (ITR compliance, audit trail, 5-portal UX) adds **₹1,00,000+**
- 50 routes × ~1.5 days manual = 75 days × ₹20,000 = **₹15,00,000** front-end alone at standard rates
- AI-accelerated effective rate delivered to client: **~₹13,700/day** (front-end) — **substantially below market**
- Resulting fair-market combined ask: **₹11,35,000** ✅

---

## 7. Why This Is Fair Value

| If built without AI (manual greenfield) | This quote |
|---|---|
| Backend ~92.5 ED × ₹6,000 = ₹5,55,000 | ₹4,40,000 |
| DevOps ~15 ED × ₹6,000 = ₹90,000+ | ₹1,10,000 (full stack) |
| Front-end 75 days × ₹20,000 = ₹15,00,000 | ₹5,35,000 |
| **Total at standard rates: ~₹21,00,000+** | **₹11,35,000** |
| 14–16 week timeline | 6.3-week timeline |

The client receives a production-grade, multi-portal, role-based platform **at ~55% of standard market cost**, delivered in **less than half the time**.

---

## 8. Payment Schedule (suggested)

| Milestone | % | Amount (₹) |
|---|---:|---:|
| Mobilisation / kickoff | 25% | 2,83,750 |
| Core APIs + auth + filing lifecycle live in staging + base portals | 25% | 2,83,750 |
| Approval flows + reports + email + all role portals live | 25% | 2,83,750 |
| Production deploy + handover + 15-day stabilisation | 25% | 2,83,750 |
| **Total** | **100%** | **₹11,35,000** |

(Since the project is already delivered, this schedule can be re-mapped to a back-loaded invoice or netted against any advances already received in May.)

- Payment via UPI / NEFT / IMPS to registered account
- Invoices raised against this quotation
- GST extra at applicable rates if claimed under GSTIN

---

## 9. What's Included

- Full backend source code (FastAPI, SQLAlchemy, Alembic migrations)
- Full front-end source code (Next.js 14 App Router, TypeScript, Tailwind, shadcn/ui)
- Dockerfiles + `entrypoint.sh` + `start.sh` + `supervisord.conf` for all services
- Docker Compose stack covering Postgres / MinIO / Redis / Traefik / Cloudflare Tunnel / Prometheus / Loki / Promtail / Grafana / backend / frontend
- Tailwind theme + customised shadcn/ui component library
- Mobile-responsive design across all 50 routes
- Privacy policy + sitemap + SEO basics
- Handover documentation in `README.md` + `database_schema.md` + `ITR_Filing_Platform_BRD.md`
- Transferable rights to client for the delivered codebase
- Bug-fix support window: **15 days** (Lean) / **30 days** (Standard) / **60 days** (Premium)

---

## 10. Out of Scope (priced separately)

- Mobile native apps (iOS / Android)
- Tax-engine logic / ITR computation rules engine
- Third-party tax-portal integration (e.g. Income Tax Dept e-filing API)
- Penetration testing & formal security audit
- SLA-based production support beyond included window
- Long-term hosting & infra running costs (AWS / Hetzner / Vercel etc.)
- Content writing, copywriting, brand assets
- Major new feature requests after sign-off

---

## 11. Assumptions

1. Source code, migrations, and deployment configs are handed over as-is in the current Git repository.
2. Cloud infra (Postgres data dir, MinIO data dir, Grafana storage on `F:\PlatformData`) is owned by the client; this quote does **not** include monthly infra costs.
3. Client supplies the Cloudflare account, domain (`workpartners.co.in`), and `CLOUDFLARE_TUNNEL_TOKEN`.
4. Email SMTP credentials, MinIO keys, JWT secrets are provisioned by client.
5. Post-launch support is bug-fix only — new features will be quoted separately.
6. GST is extra at applicable rates if claimed under client's GSTIN.

---

## 12. Validity

This quotation is valid for **30 days** from the date of issue.

---

**Bottom-line ask: ₹11,35,000 (Eleven Lakh Thirty-Five Thousand Indian Rupees) for the complete ITR Filing Platform — Backend + DevOps/Cloud + Front-End — as delivered between 01 May 2026 and 13 June 2026.**

---

**Prepared:** 13 June 2026
**By:** Aditya Jadon
**For:** ITR Manager — Client (workpartners.co.in)
**Engagement type:** Fixed-price, deliverable-based
