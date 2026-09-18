# Parity walk — every area of the existing ERP, card by card

Source: the college's live portal (Oracle APEX, confido360 Release 4.07.11), walked on 17 September 2026
signed in as the CEO, after all six areas had been built. Only page structure, labels, filters and actions
were recorded. No family, student or staff data was copied.

The five audits (`AUDIT_ACADEMICS.md`, `AUDIT_BILLING.md`, `AUDIT_HUMAN_RESOURCE.md`,
`AUDIT_EMPLOYEE_SELF_PORTAL.md`, `AUDIT_ACCOUNTS_CONFIG.md`) were written area by area over four days. This
walk went through every group page of every area in one sitting and compared each card list against our
launchpad, so that anything added to their portal since, or missed the first time, is caught.

> **Reading their sub-menus.** A group page such as `setup` or `client-management` renders its cards only if
> the area home (`accounts-home?p16_id=62`, `online-academic-billing?p275_id=161`, and so on) was opened
> earlier in the same session. Opened cold it shows the heading and an empty list. Every group below was
> read straight after its area home.

---

## Result at card level

| Area | Their groups | Match | Notes |
|---|---|---|---|
| Online Academics | 13 | 13 | Two page-level differences, below |
| Billing Management | 3 | 3 | Identical |
| Human Resource | 8 | 8 | Three page-level differences, below |
| Employee Self Portal | 14 cards | 14 | Identical |
| Accounts | 3 | 3 | Identical |
| Configuration | 10 cards | 9 | One new card, below |

Where ours lists more than theirs, the extra entries are our own functionality, kept on purpose and placed
after theirs in each group.

## Differences found

### Online Academics

- **Dashboards** — their second card reads **Subscriptions (Multiple)**; ours read "Subscriptions (Amount)".
  Relabelled.
- **Client Management** — theirs lists three cards (Client list, Trial Client list, Online Registrations).
  **Clients Users List** has moved to Academic Configuration. Ours moved to match.
- **Academic Configuration** — fourteen cards, two of them new to us: **Clients Users List** (moved here) and
  **QA Feedback Questions**, a catalogue with Create, Go and Actions. The Create form would not open under
  automation, so its fields are modelled from what a feedback form needs: the question, an Urdu rendering,
  the answer type (a 1–5 rating, yes/no, or free text), who it applies to, whether it is required, and the
  sort order. Answers are stored on the feedback itself, keyed by question, so a question can be reworded
  or retired without touching what people already answered.

### Human Resource

- **HR Home** carries four panels above the cards that ours did not: **Gender Distribution** (Total Male,
  Total Female, Total Employee); **Contract Ends (Within 2 months)** as a table of Name, Start Date, End
  Date, Remaining Days; **Today's Attendance Status** per shift (Total, Absent, Present, On Leave, Late
  Coming for Morning and for Night); and **Pending Requests** (Attendance Change, Complaints, Leave
  Request, Emp Requests, Violations, Advances, Bonuses). The first HR audit recorded these but the home
  itself had been left as a plain card page. Contract end needed a column on the employee record.
- **Employment Management** — theirs, in order: Employee Record, **Notifications**, Employee Requests,
  Staff Violations, Staff Bonuses, Downloads, **Tasks**, Complaints, Advance Requests. Notifications is a
  list of staff notices with Link, Title, Start Date, End Date and Status, with Create; the self portal's
  Notifications card shows the live ones as ID, Title, Description, Dated. Tasks is a list with ID, Title,
  Due Date, Created At, First Name and Status, with Create. Ours lacked the notices page and did not list
  tasks in this group. Both added; ours reordered to theirs, with our extras after.
- **Time and Attendance** — theirs, in order: Daily Attendance, **Attendance Summary**, Employees Progress
  Sheet, Leave Assignment, Attendance Change Requests, Leave Management, Attendance Report. The summary has
  the same Search Options as the report (From Date, To Date, Attendance Type, Shift, Employee Type,
  Designation, Department, Employee) and shows a pivot rather than a list, with a button across to the
  report. Ours had the report only. Summary added; order matched.
- **HR Configurations** — theirs, in order: Departments, **Shift**, Holidays, Users, Violation Types, Staff
  Bonus Types. Ours reordered to match and the label made singular; our own two entries follow.
- **Financial Management** — theirs has one card, Payroll. Ours keeps four more after it.

### Configuration

- A tenth card, **Confido Agents**, opens a page titled Recording Agent with three tabs: **User Licenses**
  (tiles Allowed and Consumed, a list, and actions Generate License and Download), **Devices list**, and
  **Agents Screen Shorts** (a filter and a grid of captures). This is the vendor's desktop agent that records
  calls and takes screenshots on a staff machine. We hold the licences, the devices that check in and the
  captures, and expose the two calls the agent makes (a heartbeat and a screenshot upload), keyed by
  licence. The agent program itself is theirs and is not reproduced.
- Their ten cards, in their order: Currency Rates, Roles, Lookups, Setup, Notification Templates, Support
  Ticket, WhatsApp Numbers, OTP Configuration, Payment Gateways, Confido Agents. Ours reordered to match,
  with our own entries after.

## Deliberate differences from the ERP

| Theirs | Ours | Why |
|---|---|---|
| Group pages render empty unless the area home was opened first | Every page renders on its own | Ours has no session-scoped menu state to lose |
| The desktop recording agent is their vendor's program | The licences, devices, captures and the agent's two API calls | The agent binary is not ours to reproduce; everything it reports is held and shown |
| Attendance Summary is an interactive pivot the user edits | A fixed per-employee pivot with a totals row, and a toggle to the flat report | Covers what the pivot is used for without rebuilding the report designer |
| Feedback questions are edited in a form we could not open | The fields a feedback form needs, with Urdu alongside | Recorded as an assumption; adjust once the form is seen |

---

## Status after the build

Everything in the differences list above is built, seeded and tested.

- **HR Home** carries their four panels above the group cards: gender distribution, contracts ending within
  two months with remaining days, today's attendance per shift, and the pending-request counts, every figure
  a link to the page that lists it.
- **Attendance Summary** shows one row per employee with Present, Absent, On Leave, Late Coming, Total
  Sessions and Shortage Hours, a totals row, their eight search options, print, CSV, and a button across to
  the Attendance Report.
- **Notifications** in Employment Management is a staff-notice list with Link, Title, Start Date, End Date
  and Status plus Audience, with live, scheduled, expired and inactive tiles. The self portal's Notifications
  card shows the live ones for the reader's audience as ID, Title, Description, Dated, and creating a live
  notice lights the bell.
- **Contract End Date** sits on the employee form and record beside the join and probation dates.
- **QA Feedback Questions** is a catalogue with answer type, audience, required, sort order and status. The
  feedback forms (QA manual entry, client portal, public survey) ask the active questions in order and keep
  the answers on the feedback keyed by question; the detail page shows each question with its answer.
- **Confido Agents** has their three tabs. Allowed comes from a branch property, Consumed from active
  licences; Generate License refuses past the limit; Download hands over the key; revealing or downloading a
  key is audited; revoking blocks the licence's devices. The devices tab lists what has checked in with
  online, offline and blocked tiles; the screenshots tab is a filterable grid. Two API calls take what the
  desktop agent reports, a heartbeat and a screenshot upload, refusing unknown, revoked or blocked keys, and
  a scheduled job marks a device offline after ten minutes of silence.

**One thing changed after the build.** Screen captures were first served from the open static mount, so
anyone who knew the address could open a capture of a staff member's screen. They are now served only through
a route on the Confido Agents page that checks the permission, and the static path answers 404 even to a
signed-in user.
