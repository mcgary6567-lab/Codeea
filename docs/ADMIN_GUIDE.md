# Administrator Guide

Everything a system administrator needs to run the Online Quran College Digital Operating System day to day.
All of it lives under `/admin`, and every screen described here is reachable from the **System** section of the sidebar.

Two roles normally hold these permissions:

| Role | Slug | Typical holder | Scope |
|---|---|---|---|
| Super Admin | `super_admin` | CEO | Everything, including the `is_superuser` bypass |
| System Admin | `system_admin` | Technology lead | Everything except CEO-only confidential data |
| Auditor | `auditor` | External auditor | Read-only: audit log, security centre, reports |

Sign in with `admin@oqc.local` / `Admin@12345` (demo super admin) or `sysadmin@oqc.local` / `Sys@12345`.

> **Impersonation does not exist in this platform, by design.** There is no "log in as this user" button anywhere.
> Administrators support people through the audit trail, session management and password resets, so every action in the
> system stays attributable to the person who actually performed it.

---

## 1. Users — `/admin/users`

The list shows every account on the platform: staff, teachers, parents and students. Filter by search text, role,
department, branch, status (active / deactivated / locked) and 2FA enrolment. The stats row gives you total accounts,
active count, 2FA coverage, locked accounts and the split across the four portals.

### Creating a user

`/admin/users/new`. Email and full name are required; everything else has a sensible default.

- **Leave the password blank** and the platform generates a strong temporary one, shows it once in the flash message
  and forces a change at first sign-in. Copy it before you navigate away — it is not stored in clear anywhere.
- The username is derived from the email address and de-duplicated automatically.
- **Allowed IPs** restricts the account to specific addresses or ranges. Leave it empty for unrestricted access.
- A welcome notification is sent to the new account automatically.

### The user detail page

Tabs cover the profile, permission overrides, sessions, login history, security incidents and the full activity trail.

| Action | What it does | Rationale required |
|---|---|---|
| Edit profile | Name, email, role, department, branch, timezone, language, IP allowlist | Only when the role changes |
| Permission overrides | Grants and denies on top of the role | Yes, always |
| Deactivate / reactivate | Blocks sign-in; deactivating also revokes every live session | Yes |
| Reset password | Issues a temporary password and forces a change | Recommended |
| Unlock | Clears the lockout and failed-attempt counter, resolves the matching incidents | Recommended |
| Reset / enrol 2FA | Clears or re-issues the TOTP secret | Yes |
| Revoke sessions | Signs the user out of one device or all of them | Recommended |

You cannot deactivate your own account. Changing a role is logged as a `role_change` event at warning severity.

### Linked records

The detail page shows whether the account is linked to a client, student, teacher or employee record. Deactivating a
user does **not** delete those records — a teacher who leaves keeps their history for payroll and QA reporting.

---

## 2. Roles and the permission matrix — `/admin/roles`

A role is a named set of permission patterns. The list page shows every role with its portal, user count and how many
of the platform's permissions it grants.

### The matrix

Open a role to get the full grid: one row per module, one column per action
(`view`, `add`, `update`, `approve`, `delete`, `execute`, `export`, `assign`, `configure`). Tick the cells you want.

- The **Row** checkbox at the end of each line selects every action for that module and is stored compactly as
  `module.*`.
- The header checkbox selects everything and is stored as `*`.
- A **rationale is mandatory** — a permission change is a critical, consequential audit event.
- **System roles can be edited but never deleted.** Custom roles can be deleted once no user holds them.
- Clone an existing role from `/admin/roles/new?clone=<id>` when you need a variant.

### Portal

Each role belongs to a portal — `admin`, `teacher`, `client` or `student` — and that decides which sidebar the user
sees after signing in. Moving a role between portals changes the navigation for everyone on it.

### How a permission is actually resolved

