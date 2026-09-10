# SRS v1.1 — Implementation Coverage

Traces every module and requirement in the *Master Software Requirements Specification & System Blueprint
(Version 1.1, July 2026)* to what was built. Section numbers refer to the SRS.

Legend: **Built** = implemented, seeded and exercised by tests · **Partial** = core implemented, noted limits ·
**Simulated** = works end to end locally, needs live credentials in `.env` for production traffic.

---

## Section 4 — User roles & access model

All twenty roles exist as seeded system roles with a module × action permission matrix
(view, add, update, approve, delete, execute, export, assign, configure) editable at `/admin/roles`.

| Role | Portal | Sign-in |
|---|---|---|
| Super Admin / Owner / CEO | admin | admin@oqc.local |
| System Administrator / Technology | admin | sysadmin@oqc.local |
| HOD People & Culture / Finance / Academics / QA / Technology / Marketing | admin | hr@ · finance@ · academics@ · qa@ · tech@ · marketing@oqc.local |
| Manager | admin | manager@oqc.local |
| Supervisor | admin | supervisor@oqc.local |
| Teacher | teacher | teacher1..12@oqc.local |
| Billing Representative | admin | billing@oqc.local |
| Lead Generator / Lead Closer | admin | leadgen@ · closer@oqc.local |
| Accountant | admin | accountant@oqc.local |
| QA Officer | admin | qaofficer@oqc.local |
| HR Officer | admin | hrofficer@oqc.local |
| Academic Coordinator | admin | coordinator@oqc.local |
| Client / Parent | client | parent1..40@oqc.local |
| Student | student | student1..N@oqc.local |
| External Auditor (read-only) | admin | auditor@oqc.local |

Record-level scoping is enforced in addition to role permissions: teachers see only their own students and
classes, supervisors only their assigned teachers, parents only their own family, students only themselves.

## Section 6 — Core functional modules (1–40)

| # | Module | Status | Where |
|---|---|---|---|
| 1 | CEO / Executive Command Center | Built | `/command-center` |
| 2 | Academic Home Dashboard | Built | `/dashboard` |
| 3 | CRM & Lead Management | Built | `/crm/leads` |
| 4 | Client / Parent Management | Built | `/clients` |
| 5 | Student Management | Built | `/students` |
| 6 | Online Registration | Built | `/register` (public), `/registrations` |
| 7 | Trial Management | Built | `/trials` |
| 8 | Class & Scheduling | Built | `/schedules`, `/classes` |
| 9 | Subscription & Package Management | Built | `/finance/subscriptions` |
| 10 | Curriculum & Academic Management | Built | `/academics/curriculum` |
| 11 | Lesson Planning | Built | `/academics/lesson-plans` |
| 12 | Student Evaluation | Built | `/academics/evaluations` |
| 13 | Teacher Portal | Built | `/teacher` |
| 14 | Student / Parent Portal | Built | `/portal`, `/student` |
| 15 | Supervisor Portal | Built | `/supervisor` |
| 16 | HOD & Manager Portals | Built | `/dashboard`, `/kpis/scorecards` |
| 17 | QA Module | Built | `/qa` |
| 18 | Billing | Built | `/finance/invoices` |
| 19 | Accounts & Finance | Built | `/finance/accounts` |
| 20 | HR / People & Culture | Built | `/hr/employees` |
| 21 | Payroll | Built | `/hr/payroll` |
| 22 | Roles & Permissions | Built | `/admin/roles` |
| 23 | Notifications & Communications | Built | `/admin/notifications` |
| 24 | Video Class Platform | Built | `/classroom/{id}` (Jitsi WebRTC) |
| 25 | AI Class Monitoring | Built | `/ai-monitoring` |
| 26 | KPI & Reports | Built | `/kpis`, `/reports` |
| 27 | Certificates | Built | `/academics/certificates`, `/verify/{number}` |
| 28 | Shared Arabic Lesson View | Built | `/academics/arabic-view/{student_id}` |
| 29 | Task & Project Management | Built | `/tasks` |
| 30 | Smart Reminders | Built | scheduler jobs |
| 31 | In-Platform Calling | Partial | call logging with masked numbers; no carrier/VoIP trunk |
| 32 | Complaints & Case Management | Built | `/cases` |
| 33 | Retention & Churn | Built | `/retention` |
| 34 | Marketing Analytics | Built | `/crm/marketing` |
| 35 | Data Migration | Built | `/admin/migration` |
| 36 | Integration & API Hub | Built | `/admin/integrations`, `/admin/api` |
| 37 | Security & Privacy | Built | `/admin/security` |
| 38 | AI Governance | Built | `/ai-governance` |
| 39 | Backup & Disaster Recovery | Built | `/admin/backups` |
| 40 | Transformation / Operating System | Built | `/transformation` |

