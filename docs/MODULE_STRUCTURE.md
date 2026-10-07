# Module structure — which business module owns what

Decided with the college on 7 October 2026. Every feature lives in the business module that owns the work,
so staff find it where their job is, and Configuration holds only settings that apply to the whole system.

| Module | Owns |
|---|---|
| **Human Resource** | Employees and their records, attendance, leave, recruitment, payroll, benefits, attachments; **Users & Access** (staff sign-ins, roles, the permissions overview, account provisioning); HR configuration (departments, shifts, holidays, violation and bonus types, **Confido Agents**) |
| **Accounts** | Chart of accounts, vouchers, statements; **Currency Rates** and **Payment Gateways** under Setup |
| **Billing Management** | Families' money: clients, invoices, receipts, ledgers, subscriptions, discounts |
| **Online Academics** | Students, classes, evaluation, quality; **Teachers** and **Ustaadh Lab** (teacher development) under Teacher Portal |
| **Employee Self Portal** | What each employee checks about themselves |
| **CRM & Growth** | Leads, WhatsApp inbox, campaigns, automations, tags |
| **Operations** | Tasks, KPIs, governance, reports — work that is not one department's |
| **Configuration** | System-wide settings only: lookups, Setup (branch properties), notification templates, support tickets, WhatsApp numbers, OTP, settings, integrations, API, security, backups, data migration, audit log |

## What moved on 7 October 2026

| Was | Now | Old address |
|---|---|---|
| Configuration › Currency Rates | Accounts › Setup › Currency Rates | `/config/currency-rates` redirects to `/finance/currency-rates` |
| Billing › Currencies (duplicate) | merged into Currency Rates | `/finance/currencies` redirects to `/finance/currency-rates` |
| Configuration › Payment Gateways | Accounts › Setup › Payment Gateways | `/config/payment-gateways` redirects to `/finance/payment-gateways` |
| Configuration › Confido Agents | HR › HR Configurations › Confido Agents | `/config/agents` redirects to `/hr/confido-agents` |
| Configuration › Users, HR › HR Configurations › Users | HR › Users & Access › Users | `/admin/users` and `/hr/config/users` redirect to `/hr/users` |
| Configuration › Roles, Configuration › Roles & Permissions (two lists) | HR › Users & Access › Roles (one list) | `/config/roles` redirects to `/admin/roles` |
| — | HR › Users & Access › Permissions (overview of what each role can open) | new: `/hr/permissions` |
| HR › Employment Management › Provisioning | HR › Users & Access › Provisioning | unchanged |
| HR › Employment Management › Teachers | Online Academics › Teacher Portal › Teachers | unchanged |
| HR › Benefits Management › Ustaadh Lab | Online Academics › Teacher Portal › Ustaadh Lab | unchanged |
| HR › Employment Management › Tasks | Operations › Tasks & Projects only | unchanged |
| HR › HR Configurations › Change Staff Sorting (duplicate) | Online Academics › Academic Configuration only | unchanged |

Old addresses redirect permanently (GET 301, POST 307), so bookmarks, the user guide and integrations keep
working.

**Why each move.** Currency rates and payment gateways decide money, so finance staff own them. Confido
Agents record staff screens, so it is a staff-monitoring tool under HR. User accounts, roles and access are
how HR on-boards and off-boards people. Teachers and the Ustaadh Lab are about teaching quality, which the
academic managers run. Tasks are cross-department work, which is what Operations is for.

## Who can open the moved pages

| Page | Permission | Roles that hold it |
|---|---|---|
| Users, Roles, Permissions | `users.*`, `roles.*` | Super Admin, System Administrator, HOD People & Culture; HR Officer can view users and roles and create or update users |
| Confido Agents | `staff_monitoring.view` / `.configure` (new module) | Super Admin, HOD People & Culture; HR Officer can view |
| Currency Rates | `currencies.view` / `.update` | Finance roles |
| Payment Gateways | `payments.view` / `.configure` (was `settings.*`) | Finance roles |
| Ustaadh Lab | `teacher_dev.*` | Academic and QA heads (moved from HR) |

The deploy applies the new grants to the built-in roles through the role-defaults sync
(`app/services/roles.py::sync_system_roles`): permissions the code added are granted and withdrawn ones
removed, while any change an administrator made by hand stays.

**Delegated administration.** People who hold `users.assign` (the HR head) may give the roles on the
"Roles HR may assign" list without holding every permission inside them. The default list is Teacher,
Supervisor, Academic Coordinator, Billing Representative, Lead Generator, Lead Closer, QA Officer, HR Officer
and External Auditor. A superuser edits the list on HR › Users & Access › Permissions. Super Admin and System
Administrator can never be on it, and Accountant is left off by default because it grants full access to the
books. Any other role still follows the no-escalation rule: you can only give access you hold yourself.
Nobody changes their own role, and family or student logins never receive staff roles.

## Deliberate differences from the college's old ERP

The parity audits (`AUDIT_HUMAN_RESOURCE.md`, `AUDIT_ACCOUNTS_CONFIG.md`) recorded the old ERP's menu, where
Configuration held Currency Rates, Roles, Payment Gateways and Confido Agents, and HR's Employment Management
held Tasks. The college asked on 7 October 2026 to organise by business owner instead; this document
supersedes those menus.

## Rule for new features

Put a new page in the module whose staff do the work. Configuration takes a page only when the setting
changes how the whole system behaves for everyone (a lookup list, a security policy, an integration).
Never add a department's feature to Configuration because it "configures" something.

## Two billing settings corrected on 7 October 2026

- **Late fees are opt-in.** "Late Fee Percentage" (2.5, copied from the old ERP) started being charged
  automatically on every overdue invoice with the 4 October deploy. Charging now also needs
  "Charge Late Fees Automatically" (Configuration › Setup, billing), which is off by default. Late fees
  already charged in production between 4 October and this deploy should be reviewed in Billing › Ledger
  Additions (type Late Fee) and cancelled where the college did not intend them; cancelling reverses the
  ledger line.
- **Posting Lock After Close** now means what its description says: a closed period still accepts
  correcting entries for that many days after it was closed. It no longer refuses back-dated entries in open
  periods, which had blocked payroll for the previous month and back-dated receipts and expenses.
