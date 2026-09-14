# Functional audit — Academics area of the existing ERP

Source: the college's live portal (Oracle APEX, "confido360 Release 4.07.11"), Online Academics section.
Only page structure, field labels, filters and actions are recorded here. No student, family or staff data
is copied.

Status legend: **Have** = exists in the new platform · **Partial** = exists with different fields or flow ·
**Missing** = not built yet · **N/A** = tied to the old vendor stack and intentionally not replicated.

---

## Level 1 — Home launchpad

| Card | Notes |
|---|---|
| Online Academics | Audited below |
| Billing Management | Audited in `AUDIT_BILLING.md` |
| Human Resource | Audited in `AUDIT_HUMAN_RESOURCE.md` |
| Employee Self Portal | Separate area |
| Accounts | Audited in `AUDIT_ACCOUNTS_CONFIG.md` |
| Configuration | Audited in `AUDIT_ACCOUNTS_CONFIG.md` |

## Level 2 — Academic Home

Top of page: two counter rows, then 13 cards.

**Client's Pending Requests** (each counter links to the filtered list): Client Complaints · Leave Applications ·
New References · Teacher Change Request · Time Change Request.

**Today's Class Status Summary**: Free Students · Pending Classes · Done Classes · Missed Classes ·
Student Absent · Student On-leave · Cancelled Classes · Rescheduled Classes. Each counter links to the
class schedule filtered by that status.

| # | Card | Leaf pages (level 3) |
|---|---|---|
| 1 | Dashboards | Client Management · Subscriptions (Count) · Subscriptions (Multiple) · Billing Management · Monthly Performance Dashboard · Financial Summary · Monthly Performance Insights |
| 2 | Client Management | Client list · Trial Client list · Online Registrations |
| 3 | Client Requests | Leave Applications · Time Change Requests · Teacher Change Requests · Refer New Contacts · Complaints |
| 4 | Student Management | Student List · Student Referred List · Student Leaves · On Leave Students |
| 5 | Subscription Management | Create Subscription · Faculty Allocation · All Subscriptions · All Subscriptions Value Report · Subscription Detail Report · Cancelled Subscriptions |
| 6 | Class Management | Running Trials · Class Schedules · Class Arrangements · Rescheduled Classes · Class Status Summary · Class Queries · Schedule Summary Report |
| 7 | Billing Management | Invoice List · Receipts · Ledger Additions · Client Ledger Report |
| 8 | Evaluation | Evaluations · Pending Evaluations |
| 9 | Quality Management | QA Dashboard · Client Feedbacks · Call Recordings · Agent Un-Matched Calls · QA Review Queue · Reviewed Calls · Teacher QA Performance · Configurations |
| 10 | Teacher Portal | Class Schedule (online class) |
| 11 | Supervisor Portal | Monitoring Dashboard |
| 12 | HOD Portal | Monitoring Dashboard (academic manager portal) |
| 13 | Academic Configuration | Sessions · Courses · Packages · Define Books · Change Staff Sorting · Invoice Addition List · Invoice Additions Master · Receipt Beneficiary Accounts · Client Academic Groups · MS Team Users · Question Bank · Define Assessment · Clients Users List · QA Feedback Questions |

## Level 3 — page-by-page detail

Columns, filters and actions are listed exactly as labelled in the portal. Every list page also has the standard
report toolbar: search box with "Select columns to search", saved reports (Primary + named reports), Rows selector
(1 / 5 / 10 / 15 / 20 / 25 / 50 / 100 / 1000 / All), Actions menu (columns, filter, sort, highlight, pivot, download,
report settings) and a **Create** button where noted.

### 3.1 Academic Home
- Counter row **Client's Pending Requests**: Client Complaints · Leave Applications · New References · Teacher Change
  Request · Time Change Request. Each links to the request list filtered on status = Pending.
- Counter row **Today's Class Status Summary**: Free Students · Pending Classes · Done Classes · Missed Classes ·
  Student Absent · Student On-leave · Cancelled Classes · Rescheduled Classes. Each links to the class schedule with the
  status pre-filtered.

### 3.2 Client Management
**Client List** — status tiles: Trail · Regular · Drop Out · Black List · On Leave (each a link filter). Action: Create.
Columns: ID · Client Name · Status · Registration Date · Students · Currency · Balance · Fee Recurrence · Email · Cell ·
WhatsApp (View link) · State · Shift Name · Referred By · Status Remarks · Billing Representative.

