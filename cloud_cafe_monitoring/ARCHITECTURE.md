# Architecture — Cloud Café Monitoring

## Guiding principle: edge-first

Video is the heaviest, most sensitive, and least valuable data in the system.
The *events* extracted from it are light, useful, and shareable. So all
inference runs on an edge device in the café, and only events plus occasional
blurred snapshots travel to the cloud.

A 4-camera branch streaming 1080p to the cloud costs roughly 1.5 TB/month in
egress and gives you nothing that a 200 KB/day event stream doesn't. This is
the decision the whole design hangs on.

---

## System diagram

```
┌──────────────────────── CAFÉ (edge) ─────────────────────────┐
│                                                              │
│  IP Cameras ──RTSP──┐                                        │
│  (entry, counter,   │    ┌──────────────────────────────┐    │
│   seating)          ├───▶│  Edge Agent (Jetson / x86)   │    │
│                     │    │  ├─ frame sampler (3–8 fps)  │    │
│  NFC Terminal ──────┤    │  ├─ person det. + tracker    │    │
│                     │    │  ├─ cup det. + classifier    │    │
│  IoT cup counter ───┤    │  ├─ table occupancy          │    │
│  (MQTT)             │    │  ├─ FACE BLUR (pre-upload)   │    │
│                     │    │  ├─ local SQLite buffer      │    │
│  POS terminal ──────┘    │  └─ mTLS uplink + replay     │    │
│                          └───────────────┬──────────────┘    │
└──────────────────────────────────────────┼───────────────────┘
                                           │ events (JSON)
                                           │ snapshots (S3 presigned)
                              ═════════════▼═════════════
                                     CLOUD (AWS)
┌──────────────────────────────────────────────────────────────┐
│  API Gateway / WAF                                           │
│        │                                                     │
│   ┌────▼─────┐   ┌──────────────┐   ┌────────────────────┐   │
│   │ Ingest   │──▶│ Kafka / Redis│──▶│ Stream processors  │   │
│   │ service  │   │ Streams      │   │ ├ occupancy        │   │
│   └──────────┘   └──────────────┘   │ ├ reconciliation   │   │
│                                     │ ├ alerting         │   │
│   ┌──────────┐                      │ └ rollups          │   │
│   │ Core API │◀──────────────┐      └─────────┬──────────┘   │
│   │ (NestJS) │               │                │              │
│   └────┬─────┘               │                ▼              │
│        │           ┌─────────┴──────┐   ┌───────────┐        │
│        │           │ ML svc(FastAPI)│   │ MongoDB   │ raw    │
│        │           │ forecast/retrain│  │ (TTL 30d) │ events │
│        │           └────────────────┘   └───────────┘        │
│        ▼                                                     │
│   ┌──────────┐  ┌───────┐  ┌────┐  ┌──────────────────┐      │
│   │PostgreSQL│  │ Redis │  │ S3 │  │ Report worker    │      │
│   │(entities,│  │(live, │  │(snap│ │ (PDF/Excel, cron)│      │
│   │aggregates)│ │ cache)│  │ rpts)│ └──────────────────┘     │
│   └──────────┘  └───────┘  └────┘                            │
└───────────────┬──────────────────────────┬───────────────────┘
                │ REST / WebSocket         │ scheduled email
        ┌───────▼────────┐      ┌──────────▼────────┐
        │ Android        │      │ Web portal        │
        │ (Flutter)      │      │ (React + TS)      │
        │ staff+manager  │      │ owner / head office│
        └────────────────┘      └───────────────────┘
```

---

## Edge agent responsibilities

| Concern | Behaviour |
|---|---|
| Stream health | RTSP reconnect with exponential backoff; never require a restart |
| Sampling | 3–8 FPS; detection at full 25 FPS buys nothing and costs a GPU |
| Inference | INT8 quantised models via TensorRT/ONNX Runtime |
| Privacy | Face blur applied **before** any frame is written to disk or uploaded |
| Buffering | SQLite ring buffer, 72h capacity, replay preserves original timestamps |
| Identity | Per-device X.509 cert, individually revocable |
| Updates | Pull-based OTA, staged, health-gated, auto-rollback |

The edge agent must be assumed to be running in a room with unreliable power and
a consumer router. Every failure mode it can recover from by itself, it must.

---

## Event flow: the reconciliation path

This is the flow that produces the product's core value.

1. Cup leaves the counter → edge emits `cup.served` *(or IoT dispenser emits
   `cup.dispensed` — preferred as primary)*.
2. POS adapter ingests order line items → `pos.order_line`.
3. Reconciliation processor buckets both streams into 5-minute windows per branch.
4. `variance = detected_cups − billed_cups`, with a confidence interval derived
   from rolling detection accuracy for that camera.
5. Sustained variance above threshold → `alert.cup_variance` with the snapshot
   keys for the window attached.
6. Manager sees the alert on Android; auditor drills into the window on web.

**Design note.** Variance is a *signal*, not an accusation. A busy hour with
glare on the counter produces variance too. The UI must present the confidence
band and the contributing snapshots, and the alert copy must never assert theft.
Getting this wrong turns a useful tool into a staff-relations problem — and into
a tool managers stop trusting.

---

## Multi-tenancy

Three levels: `tenant` (the business) → `branch` (the outlet) → `device`.

- Every row in every table carries `tenant_id`; branch-scoped tables also carry
  `branch_id`.
- `tenant_id` is resolved from the JWT by middleware and set as a Postgres
  session variable; **Row Level Security policies enforce it at the database**,
  so an ORM mistake cannot leak across tenants.
- S3 keys are namespaced `tenant/{tenant_id}/branch/{branch_id}/...`.
- Kafka topics are shared, partitioned by `branch_id`; the tenant is in the
  message envelope.

## Roles

| Role | Scope | Can |
|---|---|---|
| `owner` | tenant | Everything incl. billing, all branches |
| `regional_manager` | branch set | Reports + config for assigned branches |
| `branch_manager` | one branch | Live view, reports, attendance overrides, alerts |
| `staff` | self | Clock in/out, own shifts, own requests |
| `auditor` | tenant, read-only | Reports, reconciliation, audit log; no config |

Raw footage and biometric vectors are reachable by `owner` and `auditor` only,
and every such access writes an audit-log entry.

---

## Failure modes and expected behaviour

| Failure | Behaviour |
|---|---|
| Internet down | Edge buffers up to 72h, replays on reconnect with original timestamps |
| Camera offline | Heartbeat miss ×3 → alert; reports mark the gap as *incomplete*, never as zero |
| POS unreachable | Backfill job on recovery; reconciliation for affected windows marked `pending` |
| Edge device dead | Alert to manager + support; branch reports flagged unavailable |
| Model degraded | Weekly confidence-distribution check flags drift → retraining cycle |
| Kafka lag | Autoscale consumers on queue depth; ingest never blocks the edge |

The rule across all of these: **degrade visibly, never silently**. A zero in a
report must mean "zero happened", not "we weren't looking".

---

## Scaling checkpoints

| Branches | Change |
|---|---|
| 1 (pilot) | Single API instance, Redis Streams, RDS t4g.medium, no Kafka |
| 2–10 | Redis Streams still fine; add read replica; report worker separated |
| 10–50 | Move to Kafka (MSK); partition by branch; Mongo sharded by tenant |
| 50+ | Regional deployments, data warehouse (Redshift/BigQuery) for analytics, SSO |

Do not build the 50-branch architecture for the 1-branch pilot. Do keep the
schema and the topic contracts compatible with it — those are the parts that
are expensive to change later.
