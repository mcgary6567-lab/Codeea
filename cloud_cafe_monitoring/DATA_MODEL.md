# Data Model

Two stores, split by access pattern:

- **PostgreSQL** — entities, attendance, POS, aggregates. Anything that needs
  joins, constraints, or must be correct forever.
- **MongoDB** — raw detection events. High volume, flexible schema, TTL-expired.

Raw events are disposable; aggregates are permanent. Never build a report that
requires reading raw events older than the TTL.

> The PostgreSQL DDL below has been executed against **PostgreSQL 16.13** — all
> 37 statements apply cleanly. The generated `cup_variance` column, the
> tenant-isolation RLS policy, and the `pos_orders` idempotency constraint were
> each verified behaviourally, not just parsed.

---

## PostgreSQL

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";    -- case-insensitive email

-- ─── Tenancy ────────────────────────────────────────────────────────────────
CREATE TABLE tenants (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL,
  country_code    CHAR(2) NOT NULL,
  timezone        TEXT NOT NULL,              -- IANA, e.g. 'Asia/Karachi'
  plan            TEXT NOT NULL DEFAULT 'pilot',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE branches (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  address         TEXT,
  timezone        TEXT NOT NULL,              -- may differ from tenant
  opens_at        TIME NOT NULL,
  closes_at       TIME NOT NULL,              -- may be < opens_at (past midnight)
  business_day_cutoff TIME NOT NULL DEFAULT '04:00',
  is_active       BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON branches (tenant_id);

-- ─── Users & access ─────────────────────────────────────────────────────────
CREATE TYPE user_role AS ENUM
  ('owner','regional_manager','branch_manager','staff','auditor');

CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  email           CITEXT,
  phone           TEXT,
  full_name       TEXT NOT NULL,
  role            user_role NOT NULL,
  password_hash   TEXT,                       -- null if SSO-only
  is_active       BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, email)
);

-- which branches a user may see (owner/auditor: all, via absence of rows)
CREATE TABLE user_branch_access (
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  branch_id       UUID NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, branch_id)
);

-- ─── Staff & attendance ─────────────────────────────────────────────────────
CREATE TABLE staff (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  branch_id       UUID NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
  user_id         UUID REFERENCES users(id),  -- null if staff has no app login
  employee_code   TEXT NOT NULL,
  full_name       TEXT NOT NULL,
  designation     TEXT,
  nfc_card_uid    TEXT UNIQUE,
  hourly_rate     NUMERIC(10,2),
  joined_on       DATE NOT NULL,
  exited_on       DATE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, employee_code)
);
CREATE INDEX ON staff (branch_id) WHERE exited_on IS NULL;

-- Biometric consent is a first-class record, not a boolean on staff.
CREATE TABLE biometric_consents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  staff_id        UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  granted_at      TIMESTAMPTZ NOT NULL,
  granted_method  TEXT NOT NULL,              -- 'signed_form' | 'in_app'
  document_key    TEXT,                       -- S3 key of the signed form
  revoked_at      TIMESTAMPTZ,
  revoked_reason  TEXT
);

-- Embeddings, never images. Encrypted column; purge on revoke or exit.
CREATE TABLE face_embeddings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  staff_id        UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  embedding       BYTEA NOT NULL,             -- KMS-encrypted 512-d float vector
  model_version   TEXT NOT NULL,
  enrolled_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  purge_after     TIMESTAMPTZ NOT NULL
);

CREATE TYPE punch_source AS ENUM ('nfc','face','manual','auto_close');

CREATE TABLE attendance_punches (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  branch_id       UUID NOT NULL REFERENCES branches(id),
  staff_id        UUID NOT NULL REFERENCES staff(id),
  punch_type      TEXT NOT NULL CHECK (punch_type IN ('in','out')),
  punched_at      TIMESTAMPTZ NOT NULL,
  source          punch_source NOT NULL,
  device_id       UUID,
  confidence      REAL,                       -- face match score, null for NFC
  corroborated    BOOLEAN NOT NULL DEFAULT false,  -- anti-buddy-punch check
  overridden_by   UUID REFERENCES users(id),
  override_reason TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON attendance_punches (branch_id, punched_at DESC);
CREATE INDEX ON attendance_punches (staff_id, punched_at DESC);

CREATE TABLE shifts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  branch_id       UUID NOT NULL REFERENCES branches(id),
  staff_id        UUID NOT NULL REFERENCES staff(id),
  business_date   DATE NOT NULL,
  scheduled_start TIMESTAMPTZ,
  scheduled_end   TIMESTAMPTZ,
  actual_start    TIMESTAMPTZ,
  actual_end      TIMESTAMPTZ,
  worked_minutes  INT,
  late_minutes    INT NOT NULL DEFAULT 0,
  overtime_minutes INT NOT NULL DEFAULT 0,
  status          TEXT NOT NULL DEFAULT 'open', -- open|closed|missed|auto_closed
  UNIQUE (staff_id, business_date, scheduled_start)
);