1. An inactive account fails every check immediately.
2. `is_superuser` grants everything unconditionally.
3. A matching pattern in the user's `denied_permissions` **always wins**, even over the role.
4. Otherwise the role's patterns are checked, then the user's `extra_permissions`.

Record-level scoping runs *after* the permission check: a teacher holding `students.view` still only sees their own
students, a parent only their own family, a student only themselves.

### Permission test — `/admin/roles/permission-test`

Pick a user and a permission and the platform tells you allowed or denied, plus the numbered reason chain that got it
there and which patterns matched. Use this before arguing about whether someone "should" be able to do something.

### Compare roles — `/admin/roles/compare`

Side-by-side diff of two roles: what only A has, what only B has, and the full grid. Useful before merging two roles or
promoting somebody, and before creating yet another near-duplicate role.

---

## 3. Settings — `/admin/settings`

Six tabs.

**Organisation** — trading name, legal name, base currency, default timezone, contact details and the logo used on
invoices, certificates and result cards.

**Branches** — physical or virtual locations with a code, country and timezone. A branch cannot be deleted while users
are assigned to it.

**Departments** — the seven functional departments with their head of department. The HOD assignment drives approval
routing and departmental dashboards elsewhere in the platform.

**Configuration** — every key in the `settings` table, grouped. Each group saves independently and records the changed
keys plus your rationale on the audit trail. Booleans render as checkboxes, numbers as number inputs, nested objects
are shown read-only because the owning module manages their shape.

**Background jobs** — every job discovered from `app/services/jobs_*.py`, with its interval, last run, status and
result. **Run now** executes a job on demand and logs it. Use this when a nightly job needs to catch up, or to prove a
job works after a deployment.

**Environment** — read-only view of the effective configuration: app environment, masked database URL, engine, video
and AI providers, whether each integration is configured or simulated, session and lockout policy, storage directory.
A default `SECRET_KEY` is highlighted in red — fix that before production.

---

## 4. Notifications — `/admin/notifications`

**Templates** — the subject and body used when an event fires, selected by *event type + channel + language*. If no
template matches, the platform falls back to the literal text supplied by the calling module, so a missing template
never loses a message. Placeholders are `{{ name }}`-style and unknown ones are left visible in the output rather than
silently blanked, so a typo shows up in the delivery log.

**Trigger catalogue** — every automated message the platform can send: what it is, who receives it, on which channels
and which job or module fires it. The right-hand column tells you whether a template exists for that event.

**Delivery log** — every message with its recipient, channel, event, status, attempt count and error text. Filter by
channel, status, event type and date range. Failed and queued messages can be retried individually.

**Consent** — opt-in and opt-out counts per channel. Marketing and broadcast messages honour the opt-out flag;
transactional messages (invoices, receipts, class reminders, safeguarding) are always delivered.

**Broadcast** — send an announcement to a role, a department, all staff, all teachers, all parents or every active
account, on any combination of in-app, email and WhatsApp. A broadcast is a consequential audit event: the audience,
the channels and the recipient count are recorded against your name. External channels cost money and are rate
limited — prefer in-app for routine announcements.

---

## 5. Integration hub — `/admin/integrations`

One card per provider (WhatsApp, GoHighLevel, n8n, Zoom, Google, Meta Ads, Google Ads, payments, SMTP, Slack,
OpenProject, AI, video) showing status, health, calls today and failures today, plus a chart of today's traffic.

- **Run health check** probes every provider and updates the health badges.
- **Reset counters** zeroes the daily call and failure counters.
- Failed outbound deliveries and recent inbound payloads are listed at the bottom and can be replayed.

Open a provider for its detail page:

- **Configuration** — non-secret settings only (IDs, channels, limits). These are stored in the database.
- **Secrets** — listed by environment variable name. They live in `.env`, are never written to the database and are
  never displayed here.
- **Test connection** — sends a real test call when the provider is configured, or records a simulated one when it is
  not. Either way the result is audit-logged.
