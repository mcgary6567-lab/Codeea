"""Online Academics drawn as the old ERP draws it: its card colours, boxed tile panels and Search Options bar (28 Oct 2026)."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.core.nav import PALETTE
from app.main import app

ERP_COLOURS = ["#309fdb", "#13b6cf", "#2ebfbc", "#3caf85", "#81bb5f", "#dede53", "#fbce4a",
               "#ed813e", "#e95b54", "#e85d88", "#ca589d", "#854e9b", "#5a68ad"]


@pytest.fixture(scope="module")
def admin():
    c = TestClient(app)
    assert c.post("/login", data={"username": "admin@oqc.local", "password": "Admin@12345"}, follow_redirects=False).status_code == 303
    return c


def _cards(html: str) -> list[tuple[str, str]]:
    """(href, band colour) of each module card on the page."""
    return re.findall(r'<a href="([^"]+)" class="oqc-mcard .*?style="background: (#[0-9a-f]{6})"', html, re.S)


def test_palette_is_the_erp_sequence():
    assert PALETTE == ERP_COLOURS


def test_academic_home_has_the_two_boxed_panels_and_thirteen_cards_in_erp_colours(admin):
    html = admin.get("/home/academics").text
    for title in ("Client's Pending Requests", "Today's Class Status Summary"):
        assert f'aria-label="{title}"' in html.replace("&#39;", "'")
    # both tile rows start on the ERP's first colour, as the ERP draws them
    panels = re.findall(r"<section .*?</section>", html, re.S)
    assert len(panels) == 2 and all("background: #309fdb" in p for p in panels)
    cards = _cards(html)
    assert len(cards) == 13
    assert [c for _, c in cards] == ERP_COLOURS
    assert cards[6][0].endswith("/billing")  # Billing Management lands on amber in both systems


def test_sub_module_cards_cycle_the_same_colours(admin):
    cards = _cards(admin.get("/home/academics/classes").text)
    assert [c for _, c in cards][:8] == ERP_COLOURS[:8]


def test_list_pages_carry_the_search_options_bar(admin):
    html = admin.get("/classes").text
    assert "oqc-filters" in html and "Search Options" in html
    form = re.search(r'<form method="get" action="/classes".*?</form>', html, re.S).group(0)
    assert 'name="date_from"' in form and ">Search <" in form


# --------------------------------------------------------------------------- Billing Management
def _titles(html: str) -> list[str]:
    return [re.sub(r"\s+", " ", t).strip() for t in
            re.findall(r'class="oqc-mcard .*?<div class="text-\[17px\][^"]*">(.*?)</div>', html, re.S)]


def test_billing_card_pages_hold_the_erps_cards_in_its_order(admin):
    home = admin.get("/home/finance").text
    assert "Online Academic - Billing" in home
    assert _titles(home) == ["Client Management", "Billing", "Configurations"]
    assert _titles(admin.get("/home/finance/clients").text) == [
        "Clients List", "Clients Financial Summary", "Leads List", "Verify Leads", "Leads Summary"]
    assert _titles(admin.get("/home/finance/config").text) == ["Billing Groups", "Lead Closers"]
    billing = admin.get("/home/finance/billing").text
    assert "<h1" in billing and "Billing Management" in billing
    erp, _, more = billing.partition("More in OQC")
    assert _titles(erp) == ["Invoices", "Receipts", "Ledger Additions", "Client Ledger"]
    assert "Bulk Invoice Generation" in _titles(more)   # OQC's own pages stay reachable, after the ERP's


def test_leads_summary_counts_add_up(admin):
    html = admin.get("/crm/leads/summary?start=2000-01-01&end=2100-01-01").text
    assert ">Filter<" in html and "Lead Closer" in html
    total = re.search(r'<tr class="bg-\[#e8f1fb\] font-semibold">(.*?)</tr>', html, re.S).group(1)
    nums = [int(n) for n in re.findall(r">(\d+)</td>", total)]
    assert nums[0] == sum(nums[1:9]) > 0      # every lead is in exactly one stage


def test_clients_financial_summary_filters_by_country(admin):
    html = admin.get("/finance/clients-summary?country=Nowhere-Land").text
    assert 'name="country"' in html
    assert "C-0" not in html.split("<tbody", 1)[-1].split("</tbody>", 1)[0]


# --------------------------------------------------------------------------- Human Resource
def test_hr_home_has_the_erps_blue_panels_and_eight_cards(admin):
    html = admin.get("/home/hr").text.replace("&#39;", "'")
    for title in ("Gender Distribution", "Contract Ends (Within 2 months)", "Today's Attendance Status", "Pending Requests"):
        assert f'aria-label="{title}"' in html, title
    assert _titles(html) == ["Dashboards", "Employee Management", "Time and Attendance Management", "Recruitment and Hiring",
                             "Benefits Management", "Financial Management", "Attachments", "HR Configurations"]
    assert '<a href="/hr/attachments" class="oqc-mcard' in html     # the ERP's Attachments card opens the list itself


@pytest.mark.parametrize("slug,title,cards", [
    ("employment", "Employment Management", ["Employee Record", "Notifications", "Employee Requests", "Staff Violations",
                                             "Staff Bonuses", "Downloads", "Tasks", "Complaints", "Advance Requests"]),
    ("attendance", "Attendance Management", ["Daily Attendance", "Attendance Summary", "Employees Progress Sheet",
                                             "Leave Assignment", "Attendance Change Requests", "Leave Management",
                                             "Attendance Report"]),
    ("recruitment", "Recruitment &amp; Hiring", ["Job Requisitions", "Job Applications", "Interview Panels",
                                                "Schedule Interviews", "Candidate Database", "Onboarding", "Summary"]),
    ("financial", "Financial Management", ["Payroll"]),
    ("config", "HR Configurations", ["Departments", "Shift", "Holidays", "Users", "Violation Types", "Staff Bonus Types"]),
])
def test_hr_card_pages_hold_the_erps_cards_first(admin, slug, title, cards):
    html = admin.get(f"/home/hr/{slug}").text
    assert title in html
    assert _titles(html.partition("More in OQC")[0]) == cards


def test_old_users_and_access_page_lands_on_hr_configurations(admin):
    r = admin.get("/home/hr/access", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/home/hr/config"


def test_daily_attendance_has_the_erps_search_options(admin):
    html = admin.get("/hr/attendance").text
    for name in ("day", "employee_id", "type", "employee_type", "designation", "department", "shift"):
        assert f'name="{name}"' in html, name
    assert 'href="/hr/attendance-machines"' in html


# --------------------------------------------------------------------------- Employee Self Portal
ESS_CARDS = ["My Schedule", "Requests", "Attendance Sheet", "Tasks", "Daily Progress Sheet", "Team Management",
             "Account Ledger", "Violations", "Bonuses", "Salary Slips", "Notifications", "Downloads", "Complaints",
             "My Profile"]


@pytest.fixture(scope="module")
def supervisor():
    c = TestClient(app)
    assert c.post("/login", data={"username": "supervisor@oqc.local", "password": "Super@123"}, follow_redirects=False).status_code == 303
    return c


def test_ess_home_has_the_erps_panels_and_fourteen_cards(supervisor):
    html = supervisor.get("/home/self").text.replace("&#39;", "'")
    assert "Employee Self Service" in html
    for title in ("Today's Attendance", "My Team"):
        assert f'aria-label="{title}"' in html, title
    assert "Total Pending Tasks &amp; Actions" in html
    assert 'action="/hr/me/attendance/mark"' in html and 'name="next" value="/home/self"' in html
    erp, _, more = html.partition("More in OQC")
    assert _titles(erp) == ESS_CARDS
    assert [c for _, c in _cards(erp)] == ERP_COLOURS + ERP_COLOURS[:1]   # My Profile wraps to blue, as theirs does
    assert "My Overview" in _titles(more)


def test_ess_home_tree_lists_the_team_under_me(supervisor):
    html = supervisor.get("/home/self").text
    team = re.search(r'aria-label="My Team".*?</section>', html, re.S).group(0)
    assert team.count("<li") >= 2      # me, then at least one report


@pytest.mark.parametrize("url,title,cards", [
    ("/hr/me/requests", "Requests", ["Leave", "Employee Request", "Advance Requests"]),
    ("/hr/me/team-management", "Team Management", ["Staff Violation", "Staff Bonuses"]),
])
def test_ess_card_pages(supervisor, url, title, cards):
    html = supervisor.get(url).text
    assert title in html and _titles(html) == cards
    assert [c for _, c in _cards(html)] == ERP_COLOURS[:len(cards)]


@pytest.mark.parametrize("url,title,present,absent", [
    ("/hr/me/team/violations", "Staff Violations", "Penalty Description", "Bonus Type"),
    ("/hr/me/team/bonuses", "Staff Bonuses", "Bonus Type", "Penalty Description"),
])
def test_team_card_pages_show_their_own_list(supervisor, url, title, present, absent):
    html = supervisor.get(url).text
    assert title in html and present in html
    assert absent not in html


# --------------------------------------------------------------------------- Accounts
@pytest.mark.parametrize("url,title,cards", [
    ("/home/accounts", "Accounts Home", ["Setup", "Transactions", "Reports"]),
    ("/home/accounts/setup", "Setup", ["Accounts Heads", "Chart of Accounts", "Accounts Tree View"]),
    ("/home/accounts/transactions", "Accounts Transactions", ["Journal Voucher", "Payment Voucher", "Receipt Voucher"]),
    ("/home/accounts/reports", "Accounts Reports", ["Ledger Report", "Trial Balance Report", "Income Statement",
                                                    "Balance Sheet", "Payables Summary", "Account Wise Summary",
                                                    "Approved Advances"]),
])
def test_accounts_card_pages_hold_the_erps_cards_first(admin, url, title, cards):
    html = admin.get(url).text
    assert title in html
    assert _titles(html.partition("More in OQC")[0]) == cards


@pytest.mark.parametrize("vtype,title,cols", [
    ("journal", "Journal Voucher", ["ID", "Voucher No", "Voucher Date", "Total Amount", "Description", "Posted", "Print"]),
    ("payment", "Payment Voucher List", ["ID", "Voucher No", "Voucher Date", "Account Name", "Description",
                                         "Total Amount", "Posted", "Print"]),
    ("receipt", "Receipt Voucher List", ["ID", "Voucher No", "Voucher Date", "Account Name", "Description",
                                         "Total Amount", "Posted", "Print"]),
])
def test_voucher_cards_open_their_own_list(admin, vtype, title, cols):
    html = admin.get(f"/finance/accounts/vouchers?type={vtype}").text
    assert "<h1" in html and title in html
    assert f'href="/finance/accounts/vouchers/new?type={vtype}"' in html
    heads = [re.sub(r"<[^>]+>|\s+", " ", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)]
    assert heads[:len(cols)] == cols


def test_payables_summary_is_the_staff_payment_sheet(admin):
    html = admin.get("/finance/accounts/reports/payables").text
    for name in ("shift", "department_id", "designation"):
        assert f'name="{name}"' in html, name
    heads = [re.sub(r"<[^>]+>|\s+", " ", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)]
    assert heads == ["S.NO", "Name", "Status", "Shift", "Department", "Designation", "Bank", "Balance", "Payment", "Signature"]
    assert admin.get("/finance/accounts/reports/vendor-payables").status_code == 200   # OQC's vendor ageing moved, not lost


def test_account_wise_summary_has_the_erps_filters(admin):
    html = admin.get("/finance/accounts/reports/account-wise").text
    assert "Account Master" in html and 'name="account_id"' in html and "Show Zero Values?" in html
    assert "Closing Balance (Debit)" in html and "Closing Balance (Credit)" in html
    shown = admin.get("/finance/accounts/reports/account-wise?show_zero=Y").text
    assert shown.count("<tr") >= html.count("<tr")


def test_approved_advances_carry_shift_and_remarks(admin):
    html = admin.get("/finance/accounts/reports/approved-advances").text
    for col in ("Shift", "No Of Installments", "Remarks", "Hr Remarks"):
        assert f">{col}<" in html or col in html, col


# --------------------------------------------------------------------------- Configuration
CONFIG_CARDS = ["Currency Rates", "Roles", "Lookups", "Setup", "Notification Templates", "Support Ticket",
                "WhatsApp Numbers", "OTP Configuration", "Payment Gateways", "Confido AI Agents"]


def test_configuration_home_draws_the_erps_ten_cards(admin):
    html = admin.get("/home/system").text
    assert "Configuration Home" in html
    erp, _, more = html.partition("More in OQC")
    assert _titles(erp) == CONFIG_CARDS
    assert [c for _, c in _cards(erp)] == ERP_COLOURS[:10]
    assert "Integration Hub" in _titles(more) and "Audit Log" in _titles(more)


def test_old_configuration_address_lands_on_the_home(admin):
    r = admin.get("/config", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/home/system"


def test_shortcut_pages_stay_in_their_owning_area(admin):
    # a shortcut card never pulls the page into the Configuration sidebar
    for url, area in [("/finance/currency-rates", "/home/accounts"), ("/hr/confido-agents", "/home/hr")]:
        side = re.search(r'<aside id="area-sidebar".*?</aside>', admin.get(url).text, re.S).group(0)
        assert f'href="{area}"' in side, url
