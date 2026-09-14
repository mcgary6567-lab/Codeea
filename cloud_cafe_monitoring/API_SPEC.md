# API Specification

Base: `https://api.{tenant}.cafemonitor.io/v1`
All responses JSON. All timestamps RFC 3339 with offset. All money as decimal
strings, never floats.

---

## Authentication

OAuth2 with JWT. Access token 15 minutes, refresh token 30 days with rotation
(each refresh invalidates the previous — reuse of an old refresh token revokes
the whole family and raises a security alert).

```
POST /auth/login          { email | phone, password }      → { access, refresh, user }
POST /auth/refresh        { refresh }                      → { access, refresh }
POST /auth/logout         { refresh }                      → 204
POST /auth/device/token   mTLS client cert                 → { access }   # edge agents
```

**Claims**
```json
{
  "sub": "user-uuid",
  "tid": "tenant-uuid",
  "role": "branch_manager",
  "brs": ["branch-uuid-1"],
  "scp": ["reports:read", "attendance:write", "alerts:ack"],
  "exp": 1789000000
}
```

Authorisation is checked against `scp` on every endpoint, and `tid` is bound to
the Postgres session variable before any query runs. Route guards in the web and
Android clients are a usability feature, not a security control.

---

## Ingest (edge agent → cloud)

Authenticated by mTLS device certificate. Batched, idempotent, compressed.

```
POST /ingest/events
Content-Encoding: gzip
{
  "deviceId": "uuid",
  "batchId": "uuid",
  "events": [
    { "eventId": "sha256…", "type": "customer.entry", "ts": "2026-09-14T09:12:03+05:00",
      "cameraId": "uuid", "trackId": "cam1-4471", "confidence": 0.91,
      "attrs": { "direction": "in" } },
    { "eventId": "sha256…", "type": "cup.served", "ts": "…",
      "cameraId": "uuid", "confidence": 0.78,
      "attrs": { "cupType": "chai" }, "snapshotKey": "tenant/…/evt.jpg" }
  ]
}
→ 202 { "accepted": 2, "duplicates": 0, "rejected": [] }
```

`eventId` makes this safe to retry. Replay after an outage sends the same ids;
duplicates are counted and dropped.

```
POST /ingest/heartbeat    { deviceId, ts, cpuPct, gpuPct, tempC,
                            streamsUp, streamsConfigured, bufferDepth,
                            agentVersion }                → 204
GET  /ingest/snapshot-url ?cameraId&eventId              → { url, key, expiresIn }
GET  /devices/me/config                                  → camera config, ROIs,
                                                            thresholds, model pins
GET  /devices/me/updates                                 → { agentVersion, models[],
                                                             rolloutStage }
```

---

## Live

```
GET /branches/{id}/live
→ {
    "occupancy": 23,
    "occupancyUpdatedAt": "2026-09-14T14:02:11+05:00",
    "footfallToday": 187,
    "cupsDetectedToday": 214,
    "cupsBilledToday": "209.0",
    "revenueToday": "18420.00",
    "staffOnShift": [ { "staffId": "…", "name": "…", "since": "…" } ],
    "tables": [ { "code": "T1", "occupied": true, "dwellMin": 22 } ],
    "dataQuality": { "camerasUp": 3, "camerasTotal": 4, "posSynced": true,
                     "confidence": 0.92 }
  }

WS /branches/{id}/stream      # push: occupancy, cup events, alerts
```

Note `dataQuality` is part of the live payload, not an afterthought. Clients are
required to render the degraded state when `camerasUp < camerasTotal`.

---

## Reports

```
GET /branches/{id}/reports/daily?date=2026-09-14
GET /branches/{id}/reports/range?from=…&to=…&granularity=hour|day
GET /tenants/me/reports/consolidated?from=…&to=…        # all permitted branches
GET /branches/{id}/reports/reconciliation?date=…&bucket=5m
POST /reports/export   { scope, branchIds[], from, to, format: "pdf"|"xlsx" }
     → 202 { jobId }        # async; poll or receive webhook
GET  /reports/export/{jobId}  → { status, downloadUrl?, expiresAt? }
GET  /export/accounting?date=…&format=tally|zoho|csv
```

Daily report response carries the quality footer:

```json
{
  "businessDate": "2026-09-14",
  "footfall": 412,
  "cups": { "detected": 468, "billed": "455.0", "variance": "13.0",
            "confidenceBand": [-8, 22], "chai": 331, "coffee": 118, "unknown": 19 },
  "revenue": "41250.00",
  "orders": 289, "voids": { "count": 4, "amount": "620.00" },
  "labour": { "minutes": 2160, "cost": "5400.00" },
  "peakHour": 18,
  "avgDwellMin": 24.6,
  "dataQuality": { "score": 0.94, "cameraUptimePct": 97.2,
                   "posSynced": true, "gaps": [] },
  "isComplete": true
}
```

