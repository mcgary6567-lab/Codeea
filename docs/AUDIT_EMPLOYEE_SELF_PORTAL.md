# Functional audit — Employee Self Portal area of the existing ERP

Source: the college's live portal (Oracle APEX, confido360 Release 4.07.11), audited 15 September 2026
signed in as the CEO. Only page structure, field labels, filters and actions are recorded. No staff, family
or student data is copied.

Fifth and last companion to `AUDIT_ACADEMICS.md`, `AUDIT_HUMAN_RESOURCE.md`, `AUDIT_ACCOUNTS_CONFIG.md` and
`AUDIT_BILLING.md`. With this, every card on their Home screen has been audited.

> Reached from the Home card **Employee Self Portal**, whose address is `ess-home?p90_id=101`. The pages
> below carry no menu id of their own. Two of the fourteen cards are sub-menus that open further pages.

---

## Shape

Fourteen cards, eighteen pages once the two sub-menus are opened. The left-hand navigation lists the same
fourteen, so every page is one click from anywhere in the area.

| Card | ERP alias | Opens |
|---|---|---|
| My Schedule | `online-class` | The teacher's Online Class page, already audited in `AUDIT_ACADEMICS.md` |
| Requests | `employee-requests` | Sub-menu: Leave, Employee Request, Advance Requests |
| Attendance Sheet | `ess-attendance` | Own attendance, with change requests |
| Tasks | `employee-tasks` | Own, assigned and others' tasks |
| Daily Progress Sheet | `emp-progress-sheet` | Own daily progress, rated by a manager |
| Team Management | `team-management` | Sub-menu: Staff Violation, Staff Bonuses |
| Account Ledger | `essledger` | Own ledger |
| Violations | `violations` | Violations recorded against you |
| Bonuses | `bonuses` | Bonuses awarded to you |
| Salary Slips | `salary-slip-list` | Payslip per month, printable |
| Notifications | `ess-notifications` | Broadcast announcements |
| Downloads | `downloads1` | Documents and links HR publishes |
| Complaints | `hr-complaints` | Own complaints |
| My Profile | `my-profile` | Rendered empty for the account audited |

Sub-menu pages: `leave-requests2`, `emp-request`, `employee-advances1`, and the two team pages.

## Dashboard (`ess-home`)

- **Identity panel** — employee code and name, with the designation beneath it.
- **Tasks tile** — Total Pending Tasks, linking to the Tasks page.
- **Fourteen cards**, as above.
- **Today's Attendance panel** — Working Date, Attendance Time shown as a range with the end still open, for
  example `07:10AM -`, and a **Mark Attendance** button. This is how a member of staff clocks in and out.
- **My Team** — the reporting line as an expandable tree, each node an employee code and name, nested to the
  depth of the real hierarchy.

**What this tells us.** The dashboard is a working screen, not a summary: attendance is marked from it, and the
team tree makes the portal a manager's tool as well as an individual's.

## Attendance Sheet (`ess-attendance`)

**Filters** — From Date, To Date, Attendance Type (Absent, On Leave, Present), Go.

**Columns** — Attendance Date, Attendance Type, Login Time, Logout Time, Late Coming, Duration Shortage, Change.

The Change column carries a **Request to Change** button on each row; once raised it reads **Requested** and the
button is gone. Late Coming and Duration Shortage are minutes, negative when early. Saved highlight rules colour
the absent, requested, duration-shortage and late-coming rows.

**What this tells us.** A member of staff sees the same late and shortage figures payroll uses, and disputes a
row from the row itself rather than through a separate form.

## Requests (`employee-requests`)

Three pages, each a plain list with **Create**, and no rows for an employee who has raised none.

**Advance request form** — Request Date, Amount, No Of Installments, Remarks, Hr Remarks. The HR remarks field
is on the form but belongs to whoever decides it.

The leave and employee-request forms follow the same shape; their decision workflow is recorded on the
management side in `AUDIT_HUMAN_RESOURCE.md`.

## Tasks (`employee-tasks`)

**Tiles** — My Pending Tasks, My Pending Assigned Tasks, Others Pending Tasks.

**Tabs** — My Tasks, Assigned Tasks, Others Tasks.

**Columns** — ID, Task Title, Assigned To, Assigned At, Collaborators, Due Date, Comments, Status. Statuses
seen: Draft, Pending, Completed, Cancelled. Collaborators is a list of employees, and Comments is a count.