**Client Form** (detail page): photo, read-only summary (Primary Email · Fee Recurrence Type · Primary Cell · Currency ·
Registration Date · Status · State · Legacy Code · Remarks). Right-hand side tabs open modal editors:
- *Basic Detail*: Client Name* · Registration Date* · Fee Recurrence Type · Status* · Primary Email · Primary Cell ·
  WhatsApp · Country* (list of values) · State · Currency · Opening Balance · Legacy Code · Shift (Morning/Night) ·
  Referred By (list of values) · Remarks · Status Remarks. Buttons: Cancel · Change Log · Apply Changes.
- *Contacts* grid: Contact Type · Contact Detail · Remarks · Status · Log. (Edit / Save / Add Row)
- *Students* grid: Name (link) · Status · Registration Date · Gender · Trial Days · Email · Referred By · D.O.B · Drop Date.
- *Credentials* grid: Credential Type · Login · Password · Remarks · Status.

**Trial Client List** — Create; highlight rule on Shift. Columns: ID · Client Name · Registration Date · Students ·
Currency · Status · Country · State · Shift Name · Referred By · Remarks · Billing Representative · Academic Manager ·
Lead Added By · Lead Add Date · Converted At · Converted By · Lead Closer.

**Online Registrations** — web-form leads. Columns: Reg ID · Guardian Name · Mobile No · Whatsapp No · Country · State ·
City · Street Address · Skype · Email · Zip Code · Referred By · How Came To Us · Extra Query · Status
(values seen: Forward to Verifier · Converted; rows imported from the CRM carry "Source: CRM UI | GHL_ID: …" in Extra Query).

### 3.3 Client Requests
All five lists share: **Approval Status** filter tiles Pending · Approved · Rejected · Cancelled and a **Change Status**
action (select rows → new status + remarks).
- *Leave Applications*: Leave ID · Leave For All · Student · Shift · From Date · To Date · Description · Status · Created At.
- *Time Change Requests*: ID · Client · Shift · Student · Subscription ID · Current Session · New Session · Description ·
  Days · Status · Created At.
- *Teacher Change Requests*: ID · Client · Shift · Student · Subscription ID · Current Teacher · New Teacher ·
  Description · Status · Created At.
- *Refer New Contacts*: ID · Client · Name · Shift · Email · Contact No · Description · Reference Type · Status · Created At.
- *Complaints*: ID · Client · Shift · Complaint Type · Title · Description · Company Response · Status · Created At.

### 3.4 Student Management
**Student List** — status tiles Trail · Regular · Drop Out · Black List · On Leave. Saved reports: Primary Report ·
Free Students List (filters: Family Status in Regular, Trial; Status = Active; Total Subscriptions = 0). Create.
Columns: ID · Student Name · Status · Registration Date · Trial Days · Email · Client Name · State Name · Gender · D.O.B ·
Legacy Code · Client Code · Shift Name · Country Name · Family Status · Total Subscriptions. Student name → Student Form,
client name → Client Form.

**Student Form**: photo, summary (Client Name · Status · Reg. Date · Email · Gender · Trial Days · Date Of Birth),
button Change Log. Side tabs: *Basic Detail* (students grid as above) · *View Subscriptions* grid: Course · Session ·
Teacher · Registration Date · Completion Date · Status · Course Method · Language · Days · Package · Next Due Date ·
Trial Days · Remarks.

**Student Referred List** — From/To Date, pivot of referrals. **Student Leaves** (Create): ID · Client · Student · Shift ·
Leave Apply Date · Leave Start Date · Leave End Date · Leave Reason · Leave Detail · Status. **On Leave Students** — list.

### 3.5 Subscription Management
**Create Subscription** (modal wizard). *Search Parameters*: Student (LOV) · Language · Session Category (30 / 45 Minutes)
· Session Type (Job Time Session …) · Course Method (One on One / Group Class) · Course · Grade · Gender · Status
(default Trial) · Package · Trial Days (default 3) · Remarks · Days (Mon–Sun checkboxes) · Course Book (shuttle).
Three result panels: **Session Times** · **Available Teachers** · **Teacher's Sessions** — pick a slot and a free teacher.
Buttons: Reset · Create Subscription.

