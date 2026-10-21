# Fusion Logics parity (21 October 2026)

The college's old ERP (Fusion Logics, Oracle APEX) was audited in report FL-AUDIT-20261020. Its Appendix A lists
264 pages and forms, 20 workflows and 21 findings. The gap report matched every one against OQC; this file records
what was built to close the gaps. The stack is unchanged (FastAPI, SQLAlchemy, Jinja, Alpine, Tailwind).

## Pages the old ERP had (step 1)

| Old ERP | OQC | Where |
|---|---|---|
| Dashboards › Class Management | Class Management Dashboard: status tiles, completion and attendance rates, late and short classes, day-wise, teacher-wise, supervisor-wise, course-wise and start-time charts, teacher summary | `/dashboards/class-management` |
| Dashboards › Supervisors Management | One row per supervisor: teachers, verified, active students, classes, completion, QA reviews and average, complaints | `/dashboards/supervisors` |
| Dashboards › Daily Performance Dashboard | One day by department (Academics, Marketing, Billing, HR) against the day before; teacher-wise table | `/dashboards/daily-performance` |
| Class Management › Student Attendance Monitoring | Attendance per student over a window; flags under a threshold or three absences in a row; supervisors see their own teachers' students | `/classes/attendance-monitoring` |
| Question Bank › CSV Upload | CSV/Excel upload on the Questions Bank page (and a "Question Bank" sheet in Data Migration) | `/academics/config/question-bank` |
| student-detail-print | Printable student detail | `/students/{id}/print`, printer icon on class rows |
| subscription-day-wise-report | Every day of a month for one subscription | `/subscriptions/{id}/day-wise`, calendar icon on class rows |
| Header › Add to Favorite | ☆ in the header, Favourites menu, `/favourites` | header |

Figures: completion = done ÷ classes with an outcome (done, missed, absent, leave); attendance = present ÷ (present +
absent). Pending, cancelled, rescheduled and free classes are left out of both.

## Screens that worked differently (step 2)

- **QA configuration** is three pages again, as in the old ERP: Review Parameters, Issue Types and the new **Action
  Types** (`/qa/config/...`). A corrective action can carry an action type; its "due in days" sets the due date.
  Five action types are created by the migration.
- **Generate classes**: the Class Schedules button (it was labelled "Generate sessions"). Classes are also generated
  automatically once a day, 14 days ahead.

## Integrations (step 3)

| Integration | How it works | Set up |
|---|---|---|
| Biometric attendance machine | ZKTeco-style machines in ADMS push mode post to `/iclock/cdata`; only registered, active serial numbers are accepted. Or upload the machine's exported log. First punch of the day = check-in, last = check-out; late minutes by the payroll rule; days HR corrected, leave and holidays are left alone | HR › Time and Attendance › Attendance Machine List: register the machine, link each employee to their machine user ID |
| Currency rates | Daily feed from the ExchangeRate-API open endpoint (no key); manual rates are left alone; failures change nothing | Accounts › Setup › Currency Rates › Auto Currency Rate List. Another provider: Integrations › fx › `url` |
| Zoom | "Get Meeting URL" on the teacher's Online class page creates a meeting hosted by the teacher with cloud recording; "SYNC Calls" on Call Recordings pulls every teacher's cloud recordings into the QA pipeline (stored once, mapped to the class) | Integrations › Zoom: `account_id`, `client_id`, `client_secret` of a Server-to-Server OAuth app (scopes meeting:write:admin, recording:read:admin) |
| SMS | `sms` notification channel. Twilio, or any HTTP gateway whose URL takes the number and text | Integrations › SMS: `provider` = `twilio` (`account_sid`, `auth_token`, `from`) or `http` (`url` with `{to}`, `{text}`, `{sender}`; `method`; `sender`) |

Without credentials Zoom and SMS run in simulation, as WhatsApp and e-mail already did.

## The owner's decisions (step 4)

Two Branch Properties (Configuration › Branch Properties), both **No** by default, so nothing changes until the
owner chooses:

- **Exports for admins only** (`exports_admin_only`, FL-D-08): when Yes, Download, Export CSV and Subscriptions on
  the interactive reports are only for admins (System Administrator, HOD Technology, Super Admin) and the CEO. The
  Excel file and subscriptions are refused on the server; the CSV is built in the browser from the rows on screen, so
  for it the control is that the buttons are not shown.
- **Post invoices to the general ledger when issued** (`post_invoices_on_issue`): when Yes, an issued invoice posts
  Dr Accounts Receivable (1100) / Cr Tuition Revenue (4000); the receipt then posts Dr Bank / Cr Accounts Receivable
  for the part applied to such invoices; cancelling the invoice reverses its journal. When No, receipts post Dr Bank
  / Cr Tuition Revenue as before.

The old ERP's 41 role names are not in the audit report (only their count). OQC keeps its 23 roles; any old role can
be added on Human Resource › Users & Access › Roles once the college sends the list.

## Findings closed (step 5)

- FL-D-15: requester forms never set approver fields; the server sets status "pending" whatever is posted.
- FL-D-17: the Secret Pin Code is entered in a password field, stored as a bcrypt hash and cannot be revealed, only
  replaced (`company_config.verify_secret`). The migration hashes an existing pin.
- FL-D-18: Change password shows its three rules, ticking them as you type, and has a Cancel.
- FL-D-20: area home cards are drawn once (checked on every area home).

Tests: `tests/test_fusion_gap_pages.py`, `tests/test_fusion_parity_step2.py`, `tests/test_fusion_integrations.py`,
`tests/test_fusion_decisions.py`.
