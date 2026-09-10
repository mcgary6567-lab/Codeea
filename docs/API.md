# REST API

The platform exposes a JSON API for mobile clients, the PWA, automation tools (n8n, Zapier, Make) and any internal
service that needs to read or write data.

| | |
|---|---|
| Base path | `/api/v1` |
| Interactive docs | `/api/docs` (Swagger UI) · `/api/redoc` |
| OpenAPI schema | `/api/openapi.json` |
| Liveness probe | `GET /health` (no auth) |
| Content type | `application/json` on request and response |

Every endpoint is guarded by the same permission strings as the web console, and every mutation is written to the
audit trail with the acting user's identity.

---

## 1. Authentication

There are two ways to authenticate. Both resolve to a **user**, and that user's permissions decide what the call can
do. There is no anonymous write access anywhere.

### 1.1 Bearer token — for interactive clients

`POST /api/v1/auth/login` exchanges credentials for a short-lived JWT. Use this for the mobile app, the PWA and
anything acting on behalf of a person.

```bash
curl -sX POST https://os.onlinequrancollege.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin@oqc.local","password":"Admin@12345"}'
```

```json
{ "access_token": "eyJhbGciOiJIUzI1NiJ9...", "token_type": "bearer", "expires_in": 43200 }
```

If the account has two-factor authentication enabled, include the current code:

```json
{ "username": "admin@oqc.local", "password": "Admin@12345", "totp_code": "418302" }
```

Send the token on every subsequent request:

```bash
curl -s https://os.onlinequrancollege.com/api/v1/auth/me \
  -H "Authorization: Bearer $TOKEN"
```

| Response | Meaning |
|---|---|
| `401` | Bad credentials, or a missing / invalid TOTP code |
| `423` | The account is locked out after too many failed attempts |

Token lifetime comes from `ACCESS_TOKEN_MINUTES` (720 by default). Each login creates a session row, so a token can be
revoked from `/admin/security/sessions` or from the user's detail page. `POST /api/v1/auth/logout` revokes the current
session.

### 1.2 API key — for machine-to-machine integrations

Issue keys at `/admin/api`. The raw key is shown **once**, at creation; only a SHA-256 hash and the 12-character prefix
are stored, so a lost key must be replaced rather than recovered.

```bash
curl -s https://os.onlinequrancollege.com/api/v1/auth/me \
  -H "X-API-Key: oqc_live_9f3c2a7b1d4e5f60"
```

`Authorization: ApiKey <key>` is accepted as an equivalent to the `X-API-Key` header.

A key:

- **acts as its owning user** and can never do more than that user can;
- carries optional **scopes**, which narrow the owner's permissions but never widen them;
- can carry an **expiry**, after which every call returns `401`;
- records `last_used_at` on every successful call, so idle keys are easy to spot and retire;
- is revoked immediately when you click Revoke, and the revocation is a critical audit event.

Give every integration its own key. One key per system means one revocation never breaks the others.

### 1.3 IP allowlisting

A user (and therefore any key owned by that user) can be restricted to a list of addresses or CIDR ranges from
`/admin/security/policy`. A request from outside the list is treated as unauthenticated and returns `401`.

### 1.4 Errors