-- ─── Devices & cameras ──────────────────────────────────────────────────────
CREATE TYPE camera_role AS ENUM
  ('entry_counter','cup_counter','table_occupancy','attendance_terminal');

CREATE TABLE devices (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  branch_id       UUID NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL,              -- 'edge_agent'|'nfc_terminal'|'iot_counter'
  serial          TEXT NOT NULL UNIQUE,
  cert_fingerprint TEXT NOT NULL,
  agent_version   TEXT,
  last_heartbeat  TIMESTAMPTZ,
  status          TEXT NOT NULL DEFAULT 'provisioned' -- provisioned|online|offline|retired
);
CREATE INDEX ON devices (branch_id, status);

CREATE TABLE cameras (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  branch_id       UUID NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
  device_id       UUID NOT NULL REFERENCES devices(id),
  label           TEXT NOT NULL,              -- 'Front door', 'Counter left'
  role            camera_role NOT NULL,
  rtsp_url_secret TEXT NOT NULL,              -- Secrets Manager ARN, not the URL
  -- normalised 0..1 coords so config survives resolution changes
  counting_line   JSONB,                      -- {"a":[x,y],"b":[x,y],"band":0.06}
  roi_polygon     JSONB,
  is_active       BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE tables_layout (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  branch_id       UUID NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
  camera_id       UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
  table_code      TEXT NOT NULL,
  seats           INT,
  polygon         JSONB NOT NULL,
  UNIQUE (branch_id, table_code)
);

-- ─── POS ────────────────────────────────────────────────────────────────────
CREATE TABLE pos_integrations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  branch_id       UUID NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
  provider        TEXT NOT NULL,              -- 'petpooja'|'posist'|'square'|'custom'
  credentials_ref TEXT NOT NULL,              -- Secrets Manager ARN
  last_synced_at  TIMESTAMPTZ,
  sync_status     TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE pos_orders (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  branch_id       UUID NOT NULL REFERENCES branches(id),
  external_id     TEXT NOT NULL,              -- POS order id — idempotency key
  ordered_at      TIMESTAMPTZ NOT NULL,
  business_date   DATE NOT NULL,
  gross_amount    NUMERIC(12,2) NOT NULL,
  discount_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
  net_amount      NUMERIC(12,2) NOT NULL,
  payment_mode    TEXT,
  cashier_code    TEXT,
  is_void         BOOLEAN NOT NULL DEFAULT false,
  void_reason     TEXT,
  raw             JSONB,
  UNIQUE (branch_id, external_id)             -- makes ingest idempotent
);
CREATE INDEX ON pos_orders (branch_id, ordered_at DESC);

CREATE TABLE pos_order_lines (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id        UUID NOT NULL REFERENCES pos_orders(id) ON DELETE CASCADE,
  sku             TEXT NOT NULL,
  item_name       TEXT NOT NULL,
  quantity        NUMERIC(10,3) NOT NULL,
  unit_price      NUMERIC(12,2) NOT NULL,
  line_total      NUMERIC(12,2) NOT NULL
);
CREATE INDEX ON pos_order_lines (order_id);

-- SKU → beverage category. Editable, never hardcoded in application code.
CREATE TABLE sku_category_map (
  tenant_id       UUID NOT NULL,
  sku             TEXT NOT NULL,
  category        TEXT NOT NULL,              -- 'chai'|'coffee'|'other'|'non_beverage'
  cups_per_unit   NUMERIC(6,3) NOT NULL DEFAULT 1,  -- a "family pack" is not 1 cup
  PRIMARY KEY (tenant_id, sku)
);

-- ─── Aggregates (the permanent record) ──────────────────────────────────────
CREATE TABLE hourly_branch_metrics (
  branch_id       UUID NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
  tenant_id       UUID NOT NULL,
  bucket_start    TIMESTAMPTZ NOT NULL,
  business_date   DATE NOT NULL,
  entries         INT NOT NULL DEFAULT 0,
  exits           INT NOT NULL DEFAULT 0,
  peak_occupancy  INT NOT NULL DEFAULT 0,
  cups_detected   INT NOT NULL DEFAULT 0,
  cups_billed     NUMERIC(10,2) NOT NULL DEFAULT 0,
  cups_chai       INT NOT NULL DEFAULT 0,
  cups_coffee     INT NOT NULL DEFAULT 0,
  cups_unknown    INT NOT NULL DEFAULT 0,
  revenue         NUMERIC(12,2) NOT NULL DEFAULT 0,
  camera_uptime_pct REAL,
  pos_synced      BOOLEAN NOT NULL DEFAULT true,
  PRIMARY KEY (branch_id, bucket_start)
);

CREATE TABLE daily_branch_summary (
  branch_id       UUID NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
  tenant_id       UUID NOT NULL,
  business_date   DATE NOT NULL,
  footfall        INT NOT NULL DEFAULT 0,
  cups_detected   INT NOT NULL DEFAULT 0,
  cups_billed     NUMERIC(10,2) NOT NULL DEFAULT 0,
  cup_variance    NUMERIC(10,2) GENERATED ALWAYS AS
                    (cups_detected - cups_billed) STORED,
  revenue         NUMERIC(12,2) NOT NULL DEFAULT 0,
  orders_count    INT NOT NULL DEFAULT 0,
  voids_count     INT NOT NULL DEFAULT 0,
  voids_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,
  labour_minutes  INT NOT NULL DEFAULT 0,
  labour_cost     NUMERIC(12,2) NOT NULL DEFAULT 0,
  peak_hour       SMALLINT,
  avg_dwell_min   REAL,
  -- data quality: a report that can't say how complete it is, isn't a report
  data_quality    REAL NOT NULL DEFAULT 1.0,  -- 0..1
  is_complete     BOOLEAN NOT NULL DEFAULT true,
  computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (branch_id, business_date)
);

-- ─── Alerts & audit ─────────────────────────────────────────────────────────
CREATE TABLE alerts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  branch_id       UUID REFERENCES branches(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL,  -- cup_variance|camera_offline|pos_desync|
                                  -- zero_footfall|unusual_voids|late_staff
  severity        TEXT NOT NULL,  -- info|warning|critical
  title           TEXT NOT NULL,
  detail          JSONB,
  window_start    TIMESTAMPTZ,
  window_end      TIMESTAMPTZ,
  snapshot_keys   TEXT[],
  acknowledged_by UUID REFERENCES users(id),
  acknowledged_at TIMESTAMPTZ,
  resolved_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON alerts (branch_id, created_at DESC) WHERE resolved_at IS NULL;

CREATE TABLE audit_log (
  id              BIGSERIAL PRIMARY KEY,
  tenant_id       UUID NOT NULL,
  actor_user_id   UUID REFERENCES users(id),
  action          TEXT NOT NULL,   -- 'attendance.override','footage.view', ...
  entity_type     TEXT,
  entity_id       TEXT,
  before          JSONB,
  after           JSONB,
  ip              INET,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_log (tenant_id, created_at DESC);
```

### Row Level Security

Applied to every tenant-scoped table. The ORM sets `app.tenant_id` per request;
the database refuses to return anything else.

```sql
ALTER TABLE daily_branch_summary ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON daily_branch_summary
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
-- repeat for every tenant-scoped table
```

---

## MongoDB — raw events

One collection per family, all TTL-indexed. These exist to be re-scored and
debugged, not to be reported from.

```js
// detection_events — customer, cup, occupancy
{
  _id, tenantId, branchId, cameraId, deviceId,
  type: "customer.entry" | "customer.exit" | "cup.served" |
        "cup.dispensed"  | "table.occupied" | "table.vacated",
  ts: ISODate,                 // event time at the edge, not receipt time
  receivedAt: ISODate,
  trackId: "cam3-8821",
  confidence: 0.87,
  attrs: { direction: "in", cupType: "chai", tableCode: "T4", dwellSec: 412 },
  snapshotKey: "tenant/…/branch/…/2026-09-14/evt_8821.jpg",  // face-blurred
  modelVersion: "cup-det-v4.2",
  schemaVersion: 1
}
db.detection_events.createIndex({ ts: 1 }, { expireAfterSeconds: 2592000 }) // 30d
db.detection_events.createIndex({ branchId: 1, type: 1, ts: -1 })
db.detection_events.createIndex({ tenantId: 1, trackId: 1 })

// device_health
{ _id, tenantId, branchId, deviceId, ts, cpuPct, gpuPct, tempC, memPct,
  streamsUp: 3, streamsConfigured: 4, bufferDepth: 0, agentVersion }
db.device_health.createIndex({ ts: 1 }, { expireAfterSeconds: 1209600 })  // 14d

// inference_metrics — drift detection input
{ _id, tenantId, branchId, cameraId, ts, modelVersion,
  fps, avgConfidence, confidenceHistogram: [...], detectionsPerMin }
db.inference_metrics.createIndex({ ts: 1 }, { expireAfterSeconds: 7776000 }) // 90d
```

### Idempotency of events

Edge replay after an outage will resend events. Every event carries a
deterministic `eventId = sha256(deviceId | type | ts | trackId)`; ingest upserts
on it. Without this, a 4-hour outage recovery doubles a day's footfall.

---

## Retention

| Data | Retention | Enforced by |
|---|---|---|
| Raw detection events | 30 days | Mongo TTL index |
| Snapshots (S3) | 30 days default, 90 max | S3 lifecycle rule |
| Device health | 14 days | Mongo TTL |
| Inference metrics | 90 days | Mongo TTL |
| Hourly aggregates | 13 months | Partition drop job |
| Daily summaries | Indefinite | — |
| Attendance & POS | Per local labour/tax law (typically 7 years) | — |
| Face embeddings | Deleted within 30 days of exit or consent revocation | Nightly purge job on `purge_after` |
| Audit log | 7 years, append-only | — |

The face-embedding purge job is the one that carries legal weight. Write a test
for it.