**Faculty Allocation** — filters Teacher · Course · Language · Session · Days Mon–Sun · Customer · Student. Columns:
Teacher · Session · Student Name · Course · Language · Registration Date.

**All Subscriptions** — tiles Clients · Students · All Subscriptions · Coming Follow Ups. Filters: Subscription ID ·
Session (48 half-hour slots, labelled with PST and UTC) · Course · Language (Arabic · Chinese · English · French · Japanese
· Pashto · Punjabi · Sindhi · Urdu) · Client · Student · Teacher · Status (Cancelled · Completed · Freeze · Regular ·
Trial). Saved reports: Primary · Employee Wise Summary · Coming Follow Ups.

**All Subscriptions Value Report** — Regenerate Report; reports Primary · Employee Wise Summary · Shift Wise Profit Report.
**Subscription Detail Report** — adds Manager · From/To Date · Country · Currency. **Cancelled Subscriptions** —
From/To Date · Teacher · Supervisor.

### 3.6 Class Management
**Running Trials** — Subscription ID · Client Name · Student · Shift · Teacher · Session · Course · Reg. Date · Trial Days ·
Trial End Date · Language · Days · Add Date.

**Class Schedules** — filters From/To Working Date · Course (16) · Session Category (30 / 45 Minutes) · Session · Status
(Cancelled · Done · Missed · Pending · Started · Student Absent · Student On-Leave · Teacher is Available) · Student ·
Teacher · Course Method · Done By Teacher. Actions: Reschedule · Auto Arrangement · View Absent Teachers.

**Class Arrangements** (substitute cover) — From/To Working Date · Session Category · Session · From Teacher · To Teacher
· Status Active / In-Active. Actions: Class Arrangement · Change Status.

**Rescheduled Classes** — Approval Status tiles + Update Status. Columns: ID · Subscription ID · New Working Date ·
Old Working Date · New Session · Old Session · New Teacher · Old Teacher · Created At · Approved AT · Approved BY ·
Reason · Comments · Status.

**Class Status Summary** — Date · Employee · Session Category · Session · Refresh. Grid teacher × session with highlight
rules duration15 / duration20 / duration25 / duration35 · Late Available · No Activity.

**Class Queries** — ID · Class Attendance ID · Class Query Type (Family want to talk with manager · Facing Tech Issue) ·
Detail · Teacher · Created At · Shift · Status (Pending / Closed).

**Schedule Summary Report** — Employee · Session Category · Print. Columns Sr# · Employee · Free · Total (per session).

### 3.7 Billing Management (academic side)
**Invoice List** — tiles Confirmed · Over All Pending · Cancelled · Draft · Paid. Filters From/To Date · Client · Currency
(30 codes) · Status (Pending · Draft · Paid · Confirmed · Cancelled). Actions Create Single Invoice · Generate Bulk
Invoices. Columns: ID · Client · Shift Name · Invoice Date · Currency · Subs Total · Subs Discount · Subs Tax · Total
Amount · Discount Amount · Tax Amount · Net Amount · B.R (billing representative) · Status · Print · Net Amount LC.

**Receipts** — tiles Confirmed · Over All Pending · Cancelled. Create. Columns: ID · Receipt Date · Client · Shift ·
Currency · Total Amount · L.c Amount · Ref No · Receiver Name · Receiving Destination · Description · Payment Mode ·
Category · Payment Getway · Beneficiary Account · Status · Received Date · B.R · Print. Stripe receipts arrive
automatically.

**Ledger Additions** (Create) — ID · Client · Shift · Currency · Currency Rate · Amount · L.C Amount · Status · Ledger
Addition Date · Ledger Addition Type (Teacher Gift · Leave Discount …) · Reference Employee · B.R · Effect (Add / Minus).

**Client Ledger Report** — Account · From/To Date · Print. Columns Srl · Date · Transaction Type · Description · Amount ·
Balance; footer Previous Balance · Total · In Words.

### 3.8 Evaluation
**Evaluations** — tabs Evaluations / Manual Evaluations, Create, highlight Status = Fail. **Pending Evaluations** — list of
students whose periodic evaluation is due.

