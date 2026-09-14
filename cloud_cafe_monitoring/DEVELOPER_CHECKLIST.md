# ☁️ Cloud Café Monitoring Software — Developer Checklist

**Scope:** Android + Web, cloud-hosted, multi-tenant.
**How to use:** Each item is a work unit with a stated *Done when* condition. An
item is not complete because the code exists — it is complete when the
condition holds in a deployed environment.

Legend: 🔴 blocker for pilot · 🟡 needed for production · 🟢 scale/enterprise

---

## 1. Core Modules

### 1.1 Customer monitoring (camera + AI object detection) 🔴

- [ ] Define the **counting line** geometry per camera (entry door, exit door, or a
      single bidirectional line) and store it as normalised coordinates so it
      survives resolution changes.
- [ ] Person detection model deployed to edge (YOLOv8n / RT-DETR, INT8 quantised).
- [ ] Multi-object tracker (ByteTrack or OC-SORT) assigns stable track IDs.
- [ ] Line-crossing logic emits `customer.entry` / `customer.exit` with direction,
      track ID, timestamp, confidence.
- [ ] **De-duplication:** a person loitering on the line must not produce N events.
      Require a track to fully cross a hysteresis band before counting.
- [ ] Staff exclusion — staff walking in and out must not inflate customer counts
      (exclude by uniform colour zone, staff-entrance camera, or track re-ID
      against enrolled staff).
- [ ] Live occupancy = entries − exits, reset nightly at configured close time,
      clamped at ≥ 0.
- [ ] Handle the double-door / group-entry case: validate against manual counts.

> **Done when:** over a 3-hour manual-count validation at a real outlet, entry
> count is within **±5%** of ground truth, and no single hour is off by >10%.

### 1.2 Staff attendance (face recognition / NFC) 🔴

- [ ] Choose primary method. **Recommendation: NFC card as primary, face as
      secondary/audit.** Face-only attendance fails on masks, low light, and
      creates the heaviest compliance burden.
- [ ] Staff enrolment flow: profile, role, branch, shift pattern, NFC card UID,
      optional face embedding — **gated behind recorded consent**.
- [ ] Face embeddings stored as vectors (ArcFace/InsightFace, 512-d), **never raw
      face images**, encrypted at rest.
- [ ] Liveness / anti-spoof check if face is used for clock-in (reject a photo
      held to the camera).
- [ ] Clock-in / clock-out events with source (`nfc` | `face` | `manual`), device ID.
- [ ] **Buddy-punching prevention:** NFC tap must be corroborated by a face match
      or a presence detection at the terminal within ±10s.
- [ ] Manual override by manager, with mandatory reason text, fully audit-logged.
- [ ] Shift computation: late arrival, early departure, overtime, break duration,
      missed clock-out auto-close at shift end + flag.

> **Done when:** a full week of shifts for 5 staff reconciles exactly against the
> paper register, including one manual override and one missed clock-out.

### 1.3 Cup tracking (IoT sensor / camera vision) 🔴

- [ ] Decide the primary signal per outlet. **Recommendation: IoT counter on the
      dispenser/machine as primary, vision as cross-check.** Vision alone on a
      crowded counter is the least reliable module in this system.
- [ ] IoT integration: pulse counter or machine API on the chai urn / espresso
      machine → `cup.dispensed` events over MQTT.
- [ ] Vision cup detection model trained on **your** cups — glass chai tumbler,
      paper cup, ceramic mug are visually distinct classes and a generic COCO
      model will not do this.
- [ ] Counter-zone ROI so only cups on the serving counter are counted, not cups
      on tables (which would double count).
- [ ] Track-based counting — one cup crossing the handoff line = one event; a cup
      sitting in frame for 40s is not 40 events.
- [ ] Classify chai vs. coffee vs. other; fall back to `unknown` rather than
      guessing, and report `unknown` separately.
- [ ] **Reconciliation engine:** join detected cups against POS line items in
      5-minute buckets, produce variance with a confidence band.
- [ ] Variance alert threshold configurable per branch (default: flag if
      detected − billed > 10% sustained over 30 minutes).

> **Done when:** on a 2-hour shift, IoT cup count matches manual tally within
> ±2%, and the reconciliation report correctly flags a deliberately unbilled
> batch of 10 cups.

### 1.4 POS integration (sales, billing sync) 🔴