- **Inbound endpoint** — the URL to give the provider so it can push events to us.
- **Rate limits** — the provider's published limits, so you know what you are working against.

On localhost, and whenever a provider's secrets are missing, calls run in **simulated** mode: the request is recorded
and counted but nothing leaves the server.

---

## 6. API keys and webhooks — `/admin/api`

### API keys

Issue a key with a name, an owning user, a rate limit and an optional expiry and scope list.

- Only a **SHA-256 hash** is stored. The raw key is shown once, in the flash message immediately after creation, and
  can never be recovered. Copy it then, or issue a new one.
- A key **inherits its owner's permissions**. Scopes narrow that down; they never widen it.
- Give every integration its own key so one can be revoked without breaking the rest.
- Revocation is immediate and is logged as a critical audit event.

Send the raw key in the `X-API-Key` header (or `Authorization: ApiKey <key>`) on any `/api/v1` request.

### Webhooks

Point the platform at any HTTPS endpoint and subscribe it to one or more events from the catalogue — or `*` for all.

- Set a **signing secret** and every payload carries an HMAC-SHA256 signature in `X-OQC-Signature`.
- **Send test event** pushes a `system.test` payload so you can verify the endpoint before relying on it.
- Deliveries retry with exponential backoff (2, 4, 8, 16, 32, 64 minutes) and are marked **dead** after six attempts.
  A dead delivery can still be replayed by hand from the delivery log.
- Respond with any 2xx within 10 seconds; anything else counts as a failure.

---

## 7. Security centre — `/admin/security`

The landing page gives you open incidents, failed logins in the last 24 hours, locked accounts, active sessions,
privileged users without 2FA, a 14-day failed-login trend and the current login policy.

**Incidents** (`/admin/security/incidents`) — failed logins, lockouts, 2FA failures, blocked IPs, denied permissions
and suspicious exports. Filter by status, severity and type. Resolving an incident requires a triage note, which is
appended to the incident and stored as the audit rationale. Unlocking an account from its user page automatically
resolves the matching `lockout` and `brute_force` incidents.

**Sessions** (`/admin/security/sessions`) — every issued token with its IP, user agent, creation, last-seen and
expiry. Revoke one session, or revoke every active session platform-wide after a credential leak (your own session is
kept so you are not locked out mid-response).

**Login history** (`/admin/security/logins`) — sign-ins, sign-outs, failed attempts and lockouts, drawn from the audit
trail and searchable by actor, IP or description.

**Access policy** (`/admin/security/policy`) — the lockout threshold and window, session and token lifetimes, the
privileged-role list, the force-2FA switch (with the option to notify every non-compliant user when you save), and
per-user IP allowlists.

**Data masking** (`/admin/security/masking`) — the reference table of which sensitive fields are hidden, who may see
the full value and how access is recorded. Students are minors: outside their own teacher, academics and safeguarding
staff they appear by student code only.

**Retention and erasure** (`/admin/security/retention`) — how long each class of personal data is kept, and the
right-to-be-forgotten workflow. Anonymisation replaces names, contacts and addresses with a coded placeholder while
keeping invoices, payments and ledger entries intact for statutory reporting. It is **irreversible** and requires a
rationale. Audit events, financial records and safeguarding cases are never purged.

**Subject access export** (`/admin/security/export`) — builds a ZIP of everything held about one client family:
profile, students, subscriptions, invoices, payments, notifications and a manifest. Verify the requester is the account
holder first, send it over an authenticated channel, and respond within one calendar month.

---

## 8. Backups and disaster recovery — `/admin/backups`

The page lists every archive with its size, type, status and whether a restore test has passed.

- **Create backup now** writes a full archive and prunes anything beyond the retention count.
- **Test** opens the archive read-only in a temporary directory, counts tables and rows and runs an integrity check.
  The live database is never touched. A passing test stamps the archive as verified.