### 3.9 Quality Management
**QA Dashboard** — From/To Date. Tiles Total Calls · Unmatched Agent Calls · Total Zoom Calls · Avg QA Score /5.
Panels: QA Progress (Mapped Calls · QA Pending · Reviewed Calls) · QA Issue Breakdown (Engagement · Tajweed Accuracy ·
Adab · Lesson Planning) · Lowest Rated Calls · Teacher Performance · Total Reviews Pending / In Progress / Completed /
Flagged / Rejected · Parameter Wise Teacher Call Ratings (pivot).

**Client Feedbacks** — tiles Total Feedback · Average Rating · Total 5★ · Total 1★&2★. Filters From/To · Client · Feedback
Resource (Manual · App · Web Portal · Client Portal) · Rating 1–5★ · Teacher. Action Create Manual Feedback.

**Call Recordings** — tiles Total · Agent · Teams · Zoom. Action SYNC Calls. Filters From/To · Teacher · Source
(AGENT / TEAMS / ZOOM). Columns: Row Id · Source Name · Platform · Recording Date · Employee · Class Session · Start Time ·
End Time · Durations Minuts · Meeting Status · Recording Url · Created At.

**Agent Un-Matched Calls** — recordings not mapped to a class; action View Mapped Classes.

**QA Review Queue** — tiles Agent · Teams · Zoom · Total Que Calls. Columns: Call Source ID · Call Source Type · Class
Attendance ID · Class · Duration · Start Time · End Time · Review State (Unmapped …) · Platform · Source Name · Review →
"Start Review".

**Reviewed Calls** — filters From/To · Status (Pending · In-Progress · Completed · Flagged · Rejected). Tiles Completed ·
Inprogress · Pendings · Total Reviews · Total 5★ Rated Calls · Total 1★&2★ Rated Calls. Columns ID · Call Source Type ·
Overall Rating · Remarks · Reviewed AT · Status.

**Teacher QA Performance** — From/To · Teacher · Over All Teachers Performance. Columns Teacher · Calls · Reviewed · Avg
Rating · Issues · Critical Issues · Score %.

**Configurations** — QA Review Parameters (rated parameters) · QA Issue Type.

### 3.10 Teacher Portal — Online class
Tiles Total Classes · Regular · Trial · Arrangements. Panel *Class Detail*: Student · Get Meeting URL · ID · Age · Language
· Method · Course · Course Material · Session · Reg. Date · Started Time · Ended Time · Remarks. *Activity Detail*:
Add Class Activity; *Manual Activity*: Page No · Remarks · Save Manual Activity. Grid *Today's Classes* with highlight rules
(Class status · Trial Class · Arrangement · Status started · Status pending). Panel *Client – Student Time Status*.

### 3.11 Supervisor Portal — Monitoring Dashboard
Tiles Pending · Done · Missed · Student Absent · Student On Leave · Cancelled · Reschedule Classes. Panels (Teacher /
Student): Pending Classes · Marked Available · Started Classes · Done Classes · Missed Classes · Absent Students Classes ·
Student On Leave Classes · Upcoming Classes · Class Queries (type + detail) · Cancelled · Upcoming Trials (Time · Teacher ·
Student).

### 3.12 HOD Portal — Academic Manager Portal
Tiles as supervisor. Search Options From/To Date · Employee · Refresh. Tabs Academics · HR · Billing · Team Progress.
Highlight rules duration15mints / duration20 / duration25. Panels: Academics Summary (Status · Count) · Absent Staff ·
Today Trials · Dropped Students · New Student Registration.

### 3.13 Academic Configuration
- **Sessions** (Create): Session Category (30 Minutes …) · Session Label ("07:00 AM - 07:30 AM") · Duration · Start Time ·
  Status · Sort No. 48 half-hour sessions 07:00 AM → 06:30 AM.
- **Courses** (Create): ID · Name · Type (Islamic Courses · Academics Tutoring) · Fee · Attendance Required · Curriculum
  Link · Status. 16 courses: Noorani Qaida · Qaida JTQ (Tajweedi Qaida) · Iqra Book · Quran Recitation · Quran Recitation
  with Tajweed · Quran Memorization (Hifz) · Tajweed Course – Basic Level · Tajweed Course – Advanced Level ·
  Tafseer-ul-Quran · Quran Translation · Islamic Studies – Basic Level · Islamic Studies – Advanced Level · Arabic Language
  · Urdu Language · English Tuition · Math Tuition.