## Section 29 — Revision 1.1 additions (Modules 41–49)

| # | Module | Status | Notes |
|---|---|---|---|
| 41 | Monthly Test & Result-Card Automation | Built | Tests auto-generated from curriculum position; bilingual English/Urdu result-card PDF; WhatsApp + portal delivery; month-over-month improvement %; dor schedule generated automatically; new sabaq blocked until the dor quota is met, with logged teacher override; declining scores routed to the retention queue |
| 42 | Feedback & Voice-of-Customer | Built | Triggered surveys, NPS and eNPS, negative ratings auto-raise an owned ticket with an SLA clock, staff grievances bypass department heads, sentiment feeds the Command Center |
| 43 | Ambassador (Referral) Program | Built | 60-day satisfaction-gated invite, unique family code and link, dual-sided **account credit** posted to both ledgers (never cash or coupons), referral ledger with weekly counts, referral share of gross adds |
| 44 | Teacher-Match at Enrolment | Built | Ranked recommendation on shift, timezone, gender and level fit; unverified teachers excluded; overrides require a logged reason; 90-day survival tracked |
| 45 | Pricing, Discount Ladder & Scholarship Governance | Built | 0–20 % manager, 21–35 % CEO, above 35 % impossible; teacher-cost-plus-20 % pricing floor enforced; scholarships recorded separately from discounts; weekly discount register |
| 46 | Teacher Development, Grading & Ustaadh Lab | Built | Grade A/B/C computed from QA, punctuality and retention; grade maps to salary band consumed by payroll; training assignments and promotion gates |
| 47 | Safeguarding of Minors & Anti-Poaching | Built | Recording retention with every access logged, contact masking, anti-poaching signals visible to CEO only, background-check gate on live-class assignment |
| 48 | Governance & Written-Record Discipline | Built | Immutable audit log with actor, timestamp and rationale; decision register requiring an owner; structured reports replace free chat |
| 49 | Device, Identity & Data-Governance Provisioning | Built | One-action role-based onboarding and offboarding, device naming convention, MFA compliance view |

## Sections 7–24 — Cross-cutting requirements