- **Download** pulls the archive for off-site storage — itself a consequential audit event.
- **Settings** set the retention count, the RPO and RTO targets and the off-site strategy.

> **An untested backup is not a recovery point.** The page warns you when the newest archive has never been tested,
> and when no backup exists at all.

The archive directory sits on the same volume as the application. Copy it off-site every night — a backup that only
exists on the server it protects is not a backup.

**DR runbook** (`/admin/backups/runbook`) is the ordered procedure: detect and declare, freeze writes, pick the restore
point, restore the database (SQLite and PostgreSQL commands are both shown), restore storage, verify, replay and
communicate, post-mortem. It also carries the verification checklist to work through before declaring the incident over.

---

## 9. Data migration — `/admin/migration`

A five-step wizard per file: **upload → map fields → validate → import → reconcile**.

1. **Download the CSV template** for the entity you are importing and reshape the legacy export into it. One file per
   entity.
2. **Upload** the CSV or XLSX. The first row must be the header row. The platform detects the columns and suggests a
   mapping by name similarity.
3. **Map fields** — every required field must be mapped or every row will fail. Required fields show green when mapped
   and red when not.
4. **Validate** — checks required fields, data types, duplicates inside the file and duplicates already in the
   database. Nothing is written. Errors block a row; warnings (mostly duplicates) cause it to be skipped.
5. **Import** — requires a rationale. Each row is imported inside its own savepoint, so one bad row never rolls back
   the batch. Skipped rows are listed and downloadable as an error CSV.
6. **Reconcile** — row counts, success rate and, for invoices and payments, the financial total in the file against
   the total actually imported, with the variance.

**Import in this order:** clients → students → teachers → invoices → payments → leads. Children need their parents to
exist first. Rows whose parent cannot be resolved are skipped and listed, never silently dropped.

**Cutover checklist** (`/admin/migration/checklist`) tracks the full programme across six phases — inventory,
cleansing, dry run, cutover, verify, stabilise. Take a full backup and restore-test it immediately before cutover;
that is your rollback point. Deleting a migration job removes the job record and its error log only — imported
records stay in the database.

---

## 10. Reading the audit log — `/admin/audit`

The audit trail is **append-only**. The application never updates or deletes a row in this table, and the default
retention for audit data is *never purge*.

Every event carries: timestamp, actor, action, module, entity type and id, severity (`info` / `warning` / `critical`),
a consequential flag, a description, an optional rationale, a before/after snapshot and the source IP.

### Filters

Search text (matches description and rationale), actor, module, action, entity type, entity id, severity,
consequential-only, and a date range. The stats row and charts cover the last 30 days: daily volume, busiest actors,
most frequent actions and the split by module.

### The three views

- **Event detail** (`/admin/audit/<id>`) — the description, the rationale, a field-by-field diff of the before and
  after snapshots, the raw JSON snapshots, the actor and other events on the same entity.
- **Entity timeline** (`/admin/audit/entity/<Type>/<id>`) — the complete history of one record, oldest changes at the
  bottom, with each rationale inline. This is what you open when someone asks "who changed this and why".
- **CSV export** (`/admin/audit/export.csv`) — the current filter applied, up to 20,000 rows. The export is itself
  recorded as a consequential audit event.

### What "consequential" means

Approvals, refunds, discounts, permission and role changes, grade and salary changes, schedule overrides, safeguarding
access, exports, backups, anonymisation and legacy imports. These actions demand a **rationale** at the point of
action, and the rationale is what makes the trail useful a year later. An event flagged consequential with no
rationale is called out on the detail page — treat it as a process gap to fix.

### Investigating an incident

1. Start at `/admin/security/incidents` or `/admin/security/logins` to establish when and from where.
2. Filter the audit log by that actor and date range.
3. Open the entity timeline for anything that was changed to see the before and after values.
4. Export the filtered set to CSV for the incident record.
5. Revoke sessions, reset the password and reset 2FA from the user's page if the account is compromised.