- **Packages** (Create): ID · Description · Minimum Days · Maximum Days (2 Days · 3 Days · 5 Days Package).
- **Define Books**: tabs Internal Books / Public Books; Download Template · Create; ID · Name · Sort No · Status.
- **Change Staff Sorting**: Code · Name · Father Name · Designation · Department · Manager · Status · Shift · Sort No
  (edit order used across teacher lists).
- **Invoice Addition List** (Create): ID · Addition Type (Discount …) · Description · Status.
- **Invoice Additions Master** (Create): ID · Level (Subscription …) · From Date · To Date · Invoice Addition List ·
  Implementation Type (Fixed …) · Addition Type · Amount · Status · Auto Assigned?.
- **Receipt Beneficiary Accounts** (Create): ID · Payment Mode (Online Payment Gateway · Bank · Cash) · Category
  (Stripe · PayPal · UBL · Meezan Bank · Wise …) · Beneficiary Account · Status.
- **Client Academic Groups** (Create): ID · Group Name · Sudo Name · Representative Name · Status (Morning Group /
  Night Group).
- **MS Team Users**: tabs Staff / Clients — Teams account mapping.
- **Question Bank**: list (empty). **Define Assessment**: ID · Book · Title · Passing Marks · Total Marks · Add Date · Status.

### 3.14 Dashboards
- **Client Management**: filters Reg. From/To Date · Client · Country · Shift · Status (Black List · Drop Out · On-Leave ·
  Pass Out · Regular · Trial). Tiles Regular · Trial · On Leave · Passed Out · Dropped Out · Black Listed. Charts: Month
  Wise Registrations · Country Wise · Shift Wise · Status Wise · State Wise · Gateway Wise · Currency Wise · Billing
  Representative Wise · Academic Manager Wise.
- **Subscriptions (Count)**: From/To · Employee · Shift · Status. Tiles Total Regular / Trial / Cancelled / Completed
  Subscriptions. Charts Month Wise · Country wise · Status Wise · Shift wise · Teacher wise · Supervisor wise · Course wise.
- **Subscriptions (Amount)**: same filters + Country; tiles Total Amount Of Regular / Trial / Cancelled / Completed;
  same charts by amount.
- **Billing Management**: From/To · Employee · Country · Shift. Over All Business Volume (regular subscriptions count +
  volume) · Clients Receivables (Opening · Closing · Difference) · Invoices – Confirmed value · Month Payroll Amount ·
  Regular / Cancelled Subscriptions flow charts.
- **Monthly Performance Dashboard**: Start/End Date · Employees · Show All / Academics / Marketing / Billing / HR. Panels:
  Regular · Dropped · Freeze · Completed Subscriptions · Missed Classes · On leave Students · 20 Mints Classes · NO Activity
  update · Leads Generated · Status Wise Leads · Leads Conversion Status Wise · Converted (Lead → Trial) · Converted
  (Trial → Dropped) · Converted (Trial → Regular) · Invoices – Confirmed · Confirmed Receipts (Amount) · Ledger Additions ·
  New Enrollment · In-Active Employees · Change Request · Complaints · Leave Request · Employee Request · Violations ·
  Bonus · Advances.
- **Financial Summary**: filters From/To · Client · Shift · Currency · Billing Group · Country · State · Status. Tiles Total
  Invoices Amount · Total Invoice Clients · Total Pending Amount · Total Outstanding Balance · Total Received Amount ·
  Total Clients · Active Students · Regular Subscriptions · Clients Without Subscription · Exceeded Accounts · Collection
  Efficiency % · Avg Revenue Per Client · Clients Zero Students · Ledger Addition · Ledger Deduction. Charts Monthly Invoice
  Trend · Monthly Collection Trend · Clients Count Based on Currency · Student by Shift · Top 10 OutStanding Clients ·
  Subscription Status Breakdown · Collection Efficiency Trend %.
- **Monthly Performance Insights**: Select Month · Submit · Print. Table "Report vs previous month", Day / Night / Total
  columns, rows: Leads · Trial · Trial Drop · Trial On Leave · Total Fail Trial · Sign Up · Reference · Active · Total In ·
  Dropout · On Leave · Total Out · Current Student · Total Session · Free Session · Teacher · Average Student · Pending
  Family · Total Invoices · Paid Invoices · Fee Recovery % · Pending Fee % · Trial Conversion % · Student Retention % ·
  Trial Dropout Ratio · Income; change column (No Change / up / down).