- [ ] Inventory the actual POS in use (Petpooja / Posist / Square / Zomato Base /
      custom). **Do this before writing any integration code** — this decision
      shapes the whole module.
- [ ] Build a `PosAdapter` interface: `fetchOrders(since)`, `fetchItems()`,
      `subscribeWebhook()`. One implementation per POS.
- [ ] Ingest orders, line items, quantity, price, payment mode, void/refund flag,
      cashier ID, timestamp.
- [ ] **Idempotent ingest** keyed on POS order ID — replays and retries must not
      duplicate revenue.
- [ ] Map POS SKUs → internal beverage categories (chai, coffee, other) via an
      editable mapping table, not hardcoded strings.
- [ ] Backfill job for outages; gap detection alerts if no POS event for N minutes
      during open hours.
- [ ] Track **voids and discounts separately** — they are the second leakage
      vector after unbilled cups.

> **Done when:** a day of POS sales in the dashboard matches the POS's own
> end-of-day Z-report to the rupee, including voids and refunds.

### 1.5 Daily reporting (customers, cups, staff) 🔴

- [ ] Nightly aggregation job → `daily_branch_summary` (footfall, cups by type,
      billed vs. detected variance, revenue, labour hours, peak hour,
      average dwell time).
- [ ] Hourly rollups for intraday charts.
- [ ] Report must declare **data quality**: camera uptime %, POS sync status,
      and an explicit "incomplete data" banner when coverage < 90%.
- [ ] Timezone-correct business day boundary (a café closing at 01:00 belongs to
      the previous business day — do not use UTC midnight).
- [ ] Idempotent and re-runnable — reruns overwrite, never duplicate.

> **Done when:** the nightly job can be re-run three times for the same date and
> produce byte-identical output.

---

## 2. Front-End Platforms

### 2.1 Android App (Flutter) 🔴

**Staff dashboard**
- [ ] Clock in / clock out with NFC tap, with clear success/fail feedback.
- [ ] My shifts — today, this week, hours worked, late/OT flags.
- [ ] Shift log / handover notes.
- [ ] Leave or swap request submission.

**Manager dashboard**
- [ ] Today at a glance — live occupancy, footfall so far, cups served, revenue.
- [ ] Who's on shift now, who's late, who hasn't clocked out.
- [ ] Alerts feed — variance alerts, camera offline, POS desync, unusual voids.
- [ ] Approve / reject attendance overrides and leave requests.
- [ ] Daily report view + share as PDF.

**App-wide**
- [ ] Offline-first: local cache (Drift/SQLite), queued mutations, sync on
      reconnect. Cafés have bad wifi; this is not optional.
- [ ] Push notifications (FCM) for alerts, with per-category mute.
- [ ] Biometric/PIN app lock.
- [ ] Role-driven navigation — a staff user must never be able to reach manager
      screens, enforced server-side as well as in the UI.
- [ ] Support Android 8.0+ and low-end hardware; test on a ₹8,000 phone, not a Pixel.

> **Done when:** the app performs a full clock-in, shows today's numbers, and
> queues an override while in airplane mode, then syncs cleanly on reconnect.

### 2.2 Web App (React + TypeScript) 🔴

- [ ] Cloud reporting portal — date-range picker, per-branch and consolidated views.
- [ ] Branch-wise analytics — side-by-side comparison, ranking, trend lines,
      footfall→conversion funnel (visitors → orders → cups).
- [ ] Live view — occupancy per branch, table occupancy map, camera health tiles.
- [ ] Reconciliation screen — detected vs. billed cups, drill down to the
      5-minute bucket and the snapshot that triggered a variance.
- [ ] Staff module — roster, attendance register, payroll-ready hours export.
- [ ] Admin — branches, cameras, devices, users, SKU mapping, alert thresholds.
- [ ] Role-based access (Admin / Regional Manager / Branch Manager / Staff /
      Auditor-readonly), enforced by API scopes, not just route guards.
- [ ] Audit log viewer — who changed what, when. Required for any system that
      can be used in a disciplinary context.
- [ ] Responsive down to tablet; accessible (keyboard nav, WCAG AA contrast).

> **Done when:** a Branch Manager account can see only its own branch across
> every screen and every API call, verified by an automated permission test suite.

---

## 3. Back-End 🔴