The `confidenceBand` is mandatory on any detected-cup figure. A variance of 13
with a band of [-8, 22] is not an accusation, and the API shape should make that
impossible to misread.

---

## Attendance

```
POST /attendance/punch       { staffId?, nfcUid?, faceMatchId?, type: "in"|"out",
                               deviceId, ts, confidence? }     → 201
GET  /branches/{id}/attendance?date=…                          → register
GET  /staff/{id}/shifts?from=…&to=…
POST /attendance/{punchId}/override  { correctedAt, reason }   → 200  (audited)
GET  /branches/{id}/payroll-export?month=2026-09               → hours per staff
```

`override` requires `attendance:write`, a non-empty reason, and writes to
`audit_log`. There is no endpoint to delete a punch — corrections are new rows.

---

## Staff & consent

```
GET    /branches/{id}/staff
POST   /branches/{id}/staff              { fullName, employeeCode, designation, … }
PATCH  /staff/{id}
POST   /staff/{id}/nfc                   { cardUid }
POST   /staff/{id}/consent               { method, documentKey }   → consent record
DELETE /staff/{id}/consent               { reason }  # revoke → schedules purge
POST   /staff/{id}/face-enrol            multipart image
       → 201 { embeddingId }   # 409 if no active consent record
DELETE /staff/{id}/face-enrol            # immediate purge
```

`face-enrol` returning **409 without an active consent record** is a hard
requirement, not a nicety. It is the technical control that backs the policy.

---

## Alerts

```
GET  /branches/{id}/alerts?status=open&kind=…
POST /alerts/{id}/ack       { note? }
POST /alerts/{id}/resolve   { resolution }
GET  /alerts/{id}/evidence  → { snapshots[], posOrders[], detectionEvents[] }
PUT  /branches/{id}/alert-config  { cupVariancePct: 10, sustainedMinutes: 30,
                                    zeroFootfallMinutes: 45, … }
```

---

## Admin

```
GET/POST/PATCH  /tenants/me/branches
GET/POST/PATCH  /branches/{id}/cameras           # incl. counting_line, roi_polygon
GET/POST        /branches/{id}/tables            # polygons for occupancy
GET/POST        /branches/{id}/devices           # provisioning, cert rotation
GET/PUT         /tenants/me/sku-map              # SKU → category, cups per unit
GET/POST/PATCH  /tenants/me/users                # + branch access grants
GET             /tenants/me/audit-log?from=…&to=…&action=…
POST            /branches/{id}/pos/sync          # manual backfill trigger
```

---

## Stream topics

Partition key: `branch_id`. Envelope is identical across topics.

| Topic | Payload |
|---|---|
| `cafe.customer` | entry/exit, direction, trackId, confidence |
| `cafe.cup` | served/dispensed, cupType, source (`vision`\|`iot`), confidence |
| `cafe.attendance` | punch in/out, staffId, source, corroborated |
| `cafe.pos` | order + lines, void flag, external id |
| `cafe.device.health` | heartbeat, stream status, buffer depth |
| `cafe.alerts` | emitted alerts for fan-out to push/email |

```json
{
  "envelope": { "tenantId": "…", "branchId": "…", "eventId": "sha256…",
                "ts": "…", "receivedAt": "…", "schemaVersion": 1,
                "producer": "edge-agent/2.3.1" },
  "payload": { }
}
```

Consumers must tolerate unknown fields and must key on `eventId` for
idempotency. Schema changes go through a registry; `schemaVersion` bumps on any
breaking change.

---

## Conventions

- **Errors:** RFC 7807 problem+json.
  ```json
  { "type": "https://docs.cafemonitor.io/errors/consent-required",
    "title": "Biometric consent required",
    "status": 409, "detail": "No active consent record for staff …",
    "instance": "/v1/staff/…/face-enrol" }
  ```
- **Pagination:** cursor-based — `?limit=50&cursor=…` → `{ data, nextCursor }`.
  Never offset pagination on event tables.
- **Idempotency:** `Idempotency-Key` header on all POSTs that create money- or
  attendance-affecting records.
- **Rate limits:** per tenant and per device; `429` with `Retry-After`.
  Ingest limits are generous — never make the edge drop events because of
  throttling; make it buffer.
- **Versioning:** URL major version; additive changes only within a version.
