# ☁️ Cloud Café Monitoring Software

Cloud-based monitoring and analytics platform for cafés and chai/coffee outlets.
Combines CCTV-based computer vision, staff attendance, IoT cup tracking and POS
data into a single cloud reporting layer, surfaced through an **Android app**
(staff + manager) and a **Web portal** (owner + head office).

---

## What the system answers

| Question | Source |
|---|---|
| How many customers walked in today? | Camera – entry/exit line-crossing counter |
| How many cups were served (chai vs coffee)? | Camera cup detection + IoT dispenser counter |
| Do cups served match cups billed? | Cup count reconciled against POS receipts |
| Who was on shift, and when did they clock in? | Face recognition / NFC attendance |
| Which tables are occupied right now? | Camera table-occupancy model |
| How many cups will I need tomorrow at 5 PM? | Demand forecasting model |

The headline value is **reconciliation**: cups detected vs. cups billed. That
delta is the leakage/pilferage signal, and it is the reason the camera pipeline
and the POS integration must land in the same milestone.

---

## Documents in this folder

| File | Purpose |
|---|---|
| [`DEVELOPER_CHECKLIST.md`](DEVELOPER_CHECKLIST.md) | The master build checklist — every module with acceptance criteria |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System architecture, edge vs. cloud split, data flow |
| [`DATA_MODEL.md`](DATA_MODEL.md) | PostgreSQL relational schema + MongoDB event collections |
| [`API_SPEC.md`](API_SPEC.md) | REST endpoints, Kafka topic contracts, auth model |
| [`ROADMAP.md`](ROADMAP.md) | Phased delivery plan, team shape, effort estimates |
| [`COMPLIANCE.md`](COMPLIANCE.md) | Privacy, biometric consent, retention, legal obligations |

---

## Platform recommendation

| Deployment | Recommendation | Why |
|---|---|---|
| **Single café** | Android + Web combo | Manager lives on the phone; web used weekly for reports. One edge box, one tenant. |
| **Multiple branches (2–20)** | Web-first, Android for staff | Centralised comparison across branches is a desktop task. Android reduced to attendance + alerts. |
| **Enterprise chain (20+)** | Hybrid with advanced analytics | Regional hierarchy, per-region dashboards, forecasting, data warehouse export, SSO. |

Build order is the same in all three cases — the difference is how much of
Phase 4 (multi-branch analytics) you need. Do **not** design the single-café
version with a single-tenant schema; every table carries `branch_id` and
`tenant_id` from day one, because retrofitting tenancy is the single most
expensive mistake in this class of product.

---

## Recommended stack (decisions, not options)

Pick one per row and stop debating; the checklist assumes the **Recommended**
column throughout.

| Layer | Recommended | Alternative | Deciding factor |
|---|---|---|---|
| Android | **Flutter** | React Native | One codebase also gives you a tablet POS-side view later |
| Web | **React + TypeScript** | Angular | Hiring pool, ecosystem for charting |
| API | **Node.js (NestJS)** | Django REST | Same language as web; NestJS gives structure Express lacks |
| ML serving | **Python (FastAPI) + TensorRT** | TorchServe | Vision team works in Python regardless |
| Streaming | **Kafka (MSK)** | Redis Streams | Kafka only once >5 branches; Redis Streams is fine for a pilot |
| Relational DB | **PostgreSQL 16** | — | Attendance, POS, users, config, aggregates |
| Event store | **MongoDB** | Timescale | High-volume raw detection events, flexible schema |
| Cache / realtime | **Redis** | — | Live occupancy, rate limits, websocket fan-out |
| Object storage | **S3** | GCS/Blob | Snapshots, clips, generated reports |
| Auth | **OAuth2 + JWT (Keycloak or Auth0)** | Custom | Do not hand-roll; you need refresh rotation and RBAC |
| Dashboards | **Grafana** (ops) + in-app charts (business) | PowerBI | Grafana for engineers, native UI for café managers |

---

## Non-negotiables

1. **Edge-first inference.** Video never leaves the café except as snapshots and
   events. Bandwidth, cost and privacy all demand it.
2. **Every table is tenant- and branch-scoped.** See above.
3. **Biometric consent is captured before enrolment**, stored, and revocable.
   See [`COMPLIANCE.md`](COMPLIANCE.md).
4. **Counts are estimates, and the UI says so.** Vision counts carry a
   confidence band. Never present a detected cup count with the same visual
   authority as a billed cup count.
5. **The system degrades, it does not stop.** Internet down → edge buffers and
   replays. Camera down → alert, and reports mark the gap rather than
   silently reporting zero.