- [ ] NestJS service skeleton: modules for auth, tenancy, branches, devices,
      attendance, events, pos, reports, alerts.
- [ ] **Tenancy middleware** resolving `tenant_id` from the JWT and injecting it
      into every query — enforce via Postgres Row Level Security as a second
      line of defence.
- [ ] Ingest service — high-throughput endpoint / Kafka consumer for edge events.
- [ ] ML service (FastAPI) for retraining, batch re-scoring, and forecasting;
      real-time inference stays on the edge.
- [ ] Event streaming: topics `cafe.customer`, `cafe.cup`, `cafe.attendance`,
      `cafe.pos`, `cafe.device.health`. Partition by `branch_id`.
      *Start with Redis Streams for the pilot; migrate to Kafka past ~5 branches.*
- [ ] PostgreSQL for entities + aggregates; MongoDB for raw detection events with
      a TTL index (raw events expire, aggregates are forever).
- [ ] OAuth2 + JWT: short-lived access token (15m), rotating refresh token,
      device binding, revocation list.
- [ ] Per-tenant rate limiting and request quotas.
- [ ] Structured JSON logging with correlation IDs threaded from edge → API → DB.
- [ ] OpenAPI spec generated from code, published, and used to generate the
      TypeScript and Dart clients.
- [ ] Background jobs (BullMQ): nightly aggregation, report generation, retention
      purge, forecast refresh.

> **Done when:** a load test sustains the expected peak event rate for 20 branches
> (≈50 events/sec) for one hour with p95 ingest latency < 200 ms and zero loss.

---

## 4. Camera Integration 🔴

- [ ] Edge device spec per branch (Jetson Orin Nano or equivalent x86 + NPU);
      size it for the number of camera streams, not the number of models.
- [ ] RTSP/ONVIF ingest with auto-reconnect and backoff — IP cameras drop
      constantly and the pipeline must self-heal without a restart.
- [ ] Per-camera role config: `entry_counter` | `cup_counter` | `table_occupancy` |
      `attendance_terminal`.
- [ ] Frame sampling (3–8 FPS is plenty; do not run detection at 25 FPS).
- [ ] Models: person detection, cup detection + classification, table occupancy.
- [ ] **Table occupancy:** define table polygons in a UI; occupancy = person
      detected inside polygon for > 60s. Produce dwell time per table.
- [ ] Snapshot capture on event (not continuous video) → upload to S3, store the key.
- [ ] **Privacy: face blurring applied at the edge, before upload**, for all
      customer-facing cameras. A snapshot with an unblurred customer face must
      never reach cloud storage.
- [ ] Local buffering (SQLite ring buffer) during internet outage, replay on
      reconnect with original timestamps preserved.
- [ ] Device health heartbeat every 60s → offline alert after 3 misses.
- [ ] Remote model/config update with staged rollout and rollback.
- [ ] Low-light and glare handling — validate at the actual worst hour of the day
      at a real site before declaring the model done.

> **Done when:** a branch survives a 4-hour internet outage and a camera power
> cycle, and after recovery the day's counts are complete and correctly timestamped.

---

## 5. Cloud Infrastructure 🟡

- [ ] Pick one cloud and commit. **Recommendation: AWS** (MSK, S3, RDS, EKS,
      IoT Core all in one place).
- [ ] Infrastructure as Code — Terraform, no console clicking.
- [ ] Environments: `dev`, `staging`, `prod`, fully separated accounts/projects.
- [ ] Compute: EKS or ECS Fargate with autoscaling on queue depth, not just CPU.
- [ ] Storage: S3 with lifecycle rules — snapshots to Infrequent Access at 30d,
      delete at the configured retention limit.
- [ ] RDS PostgreSQL Multi-AZ, automated backups, PITR, tested restore.
- [ ] Secrets in Secrets Manager / Vault. No credentials in env files in the repo.
- [ ] TLS everywhere; mTLS for edge→cloud device authentication with per-device
      certificates that can be revoked individually.
- [ ] Encryption at rest (KMS) for DB, S3, and especially the biometric vector store.
- [ ] Network: private subnets for DB, VPC endpoints, WAF on the public API.
- [ ] Cost guardrails: budget alerts, per-branch cost attribution tags. Video
      infrastructure costs escalate quietly.

> **Done when:** `terraform apply` builds a working environment from zero, and a
> restore-from-backup drill succeeds within the stated RTO.

