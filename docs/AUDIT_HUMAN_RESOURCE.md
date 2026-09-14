# Functional audit — Human Resource area of the existing ERP

Source: the college's live portal (Oracle APEX, confido360), Human Resource section, audited 14 September 2026.
Only page structure, field labels, filters and actions are recorded. No staff personal data is copied.

Companion to `AUDIT_ACADEMICS.md`. Status legend: **Have** = exists in the new platform ·
**Partial** = exists with different fields or flow · **Missing** = not built yet.

---

## Level 1 — Human Resource Dashboard

Three counter panels, then eight cards.

**Gender Distribution** — Total Male · Total Female · Total Employee.

**Today's Attendance Status** — one block per shift (Morning, Night), each showing Total · Absent · Present ·
On Leave · Late Coming. Every number links to the attendance list filtered by shift and attendance type.

**Pending Requests** — Attendance Change · Complaints · Leave Request · Emp Requests · Violations · Advances ·
Bonuses. Each links to its list filtered on Pending.

| # | Card | Leaf pages |
|---|---|---|
| 1 | Dashboards | Employee Management · Attendance Management · Financial Management |
| 2 | Employment Management | Employee Record · Notifications · Employee Requests · Staff Violations · Staff Bonuses · Downloads · Tasks · Complaints · Advance Requests |
| 3 | Time and Attendance Management | Daily Attendance · Attendance Summary · Employees Progress Sheet · Leave Assignment · Attendance Change Requests · Leave Management · Attendance Report |
| 4 | Recruitment and Hiring | Job Requisitions · Job Applications · Interview Panels · Schedule Interviews · Candidate Database · Onboarding · Summary |
| 5 | Benefits Management | Grades & Allowances |
| 6 | Financial Management | Payroll |
| 7 | Attachments | ERP Attachments List |
| 8 | HR Configurations | Departments · Shift · Holidays · Users · Violation Types · Staff Bonus Types |

## Level 2 — shared conventions

Every request list carries the same **Approval Status** tiles (Pending · Approved · Rejected · Cancelled) and a
**Change Status** action, exactly as the academics-side request lists do. Rows are colour-highlighted by status
through the report's own highlight rules (for example pending in yellow, rejected in red).

## Level 3 — page by page

### Employment Management

**Employee Record** (Employee List) — actions **Apply Bulk Update** and **Create**; highlight rules on Shift
Colour and on Status = in-active. Columns: ID · Name · Designation · Type · Employment Type · Status ·
Department · Gender · Date Of Birth · Email · Cell · WhatsApp · Shift Name · Shift Code · Basic Salary ·
Joining Date · Leaving Date · Father Name · Mother Name · Religion · Blood Group · Bank Name ·
Bank Account No · Manager · Check In Time · Check Out Time · Duty Hours · Leaving Reason · Remarks.

**Employee Requests** — a general-purpose staff request. Columns: ID · Employee · Shift · Date · Req Type ·
Description · HR Remarks · Status.

**Staff Violations** — Create, Reset. Columns: ID · Employee · Shift · Penalty Description · Penalty Amount ·
Remarks · Created By · Created At · Status. The penalty description is chosen from the Violation Types
catalogue, which carries the standard fine for each offence.

**Staff Bonuses** — Create, plus an Acceptance Date filter. Columns: ID · Emp Name · Shift · Bonus Type ·
Bonus Type Status · Bonus Amount · Remarks · Created At · Update Date · Acceptance Date · Status.

**Advance Requests** — salary advances. Columns: ID · Employee · Shift · Request Date · Amount ·
No Of Installments · Remarks · Hr Remarks · Status.

**Complaints** — staff-raised complaints, with tabs **Non Secret** and **Secret**. Columns: ID · Employee ·
Complaint Type · Title · Description · Admin Response · Status · Secret · Created At.

**Downloads** — HR documents published to staff. Columns: ID · Description · Link · Status; Create.

**Notifications** and **Tasks** reuse the platform-wide lists.

### Time and Attendance Management

**Daily Attendance** — an editable grid (Edit / Save / Add Row / Reset, Grid Settings) with highlight rules for
Absent, Late Coming, Time Shortage and Leave. Columns: Type · Date · Details · Login Time · Logout Time ·
Late Coming · Duration · Shortage. Alongside it, three pick-lists of staff for the day — Absent, On Leave and
Present — each entry labelled `code-name-department-shift`.

**Employees Progress Sheet** — what each employee did that day. Columns: ID · Employee · Working Date ·
Progress Detail · Manager Rating.

**Leave Assignment** — the leave entitlement each employee holds. Create. Columns: ID · Employee ·
Employee Status · Shift · Leave Type · Total Assigned Leaves · Consumed Leaves · Expiry Date · Status.

**Attendance Change Requests** — a member of staff asks to correct a punch. Columns: ID · Employee Name ·
Shift Name · Attendance Date · Old Attendance Type · Old Login Time · Old Logout Time · New Attendance Type ·
New Login Time · New Logout Time · User Remarks · Hr Approval Status · Request Date.

**Leave Management** (Leave Requests) — Create, Reset. Columns: ID · Employee · Employee Status · Shift ·
Request Date · Leave Type · Leave Start Date · Leave End Date · Total Applied · Status.

**Attendance Report** (Attendance Summery Report) — filters From Date · To Date · Employee · Shift ·
Employee Type · Designation · Department, with Search, Reset and **Print Report**.

### Recruitment and Hiring

**Job Requisitions** (Jobs) — saved reports Primary · Summary · Summary Department Wise; Create. Columns:
ID · Job Title · Job Type · Department · Job Categories · Skills · Total Positions · Status · Start Date ·
End Date · Job Location.

