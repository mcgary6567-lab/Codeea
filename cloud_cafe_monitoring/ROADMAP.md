# Delivery Roadmap

Estimates assume the team shape below and a real pilot café that will let you
install hardware and run manual validation counts. Without that access the
vision modules cannot be finished at any price — secure the site before Phase 1.

## Team

| Role | Count | Phases |
|---|---|---|
| Backend (Node/NestJS) | 2 | 1–5 |
| Computer vision / ML (Python) | 1–2 | 1–4 |
| Android (Flutter) | 1 | 2–5 |
| Web (React) | 1 | 2–5 |
| DevOps / infra | 0.5 | 1–5 |
| QA | 0.5 | 2–5 |
| Product / project lead | 0.5 | 1–5 |

A single full-stack developer cannot build this. The vision pipeline and the
POS integrations are independent specialities and both are on the critical path.

---

## Phase 0 — Discovery (2 weeks)

Cheap to do, catastrophic to skip.

- [ ] Site survey: camera positions, lighting at worst hour, network, power.
- [ ] **Identify the exact POS** and confirm API/webhook availability in writing.
- [ ] Confirm whether the chai urn / coffee machine can expose a pulse or API.
- [ ] Collect and label a starter dataset from the actual site (≥2,000 frames
      across morning, peak, and night).
- [ ] Legal review of biometric attendance in the operating jurisdiction.
- [ ] Agree the ground-truth validation protocol with the café owner.

**Exit:** hardware BOM signed off, POS integration path confirmed, labelled
dataset in hand.

> If the POS has no API and the machine has no counter, say so now and re-scope.
> Discovering this in Phase 3 costs the project a month.

---

## Phase 1 — Foundation (4 weeks)

- [ ] Terraform: VPC, RDS, S3, ECS/EKS, secrets, dev + staging.
- [ ] NestJS skeleton, tenancy middleware, RLS policies, OAuth2/JWT.
- [ ] Postgres schema + migrations; Mongo collections + TTL indexes.
- [ ] Ingest endpoint with idempotent upsert; Redis Streams.
- [ ] Edge agent skeleton: RTSP ingest, frame sampler, buffer, mTLS uplink,
      heartbeat. **No models yet** — emit synthetic events to prove the pipe.
- [ ] CI: lint, test, build, container scan.

**Exit:** a synthetic event produced at the edge appears in a dashboard query
in the cloud, and survives a simulated 2-hour network outage.

---

## Phase 2 — Core detection + attendance (6 weeks)

- [ ] Person detection + tracker + line crossing → footfall.
- [ ] Face blur at edge, snapshot upload.
- [ ] NFC terminal integration, punch API, shift computation.
- [ ] Face enrolment + consent records + match (secondary to NFC).
- [ ] Android: clock in/out, my shifts, today-at-a-glance.
- [ ] Web: login, branch dashboard, attendance register.
- [ ] First ground-truth validation run against manual counts.

**Exit:** footfall within ±5% over a 3-hour validation; a week of attendance
reconciles against the paper register.

---

## Phase 3 — Cups + POS + reconciliation (6 weeks)

The value phase. Everything before this is plumbing.

- [ ] Cup dataset labelled for **your** cup types; train + quantise.
- [ ] IoT dispenser counter integration (primary signal).
- [ ] Cup counting on handoff line with ROI.
- [ ] POS adapter for the confirmed provider; idempotent ingest; SKU mapping.
- [ ] Reconciliation processor, 5-minute buckets, confidence bands.
- [ ] Variance alerting + evidence drill-down.
- [ ] Nightly aggregation job; daily report API.
- [ ] Android manager alerts; Web reconciliation screen.

**Exit:** a deliberately unbilled batch of 10 cups is correctly flagged, and the
day's POS total matches the Z-report exactly.

---

## Phase 4 — Reporting, occupancy, polish (5 weeks)

- [ ] Table occupancy + dwell time.
- [ ] PDF/Excel report engine + scheduled email.
- [ ] Branch comparison analytics (multi-branch).
- [ ] Accounting export (Tally/Zoho/CSV).
- [ ] Grafana ops dashboards, Prometheus alerting, Sentry across all clients.
- [ ] Demand forecast — seasonal-naive baseline first, then a model only if it
      beats the baseline on a held-out month.
- [ ] Offline-first hardening on Android; permission test suite on web.

**Exit:** owner receives the weekly PDF automatically for 4 consecutive weeks.

---

## Phase 5 — Pilot hardening (4 weeks)

- [ ] Edge OTA staged rollout with auto-rollback.
- [ ] Runbooks, on-call rotation, synthetic checks.
- [ ] Backup/restore drill; RTO/RPO measured, not assumed.
- [ ] Security review + penetration test.
- [ ] Privacy sign-off; deletion request executed end-to-end.
- [ ] Staff training materials; manager walkthrough.
- [ ] 14-day unattended operation soak.

**Exit:** Definition of Pilot-Ready in `DEVELOPER_CHECKLIST.md` fully met.

---

## Timeline

**~27 weeks (≈6.5 months)** to a hardened single-café pilot.

```
Phase 0  ██                                        wk 1–2
Phase 1    ████                                    wk 3–6
Phase 2        ██████                              wk 7–12
Phase 3              ██████                        wk 13–18
Phase 4                    █████                   wk 19–23
Phase 5                         ████               wk 24–27
```

Multi-branch rollout adds ~4 weeks (Kafka migration, regional hierarchy,
branch-comparison analytics at scale). Enterprise features — SSO, data
warehouse export, per-region dashboards — are a further ~6 weeks and should not
be committed to before the pilot proves the core numbers.

---

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| POS has no usable API | Kills reconciliation, the core value | Confirm in Phase 0; fall back to receipt-printer tap or nightly CSV |
| Cup detection accuracy too low | Variance signal becomes noise, users stop trusting it | IoT counter as primary; vision as corroboration only |
| Staff resist biometric attendance | Adoption failure, possible legal exposure | NFC primary, face optional, consent recorded, alternative always available |
| Variance misread as theft accusation | Staff-relations damage, tool abandoned | Confidence bands in API and UI; alert copy reviewed; evidence always attached |
| Bad site lighting | Model underperforms only at peak hours | Validate at worst hour in Phase 0, not in UAT |
| Edge hardware cost per branch | Rollout economics fail | Size for stream count; consider 1 device per 4 cameras |
| Cloud egress/storage creep | Margin erosion | Edge-first inference, snapshots not video, lifecycle rules, cost tags |
| Model drift after cup/lighting change | Silent accuracy decay | Weekly confidence-distribution monitoring, quarterly retrain |