---

## 6. Reporting & Analytics 🟡

- [ ] Report engine: daily / weekly / monthly, PDF + Excel, branch or consolidated.
- [ ] Scheduled email delivery to owner and managers at a configured local time.
- [ ] In-app dashboards for business users (footfall curve, cup mix, variance,
      labour cost vs. revenue).
- [ ] Grafana for engineering metrics — pipeline lag, model FPS, device uptime.
      Keep this separate from the business UI; café owners should never see Grafana.
- [ ] **Predictive analytics:** demand forecast for cups per hour/day.
      Baseline = seasonal naive (same weekday, same hour, last 4 weeks). Only move
      to Prophet/LightGBM once the baseline is beaten on a held-out month —
      publish the MAPE for both.
- [ ] Anomaly detection: unusual voids, footfall without matching sales, cup
      variance spikes, attendance pattern anomalies.
- [ ] Export API for accounting (Tally / Zoho Books / QuickBooks) — daily sales
      summary in the format the accountant actually uses, plus a generic CSV.
- [ ] Every report includes the data-quality footer (camera uptime, POS sync
      status, count confidence band).

> **Done when:** the weekly PDF lands in the owner's inbox automatically for four
> consecutive weeks with no manual intervention, and the numbers match the
> dashboard.

---

## 7. Deployment & Maintenance 🟡

- [ ] CI (GitHub Actions): lint, typecheck, unit + integration tests, build,
      container scan, SBOM. Required checks on the default branch.
- [ ] CD: staging on merge, production on tag with manual approval.
- [ ] Database migrations versioned and reversible; migration runs in CI against a
      copy of production schema before it is allowed near production.
- [ ] **Edge fleet OTA updates** — staged rollout (1 branch → 10% → all), health
      gate between stages, automatic rollback. This is the deployment surface
      people forget and then dread.
- [ ] Monitoring: Prometheus + Alertmanager; golden signals per service plus
      domain alerts (branch reporting zero footfall during open hours is an alert,
      not a data point).
- [ ] Error tracking: Sentry for API, web, Android and edge agent, with release
      tagging and source maps.
- [ ] Uptime/synthetic checks on the public API and login flow.
- [ ] On-call runbook: camera offline, POS desync, ingest lag, model drift,
      failed nightly job.
- [ ] **Model drift monitoring** — track detection confidence distribution per
      camera weekly; a shift means re-labelling and retraining, and it *will*
      happen when the café changes its cups or its lighting.
- [ ] Staff training material: 1-page laminated card for clock-in, 15-minute
      manager walkthrough, short video. Adoption fails here more often than in code.
- [ ] Quarterly model retraining cycle with a labelled holdout set per branch.

> **Done when:** a full release reaches production — cloud and edge fleet —
> through the pipeline with no manual steps, and a deliberate bad build is
> auto-rolled-back at the 10% stage.

---

## 8. Cross-cutting: Privacy & Compliance 🔴

Detailed in [`COMPLIANCE.md`](COMPLIANCE.md). Checklist form:

- [ ] Signage at entry disclosing CCTV and analytics, in local language.
- [ ] Written, revocable staff consent for biometric enrolment before any
      embedding is generated.
- [ ] Non-biometric alternative (NFC/PIN) always available — consent that costs
      someone their job is not consent.
- [ ] Face blurring on all stored customer imagery.
- [ ] Retention policy enforced by job: raw events 30d, snapshots 30–90d,
      aggregates indefinite, biometric vectors deleted within 30d of exit.
- [ ] Data subject requests: access, correction, deletion — with an owner and an SLA.
- [ ] DPA with the cloud provider; data residency in-country if required
      (India DPDP Act, GDPR Art. 9 for biometrics if EU).
- [ ] Access to raw footage and biometric data restricted to named roles and
      fully audit-logged.
- [ ] Security review + penetration test before go-live.

> **Done when:** a privacy review signs off in writing, and a deletion request
> executed end-to-end removes the subject from every store including backups
> policy documentation.

---

## Definition of Pilot-Ready (one café)

All 🔴 items complete, plus:
- 14 consecutive days of unattended operation
- Footfall within ±5%, cup count within ±5% of manual ground truth
- Daily report delivered every morning without intervention
- Manager using the Android app daily without support contact