| Status | Meaning |
|---|---|
| `401` | No credentials, expired token, unknown or revoked key, or a blocked source IP |
| `403` | Authenticated but the permission check failed, or the record is out of scope for that user |
| `404` | Not found — also returned instead of `403` where existence itself is sensitive |
| `422` | Request body failed validation (FastAPI's field-level error list) |
| `423` | Account locked |
| `500` | Unhandled server error; the incident is logged |

Errors return `{"detail": "..."}`.

---

## 2. Endpoint overview by module

Full request and response schemas are in `/api/docs`. This is the map.

### Auth — `/api/v1/auth`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/login` | Exchange credentials (plus TOTP) for a bearer token |
| `POST` | `/logout` | Revoke the current session |
| `GET` | `/me` | Current identity: id, email, name, role, portal, permission list, 2FA state |

`/auth/me` is the endpoint to hit when verifying that a new key or token works.

### System administration — `/api/v1/system`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/status` | Platform health: version, environment, database engine, job and integration state |
| `GET` | `/users` · `/users/{id}` | User directory |
| `GET` | `/users/{id}/permissions/{permission}` | Evaluate one permission for one user, with the reason chain |
| `GET` | `/roles` · `/permissions` | Role catalogue and the full permission string list |
| `GET` | `/settings` | Configuration keys and values |
| `GET` | `/integrations` | Provider status, health and today's counters |
| `GET` | `/audit` | Query the audit trail (filterable by actor, module, action, severity, date) |
| `GET` | `/backups` · `POST /backups` | List archives; trigger a backup |
| `GET` | `/migration-jobs` | Legacy import jobs and their counts |
| `POST` | `/webhooks/inbound/{source}` | Generic inbound webhook receiver (see §3.1) |

### People — `/api/v1/people`

Clients and their students, students with status transitions, teachers and their rosters, and the teacher-matching
engine (`GET /teacher-match`, `GET /students/{id}/matches`).

### Classes — `/api/v1/classes`

Schedules and session generation, class sessions with status transitions and attendance, per-session AI analysis,
daily counters, per-teacher statistics and QA reviews.

### Academics — `/api/v1/academics`

Courses, divisions and packages, the curriculum tree, per-student progress and recommendations, lesson plans and
delivery, evaluations, monthly tests (generate → score → deliver), improvement and syllabus-compliance reports,
certificates and public certificate verification (`GET /certificates/verify/{certificate_number}`).

### CRM — `/api/v1/crm`

Leads with stage transitions and conversion, closer KPIs, marketing and campaign metrics, trials, cases with SLA
trends, feedback, referrals, retention risk scoring and conversation threads.

### Finance — `/api/v1/finance`

Subscriptions, invoices and invoice generation, payments and the payment-gateway webhook, per-client ledger, expenses
and currency rates.

### HR — `/api/v1/hr`

Employees and their KPIs, attendance check-in, leave requests and decisions, payroll runs with approval and payslips,
teacher cost analysis, grades, training, violations, the recruitment pipeline and grievance summaries.

### Operations — `/api/v1/ops`

Tasks and projects, KPI definitions with history and value posting, the decision register, daily reports and the
command-centre roll-up.

### Webhook receivers — `/api/v1/webhooks`

Provider-specific inbound endpoints; see §3.1.

---

## 3. Webhooks

### 3.1 Inbound — providers calling us

Point each provider at its endpoint. Every received payload is stored as an inbound `WebhookDelivery` so it can be
inspected and replayed from `/admin/integrations`.

| Provider | Endpoint | Notes |
|---|---|---|
| WhatsApp Cloud API | `GET/POST /api/v1/webhooks/whatsapp` | `GET` answers Meta's `hub.challenge` verification handshake; `POST` carries messages and status callbacks |
| GoHighLevel | `POST /api/v1/webhooks/ghl` | Contact and opportunity events |
| Meta Lead Ads | `POST /api/v1/webhooks/meta-lead` | Lead form submissions become CRM leads |
| n8n | `POST /api/v1/webhooks/n8n` | Automation callbacks |
| Payment gateway | `POST /api/v1/finance/payment-webhook` | Payment succeeded / failed / refunded |
| Generic | `POST /api/v1/system/webhooks/inbound/{source}` | Anything else; `{source}` is recorded on the delivery |

Each receiver verifies the provider's signature or the shared secret configured on
`/admin/integrations/<provider>` **before** parsing the body. Receivers are idempotent — replaying the same payload
does not create duplicate records.

Always return quickly. If a provider does not get a `2xx` inside its own timeout it will retry, and you will process
the event twice.

### 3.2 Outbound — us calling you

Create an endpoint at `/admin/api?tab=webhooks`, subscribe it to one or more events (or `*` for all) and set a signing
secret.

**Event catalogue**

| Event | Fires when |
|---|---|
| `lead.created` | A new lead enters the pipeline |
| `lead.converted` | A lead converts to a client |
| `class.status_changed` | A class session starts, completes, is missed or is rescheduled |
| `invoice.issued` | An invoice is issued to a client |
| `payment.received` | A payment is recorded |
| `case.opened` / `case.resolved` | A complaint or request is opened / resolved |
| `student.enrolled` / `student.cancelled` | A subscription becomes active / is cancelled |
| `monthly_test.delivered` | A monthly result card is delivered |
| `referral.qualified` | A referral qualifies for credit |
| `qa.review_completed` | A QA review is completed |
| `ai.flag_raised` | AI monitoring raises a flag |
| `system.test` | The **Send test event** button in the admin console |

**Request**

```http
POST https://n8n.example.com/webhook/oqc
Content-Type: application/json
User-Agent: OQC-Webhook/1.1
X-OQC-Event: lead.created
X-OQC-Delivery: 4821
X-OQC-Signature: sha256=9c1185a5c5e9fc54612808977ee8f548b2258d31

{
  "event": "lead.created",
  "sent_at": "2026-09-09T10:15:00Z",
  "data": { "id": 412, "full_name": "Bilal Yusuf", "email": "bilal@example.com", "stage": "new" }
}
```

**Verifying the signature** — HMAC-SHA256 of the raw request body, keyed with the webhook's secret:

```python
import hmac, hashlib

def valid(raw_body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

Compare against the **raw bytes**, before any JSON parsing or re-serialisation.

**Delivery semantics**

- Respond with any `2xx` within 10 seconds. Anything else counts as a failure.
- Failures retry with exponential backoff: 2, 4, 8, 16, 32 and 64 minutes.
- After six failed attempts the delivery is marked **dead** and stops retrying.
- Dead and failed deliveries can be replayed by hand from `/admin/api?tab=deliveries`.
- Delivery is **at-least-once**. Deduplicate on `X-OQC-Delivery` if a repeat would cause harm on your side.

---

## 4. Rate limits

| Layer | Limit | Behaviour on breach |
|---|---|---|
| Per API key | `rate_limit_per_minute` on the key (default 120) | Set when the key is issued and enforced at the edge; visible on `/admin/api` |
| Login endpoints | 10 requests/minute per IP (`/login`, `/api/v1/auth/login`) | `429` from nginx |
| General API | 120 requests/minute per IP, burst 60 | `429` from nginx |
| Account lockout | 5 failed sign-ins locks the account for 15 minutes | `423` and a `lockout` security incident |

The per-IP limits are the `limit_req` zones in the reference nginx configuration in `DEPLOYMENT.md`; adjust them there
if a legitimate integration needs more headroom. The per-key limit is the contract you agreed with that integration —
raise it on the key rather than removing the zone.

Be a good citizen regardless of the ceiling: page through list endpoints rather than requesting everything, cache
reference data (courses, packages, currencies) instead of re-fetching it per record, and back off on `429` rather
than retrying immediately.

Outbound calls the platform makes to third parties are governed by each provider's own limits, which are documented
on that provider's page in the integration hub.

---

## 5. Conventions

**Pagination.** List endpoints take `page` (1-based) and `per_page`, and return the items alongside the total count.
Defaults are chosen per endpoint; do not assume you received everything.

**Dates and times.** Dates are `YYYY-MM-DD`. Timestamps are ISO-8601 in **UTC**. Display in the user's timezone on
your side — every user and client record carries one.

**Money.** Amounts are decimals with the currency code alongside. Convert to the base currency with
`GET /api/v1/finance/currencies`, which returns `rate_to_base` per currency (`amount_in_base = amount * rate_to_base`).

**Identifiers.** Records carry both a numeric `id` and a human code (`C-00001` clients, `S-00001` students,
`T-00001` teachers, `E-00001` employees, `INV-YYYY-NNNNN` invoices). Key your integration on the numeric id and show
the code to humans.

**Record scoping.** The API applies the same record-level rules as the web console. A key owned by a teacher sees only
that teacher's students and classes; a key owned by a parent sees only their own family. If an integration needs a
broad view, own its key with a service account that holds the right role.

**Audit.** Every write through the API is audit-logged with the acting user, the source IP and the before/after
snapshot, exactly as if it had been done in the console. Consequential actions still require a rationale — pass it in
the request body where the endpoint asks for one.

---

## 6. Worked examples

**Check a key works**

```bash
curl -s https://os.onlinequrancollege.com/api/v1/auth/me -H "X-API-Key: $OQC_KEY" | jq
```

**Platform status**

```bash
curl -s https://os.onlinequrancollege.com/api/v1/system/status -H "X-API-Key: $OQC_KEY" | jq
```

**Create a lead from a website form**

```bash
curl -sX POST https://os.onlinequrancollege.com/api/v1/crm/leads \
  -H "X-API-Key: $OQC_KEY" -H "Content-Type: application/json" \
  -d '{
        "full_name": "Bilal Yusuf",
        "email": "bilal@example.com",
        "phone": "+923001234567",
        "country": "Pakistan",
        "student_name": "Hamza",
        "student_age": 9,
        "source": "website"
      }'
