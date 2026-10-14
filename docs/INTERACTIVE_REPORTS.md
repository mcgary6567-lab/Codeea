# Interactive reports: the toolbar on every list

Built 14 October 2026 at the college's request. It follows the Oracle APEX interactive report the staff used in
the old ERP. The main purpose is to move data out of the old ERP into this one without typing it in by hand.

## What staff see

Every list table (every `ui.table_start`, about 290 of them) gets a toolbar. The main table of a page gets the
full toolbar; other tables on the same page get a compact **Report ▾** menu.

| Control | What it does |
|---|---|
| Search rows | Narrows the rows on screen as you type. |
| Rows | Sets the page size: 10, 25, 50, 100, 500, 1,000 or All (capped at 10,000). Above 2,000 records it asks first. |
| Actions › Columns | Shows or hides columns. Hidden columns are left out of downloads. |
| Actions › Filter | Adds a rule: contains, equals, greater than, at least, starts with, is empty, and so on. Each rule shows as a chip; click it to remove it. |
| Actions › Data | **Sort** by up to three columns; **Aggregate** adds a total line (sum, average, count, count distinct, min, max). |
| Actions › Format | **Highlight** colours rows or cells that match a rule; **Rows per page**. |
| Actions › Chart | Bar, horizontal bar, line, pie or doughnut of a value by a label; downloadable as an image. |
| Actions › Group By | One line per value of one or two columns, with a row count and up to three values. |
| Actions › Pivot | Rows from one column, columns from another, and a value in each cell, with totals. |
| Actions › Report | **Save report** under a name (optionally your default, or shared with everyone who can open the page), **Saved reports**, **Reset**. |
| Actions › Download | The current view as CSV, Excel (.xlsx) or a printable page. |
| Actions › Subscription | E-mails the report as a CSV every day, week or month; "Send now" tests it. |
| Export CSV | **All records** (every page), **Filtered records** (with search and filters), **Selected records** (ticked rows). |
| Import CSV | On the list pages of the ten import sheets: opens the bulk import with that sheet chosen. |
| Checkboxes | One per row; the one in the heading selects every row shown. On a paged list, a link then offers to select all records. |

Settings (columns, filters, sort, highlights, totals) are remembered per page in the browser until **Reset**. The
search text is not remembered.

## Who may do what

- **Staff only.** Families and students do not get the toolbar, and every `/ir/...` endpoint uses `require_staff`.
- **Exports** carry only what the user can see. They read the same page again, with the same filters and the same
  permissions; nothing is fetched from a separate data API.
- **Import CSV** shows only with `migration.add`, the Data Migration permission.
- **Subscriptions** render the page as the subscriber, through an internal request with a session that lives for
  that one request. If the subscriber loses access, the e-mail is not sent and the subscription shows
  "Failed: the page answered 403".
- **Records.** Every export, saved report and subscription is written to the Audit Log.

## CSV import (old ERP → new ERP)

The import engine is Configuration › Data Migration (`app/services/migration.py`). It works in steps:

1. Upload a CSV or XLSX file.
2. Map the columns. The system suggests the mapping.
3. Validate: required fields, dates, numbers, e-mails, duplicates within the file and duplicates already in the
   database.
4. Preview the rows.
5. Import row by row. A bad row is skipped; the rest are imported.
6. Read the summary, and download the error CSV to fix and re-upload the failed rows.

| Sheet | List page with the Import button | Matched on (duplicates) |
|---|---|---|
| Clients / Parents | /clients | email, phone, client code |
| Students | /students | student code, or family + name |
| Leads | /crm/leads (List view) | email, phone |
| Teachers | /teachers | email, teacher code |
| **Employees** (new) | /hr/employees | employee code, email |
| **Subscriptions** (new) | /finance/subscriptions | subscription code |
| **Courses** (new) | Academic Configuration › Courses | course code |
| **Packages** (new) | Academic Configuration › Packages | name + currency + country |
| Invoices | /finance/invoices | invoice number |
| Payments | /finance/receipts | payment number, reference |

**Import order** for a full migration, because each sheet looks up the earlier ones:

1. Courses
2. Packages
3. Employees
4. Teachers
5. Clients
6. Students
7. Subscriptions
8. Invoices
9. Payments

The CSV template for each sheet is on the Data Migration page.

## Large lists

The `?rows=` value reaches every paginated list through `RowsParamMiddleware` (app/main.py) and `paginate()`
(app/core/utils.py). Export CSV › All records fetches pages of 1,000 rows one after another; the audit log's
6,950 records export in about 5 seconds.

Showing several thousand rows on one page is slow in any browser. On the audit log the HTML is 9 MB: about 4 s to
download and about 18 s to lay out. That is why Rows asks first above 2,000 records.

In the browser all DOM changes run inside `Alpine.mutateDom`, because Alpine watches the table. Outside it, hiding
rows on that page took 24 seconds; inside it, a search over 6,974 rows takes 0.3 seconds.

## Code

| Area | Files |
|---|---|
| Browser | `app/static/js/report.js` |
| Markup | `data-ir` on tables and `data-ir-pager` on pagination (`app/templates/macros.html`); staff and import flags on `<body>` (`base.html`, `app/core/templating.py`) |
| Server | `app/web/ir.py` (saved views, Excel/CSV, subscriptions); `app/services/ir.py` (reading a rendered table, applying saved settings, files, sending); `app/services/jobs_reports.py` (runs due subscriptions every 30 minutes) |
| Data | `report_views`, `report_subscriptions` (migration `9d799feea198`) |
| Import | `app/services/migration.py` (`ENTITIES`, `PAGE_ENTITIES`) |
| Tests | `tests/test_interactive_reports.py` |

To leave a table out of the toolbar, pass `report=False` to `ui.table_start`.