## Gap list against the new platform

Compiled 13 Sep 2026 after the level-3 audit. Every gap below has since been built and verified; the
"Built as" column names where it now lives. Status of the whole area is now **Have** unless noted.

| Area | Status before | Gap that was closed | Built as |
|---|---|---|---|
| Navigation | Partial | ERP is Home → Online Academics → 13 groups → pages, with request/class counters on Academic Home | Done: `nav.py` groups, `/home/<section>/<group>`, `launchpad/academic_home.html`, `services/erp_home.py` |
| Session slots | Missing | 48 half-hour sessions with category, label, sort no, PST/UTC | Done (model `SessionSlot`); WP-1 pages + seed |
| Academic configuration | Missing | Courses (type, fee, attendance, link), packages (min/max days), books (internal/public), staff sorting, invoice addition list/master, beneficiary accounts, client academic groups, MS Teams users, question bank, assessments | WP-1 |
| Clients | Partial | Fee recurrence, legacy code, opening balance, shift, state, referred by, status remarks, academic manager, group, contacts grid, credentials grid, ERP status tiles (Trial/Regular/Drop Out/Black List/On Leave), trial client list, clients users list | WP-2 |
| Students | Partial | Trial days, legacy code, drop date, referred by, family status, ERP tiles, "Free Students" report, referred list, on-leave list, student form with subscriptions grid | WP-2 |
| Client requests | Partial | Uniform Pending/Approved/Rejected/Cancelled workflow for leave, time change, teacher change, references, complaints; client-portal submission | WP-2 |
| Subscriptions | Partial | Slot, days, language, method, category, trial days, status Trial/Regular/Freeze/Cancelled/Completed, create wizard with available teachers, faculty allocation, value/detail/cancelled reports, coming follow-ups | WP-3 |
| Class management | Partial | Teacher-available status, arrangements (+auto), reschedule approval, status summary grid with duration highlights, class queries, schedule summary (free/total), class activities, running trials | WP-3 |
| Portals | Partial | Teacher online-class page, supervisor panels per ERP, HOD portal | WP-3 |
| Billing | Partial | Invoice statuses Draft/Pending/Confirmed/Paid/Cancelled with subs totals and Net Amount LC, bulk generation with addition rules, receipts (mode, category, gateway, beneficiary, receiver), ledger additions (type, effect), client ledger report with previous balance + in words | WP-4 |
| Dashboards | Partial | Client management, subscriptions (count/amount), billing management, monthly performance, financial summary, monthly insights (Day/Night vs previous month) | WP-4 |
| Evaluation | Have | Evaluations/Manual tabs, pending evaluations queue, assessment link | WP-5 |
| Quality | Partial | Call recordings (Agent/Teams/Zoom) with sync, unmatched calls, review queue, reviews with parameters/issues/1–5 rating and Pending/In-Progress/Completed/Flagged/Rejected, reviewed calls, teacher QA performance, client feedbacks with source, configurations | WP-5 |

### Verification, 13 September 2026

- 622 page loads across seven roles (super admin, teacher, parent, student, supervisor, QA officer, billing
  representative) return 200. Every URL in `app/core/nav.py` resolves to a real page.
- 375 automated tests pass, and the suite is repeatable: running it twice against the same database gives the
  same result.
- Schema change captured in Alembic revision `dee1fc82982b`; a second autogenerate finds no drift.

### Deliberate differences from the ERP

| Their system | Ours | Why |
|---|---|---|
| Client list tile spelled "Trail" | "Trial" | Their label is a typo; the filter accepts both spellings. |
| Statuses stored as numeric ids (`p41_status_id=327`) | Readable values (`?status=drop_out`) | Links stay meaningful; ERP spellings are accepted as aliases. |
| Passwords visible in the client Credentials grid | Masked, revealed only on request and the reveal is written to the audit log | Handling of family credentials has to be accountable. |
| Oracle APEX saved reports per user | Fixed named reports (Primary, Employee Wise Summary, Free Students List, Coming Follow Ups) | Covers the reports actually in use without rebuilding APEX's report designer. |
| Gateway receipts arrive from a live Stripe feed | A sync action that simulates the pull | No payment credentials are configured yet; the flow and data shape are real. |