```

**Move a lead down the pipeline**

```bash
curl -sX POST https://os.onlinequrancollege.com/api/v1/crm/leads/412/stage \
  -H "X-API-Key: $OQC_KEY" -H "Content-Type: application/json" \
  -d '{"stage":"trial_scheduled","note":"Trial booked for Saturday 10:00 PKT"}'
```

**Today's class sessions for one teacher**

```bash
curl -s "https://os.onlinequrancollege.com/api/v1/classes/sessions?teacher_id=7&date=2026-09-09" \
  -H "Authorization: Bearer $TOKEN" | jq '.items[] | {id, student, start_time, status}'
```

**Mark a session done**

```bash
curl -sX POST https://os.onlinequrancollege.com/api/v1/classes/sessions/2301/status \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"status":"done","minutes_attended":42}'
```

**Record a payment**

```bash
curl -sX POST https://os.onlinequrancollege.com/api/v1/finance/payments \
  -H "X-API-Key: $OQC_KEY" -H "Content-Type: application/json" \
  -d '{
        "client_id": 19,
        "invoice_id": 131,
        "amount": "90.00",
        "currency": "GBP",
        "method": "card",
        "reference": "ch_3PqX1a2b",
        "received_at": "2026-09-09"
      }'
```

**Query the audit trail**

```bash
curl -s "https://os.onlinequrancollege.com/api/v1/system/audit?module=finance&severity=critical&start=2026-09-01" \
  -H "X-API-Key: $OQC_KEY" | jq '.items[] | {created_at, actor_name, description, rationale}'
```

**Trigger a backup**

```bash
curl -sX POST https://os.onlinequrancollege.com/api/v1/system/backups \
  -H "X-API-Key: $OQC_KEY" -H "Content-Type: application/json" \
  -d '{"rationale":"Pre-deployment snapshot from CI"}'
```

**Receive a webhook in n8n and verify it** — add a Function node before anything else:

```javascript
const crypto = require('crypto');
const raw = JSON.stringify($json.body);
const expected = 'sha256=' + crypto.createHmac('sha256', $env.OQC_WEBHOOK_SECRET).update(raw).digest('hex');
if (expected !== $json.headers['x-oqc-signature']) { throw new Error('Bad signature'); }
return items;
```