**What this tells us.** Tasks are three-sided: what you must do, what you gave someone else, and what your team
owes other people. Titles are written in Urdu as often as English.

## Daily Progress Sheet (`emp-progress-sheet`)

**Create**. Columns — ID, Working Date, Progress Detail, Status (Draft, Submitted), Manager Rating.

**What this tells us.** Staff write their own day up and submit it, and a manager scores it. The rating lives on
the same row.

## Team Management (`team-management`)

Two pages, **Staff Violation** and **Staff Bonuses**, for raising each against your own reports. The catalogues
and the approval workflow are the ones recorded in `AUDIT_HUMAN_RESOURCE.md`.

## Account Ledger (`essledger`)

**Filters** — From Date, To Date, Search, **Print**.

**Report** — a titled Ledger Report with the range printed under it, and columns Srl., Date, VID, Description,
Amount Dr., Amount Cr., Balance. VID is the voucher id, which ties a line back to the voucher that created it.

## Violations and Bonuses (`violations`, `bonuses`)

Read-only lists of what has been recorded against, or awarded to, the signed-in employee. Both were empty for
the account audited, so no columns rendered; the fields are the ones on the management side.

## Salary Slips (`salary-slip-list`)

**Columns** — Month, Description, Net Salary, **Print**. One row per payroll month, newest first, each
printable. Description is the payroll run's own description.

## Notifications (`ess-notifications`)

**Columns** — ID, Title, Description, Dated. Broadcast announcements from People and Culture, most written in
Urdu, kept indefinitely.

## Downloads (`downloads1`)

**Columns** — ID, Description, Link, Status. Documents and links HR publishes, each either active or not.

## Complaints (`hr-complaints`)

A list with **Create**, holding the signed-in employee's own complaints.

## My Profile (`my-profile`)

The page renders its heading and nothing else for the account audited.

---

## Gap list against the new platform

Ours is one page, `/hr/me`, with seven tabs (overview, attendance, leaves, payslips, record, development,
grievance), plus Daily Report, Tasks and Profile reached from the same card.

| Their page | Status | What is missing |
|---|---|---|
| Dashboard identity and team tree | **Missing** | No reporting-line tree; a manager cannot see their own team from the self portal |
| Mark Attendance from the dashboard | **Missing** | Staff cannot clock in or out from their own portal |
| Attendance Sheet | **Partial** | We show own attendance; no Late Coming or Duration Shortage column, and no per-row Request to Change |
| Requests: leave, employee request, advance | **Have** | All three exist; leave and employee requests from `/hr/me`, advances from the HR side |
| Tasks, three-sided | **Partial** | We have tasks; not split into mine, assigned by me and my team's, and no collaborators or comment count |
| Daily Progress Sheet | **Partial** | We have daily reports and a manager-rated progress sheet on the HR side; the employee cannot write and submit their own day from the portal |
| Team Management: raise a violation or bonus against a report | **Missing** | A manager cannot raise either from the self portal |
| Account Ledger | **Missing** | Staff have no ledger of their own, so an advance being recovered is invisible to them |
| Violations and Bonuses, read-only | **Have** | Both on the record tab |
| Salary Slips with Print | **Partial** | Payslips are listed; no per-month print |
| Notifications | **Have** | Ours are richer, with read state and per-channel delivery |
| Downloads | **Have** | Built in the HR pass |
| Complaints | **Have** | Plus our confidential grievance channel, which they do not have |
| My Profile | **Have** | Ours works; theirs rendered empty |

## Recommended build order

1. **Mark Attendance, and the attendance row's Request to Change** with Late Coming and Duration Shortage
   shown, because this is what staff open the portal for daily.
2. **The employee ledger**, the only place a member of staff can watch an advance being recovered.
3. **The team tree, and raising a violation or bonus against a report**, which turns the portal into a
   manager's tool rather than only an individual's.
4. **Three-sided tasks and the self-written progress sheet**, then **per-month payslip printing**.

## Deliberate differences from the ERP

| Theirs | Ours | Why |
|---|---|---|
| Fourteen cards, each its own page | One page with seven tabs, plus three linked pages | Fewer clicks for what one person checks about themselves; their card list is mirrored by the tab strip |
| No confidential channel | A grievance channel separate from complaints | A complaint read by your own management chain is not a safe way to raise one about it |