**Job Applications** — filters From/To Date · Status · Job Title · Application Type (Profile / Non-Profile) ·
Action (Process); **Add Manually**. Columns: ID · Candidate Name · Father Name · Gender · Department ·
Application Date · Job Title · NIC Number · Cell No, and onward. Application status vocabulary: On-Hold ·
Applied · Initial Selected · Pre-Selected · Marked - 1st Interview · Marked - 2nd Interview ·
Marked - Final Interview · Selected · Rejected · Hired.

**Interview Panels** · **Schedule Interviews** · **Candidate Database** · **Onboarding** · **Summary**.

### Benefits and Financial Management

**Grades & Allowances** — Create. Columns: Grade Name · Description · Status.

**Payroll** — Create. Columns: Month · Description · Status · Detail (opens Payroll Detail). Payroll status
vocabulary: Pending · Generated · Posted · Cancelled.

### HR Configurations

| Page | Columns |
|---|---|
| Departments | ID · Department Name · Status |
| Shift | ID · Shift Name · Shift Type · Shift Code · Start Time · End Time · Status |
| Holidays | Create (calendar of non-working days) |
| Users | branch user list |
| Violation Types | ID · Description · Penalty Amount · Status |
| Staff Bonus Types | ID · Description · Bonus Amount · Status |

**Attachments** — ERP Attachments List, with **Upload Attachment**.

## Vocabularies in use

- **Employee Type**: Academics · Admin · Marketing.
- **Department**: Academics · Admin.
- **Designation**: Academic Excellence · Academic Manager · Accountant · Admission Incharge · Ayyah · CEO ·
  COO · Director · Finance Head · IT Head · Manager · Observer · P&C Head · QA Observer · Supervisor ·
  Supporting Staff · Teacher On-Site · Teacher Remote · Trainer.
- **Shift**: Morning · Night, each with a shift code, start time and end time.
- **Employment Type**: Full Time and others.
- **Attendance type**: Present · Absent · On Leave, with a separate Late Coming flag and a Shortage measure.

## Gap list against the new platform

| Area | Status | What is missing |
|---|---|---|
| HR landing page | **Missing** | No Human Resource home with the gender, today's-attendance-by-shift and pending-request counters |
| Employee record | **Partial** | Father Name, Mother Name, Religion, Blood Group, Bank Name, Bank Account No, Shift Code, Check In / Check Out Time, Duty Hours, Leaving Date, Leaving Reason, employee Type (Academics/Admin/Marketing); no bulk update action |
| Employee Requests | **Missing** | No general staff request with Req Type and HR Remarks |
| Attendance Change Requests | **Partial** | We flag a correction on the attendance row; they keep a request with old and new type, login and logout times and an approval workflow |
| Leave Assignment | **Missing** | No entitlement record (assigned, consumed, expiry) per employee per leave type |
| Employees Progress Sheet | **Missing** | No daily progress note with a manager rating |
| Staff Bonuses | **Partial** | Bonus exists; no approval workflow, no acceptance date, no bonus-type catalogue |
| Advance Requests | **Partial** | Advance exists; no request list with instalments, HR remarks and approval tiles |
| Staff Complaints | **Partial** | We have a confidential grievance channel; they also run an open complaint list with a Secret flag, complaint type and admin response |
| Violation Types | **Missing** | No catalogue of offences with a standard penalty amount |
| Bonus Types | **Missing** | No catalogue of bonuses with a standard amount |
| Holidays | **Missing** | No holiday calendar |
| Grades & Allowances | **Partial** | Salary structures exist; no grade catalogue |
| Downloads | **Missing** | No HR document list for staff |
| Attachments | **Missing** | No general attachment store |
| Recruitment | **Partial** | We have requisitions, candidates and interviews; missing job categories, skills, locations, the ten-step application pipeline, interview panels and the summary report |
| Payroll | **Partial** | Status vocabulary differs: theirs is Pending / Generated / Posted / Cancelled, ours draft / pending approval / approved / paid; theirs carries a free-text description per run |
| Attendance report | **Partial** | Ours has no print view and fewer filters (no designation, employee type or department) |

---

## Status after the build

All eight groups are built, seeded and tested.

**Dashboards** — Employee Management, Attendance Management and Financial Management, with the counters the
ERP shows: gender distribution, today's attendance per shift (total, absent, present, on leave, late coming)
and the pending-request counts.

**Employment Management** — the employee record with their 29 columns and 7 filters, employee requests, staff
violations, bonuses, advance requests, complaints, downloads, provisioning and attachments. Approving a
violation writes the deduction so payroll picks it up; a pending or rejected one never charges a fine.

**Time and Attendance** — daily attendance with inline editing, attendance change requests, the attendance
summary report, leave assignment (entitlements), leave management and the employees progress sheet. Approving
a leave consumes working days, skipping holidays; cancelling or reversing returns them; an over-entitlement
approval is refused unless it is overridden with a reason.

**Recruitment and Hiring** — job requisitions, job applications across the ten-step pipeline, interview panels,
scheduled interviews, the candidate database, onboarding and the summary.

**Benefits, Financial Management, Attachments and HR Configurations** — grades and allowances, the Ustaadh Lab,
payroll runs (create, generate, post, cancel, with posting locking the run), attachments, and the configuration
screens for departments, shifts, holidays, violation types, bonus types and users.

### Deliberate differences from the ERP

| Theirs | Ours | Why |
|---|---|---|
| Complaints carry a Secret flag in the main list | A separate confidential channel, plus a Secret tab gated on its own permission | A flag in a shared list is read by whoever opens the list |
| Whole-staff attendance and leave balances open to anyone who can file their own | Those two pages require the People and Culture permission | A teacher needs their own record, not everyone's |
| Shift Type and Shift Code stored on the shift | Derived from the shift group | Adding columns was out of scope for this pass; the pages say where the value comes from |