| Section | Requirement | Status |
|---|---|---|
| 7 | CRM, GHL & WhatsApp: two-way messaging, shared inbox, templates, opt-in/out, webhook retries, lead→client→student without re-entry | Built · WhatsApp and GHL **simulated** until credentials are set |
| 8 | Quran education: Qaida, Nazra, Hifz, Tajweed, Tarjuma; books, chapters, lessons, objectives; RTL, tashkeel, Tajweed colours; human-in-the-loop QA | Built · Tajweed colouring is a documented heuristic |
| 9 | AI monitoring: camera presence, punctuality, active teaching, engagement, curriculum coverage, tone and conduct flags, cost dashboard, model/version/confidence on every result, no action on AI score alone | Built · **simulated** provider by default; set `AI_PROVIDER` and `AI_API_KEY` for live inference. Transcription requires a live provider |
| 10 | QA framework: queue, random and risk-based sampling, scorecards, corrective actions, re-evaluation, trends, human approval for high-impact | Built |
| 11 | HR & payroll: recruitment through onboarding, twice-daily attendance with corrections, leave, violations, advances, bonuses, payroll approval, payslips, teacher cost/output | Built |
| 12 | Six-department operating model | Built (7 departments incl. Operations) |
| 13 | Iqra Tech 2.0 / Transformation OS | Built |
| 14 | Integrations: WhatsApp, GHL, n8n, Zoom, Google, Meta Ads, Google Ads, payments, SMTP, Slack, OpenProject, AI | Built as adapters · **simulated** without credentials. Zoom import and ad-platform pulls are stubs pending live keys |
| 15 | Security & privacy: RBAC, 2FA, session control, encryption in transit, audit trail, IP allowlist, masking, safeguarding, retention, export, incident logging, API auth, backups, DR | Built · encryption at rest is a deployment concern, documented in DEPLOYMENT.md |
| 16 | Data model — core entities | Built · 117 tables |
| 17 | Reporting & KPI framework | Built · 64 seeded KPIs across every role and department |
| 18 | Notifications & automation triggers | Built · 42 scheduled jobs |
| 19 | Multi-currency & international operations | Built · PKR base with GBP, USD, AUD, CAD, EUR; rate history; timezone-aware scheduling |
| 20 | Migration & go-live | Built · wizard plus cutover checklist |
| 21 | Non-functional requirements | Built · responsive, indexed, live boards (HTMX polling, the SRS's "WebSockets or equivalent"), automated tests, Alembic migrations |
| 22 | Environments & DevOps | Built · Alembic migrations under `migrations/`; environments, secrets, backups and rollback documented in DEPLOYMENT.md · CI/CD pipeline is not provisioned |
| 23 | Ownership & vendor independence | Built · no proprietary dependency; PostgreSQL and standard Python |
| 24 | Testing & acceptance | Built · 169+ automated tests plus a full-platform crawler |

## Section 26 — Minimum production readiness

| Criterion | Evidence |
|---|---|
| All critical roles have verified access boundaries | `tests/test_platform.py` asserts role isolation for all 19 accounts |
| No critical cross-role data leakage | Parent cannot open another family's student; teacher cannot open another teacher's classroom |
| Core class scheduling and status workflows validated | `tests/test_scheduling_module.py`, conflict detection proven |
| Billing and financial balances reconciled | Every journal entry balances individually and in aggregate |
| CRM lead-to-client conversion validated | `tests/test_crm_module.py` |
| QA workflows validated | `tests/test_scheduling_module.py` |
| Backup restoration tested | `/admin/backups` restore test, exercised in seed |
| Audit logs available for critical actions | Consequential actions carry actor, timestamp and rationale |
| Technical documentation and handover | `docs/` — conventions, admin guide, deployment, API, this coverage map |

## Known limits and what needs live credentials

1. **External providers are simulated by default.** WhatsApp Cloud API, GoHighLevel, SMTP, ad platforms and
   the AI provider all run through adapters that log and no-op locally. Set the corresponding values in
   `.env` to go live; no code changes are needed.
2. **AI analysis is heuristic locally.** Class monitoring, lead scoring, churn and complaint classification
   produce deterministic simulated results so every workflow runs offline. Real inference needs
   `AI_PROVIDER` and `AI_API_KEY`. Speech-to-text transcription requires a live provider.
3. **Video** uses the public Jitsi service by default. Self-hosting is a deployment choice (`JITSI_DOMAIN`).
4. **In-platform calling** logs masked calls but is not connected to a VoIP carrier.
5. **CI/CD** is described in DEPLOYMENT.md but no pipeline is provisioned, since that depends on the
   hosting provider the college chooses.
6. **Real-time updates** use HTMX polling on the live dashboard and supervisor board rather than a
   WebSocket layer. The SRS asks for "WebSockets or equivalent"; polling is simpler to operate behind
   nginx and adequate at this scale, but a WebSocket transport is the upgrade path for thousands of
   concurrent viewers.
7. **Encryption at rest** is a database and disk configuration, covered in DEPLOYMENT.md rather than in code.
8. **Open decisions in SRS sections 27 and 29.13** (cloud provider, video provider, payment gateways per
   country, retention periods, RPO/RTO, multi-tenancy) are left configurable rather than hard-coded.
   Current values live in `/admin/settings` and can be changed without a deployment.

## Appendix B — critical development rule

Each module ships with its data model, permissions, workflows, validation, audit logging, notifications,
API endpoints, error handling, reports, seed data and tests, rather than screens alone. Cross-module flows
exercised end to end include: lead → trial → client → student → schedule → class → attendance → recording →
AI analysis → QA review; and subscription → invoice → payment → receipt → ledger → journal → P&L.
