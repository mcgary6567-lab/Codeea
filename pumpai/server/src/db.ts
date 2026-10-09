import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import fs from "node:fs";
import path from "node:path";
import { config } from "./config.js";

fs.mkdirSync(path.dirname(config.dbPath), { recursive: true });
// a restore staged from Settings → Backups is swapped in before the database is opened
if (fs.existsSync(`${config.dbPath}.restore`)) {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  for (const ext of ["", "-wal", "-shm"]) if (fs.existsSync(config.dbPath + ext)) fs.renameSync(config.dbPath + ext, `${config.dbPath}.before-restore-${stamp}${ext}`);
  fs.renameSync(`${config.dbPath}.restore`, config.dbPath);
  console.log(`[db] restored backup; previous database kept as ${path.basename(config.dbPath)}.before-restore-${stamp}`);
}
export const db = new DatabaseSync(config.dbPath);
db.exec("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000; PRAGMA synchronous = NORMAL;");

/** Write everything into the main database file and close it (on stop / restart), so pumpai.db alone is a full copy. */
export function closeDb() {
  try { db.exec("PRAGMA wal_checkpoint(TRUNCATE)"); db.close(); } catch { /* already closed */ }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Row = any;
type Params = SQLInputValue[] | [Record<string, SQLInputValue>];

export function all<T = Row>(sql: string, ...params: Params): T[] {
  return db.prepare(sql).all(...(params as SQLInputValue[])) as T[];
}
export function get<T = Row>(sql: string, ...params: Params): T | undefined {
  return db.prepare(sql).get(...(params as SQLInputValue[])) as T | undefined;
}
export function run(sql: string, ...params: Params) {
  const r = db.prepare(sql).run(...(params as SQLInputValue[]));
  return { id: Number(r.lastInsertRowid), changes: Number(r.changes) };
}
let txDepth = 0;
/** Run fn in a transaction. Re-entrant: nested calls join the outer transaction (all-or-nothing). */
export function tx<T>(fn: () => T): T {
  if (txDepth > 0) {
    txDepth++;
    try { return fn(); } finally { txDepth--; }
  }
  db.exec("BEGIN");
  txDepth = 1;
  try {
    const out = fn();
    db.exec("COMMIT");
    return out;
  } catch (e) {
    db.exec("ROLLBACK");
    throw e;
  } finally {
    txDepth = 0;
  }
}
export const now = () => new Date().toISOString();
/** Pakistan (UTC+5, no DST) calendar date, e.g. "2026-10-04". */
/** Meter name shown everywhere: "No.3 · HOBC-11" (needs the nozzles table aliased as n). */
export const METER = "('No.' || n.meter_no || ' · ' || n.label)";
export const meterName = (n: { meter_no?: number | null; label: string }) => (n.meter_no ? `No.${n.meter_no} · ${n.label}` : n.label);
export const pkDate = (ms = Date.now()) => new Date(ms + 5 * 3600_000).toISOString().slice(0, 10);
/** A "YYYY-MM-DD" Pakistan date → UTC timestamp of its midnight (ISO strings pass through). */
export const pkStart = (d: string) => (d.length === 10 ? new Date(`${d}T00:00:00+05:00`).toISOString() : d);
/** Exclusive end of a Pakistan date: midnight of the next day (ISO strings pass through). */
export const pkEnd = (d: string) => (d.length === 10 ? new Date(Date.parse(`${d}T00:00:00+05:00`) + 86_400_000).toISOString() : d);
/** UTC timestamp of midnight in Pakistan for the day containing `ms` ("today" starts here, not at 5am). */
export const pkDayStart = (ms = Date.now()) => new Date(Date.parse(pkDate(ms) + "T00:00:00+05:00")).toISOString();

export function migrate() {
  db.exec(`
  CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner_name TEXT, owner_phone TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
  );
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','manager','salesman','wholesale','cashier','staff')),
    station_id INTEGER, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
  );
  CREATE TABLE IF NOT EXISTS stations (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL, city TEXT, address TEXT, omc TEXT, lat REAL, lng REAL, timings TEXT,
    services TEXT
  );
  CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, product TEXT NOT NULL,
    price REAL NOT NULL, effective_from TEXT NOT NULL, created_by TEXT
  );
  CREATE TABLE IF NOT EXISTS tanks (
    id INTEGER PRIMARY KEY, station_id INTEGER NOT NULL REFERENCES stations(id),
    name TEXT NOT NULL, product TEXT NOT NULL, capacity_l REAL NOT NULL, current_l REAL NOT NULL,
    reorder_pct REAL NOT NULL DEFAULT 25
  );
  CREATE TABLE IF NOT EXISTS nozzles (
    id INTEGER PRIMARY KEY, station_id INTEGER NOT NULL REFERENCES stations(id),
    tank_id INTEGER NOT NULL REFERENCES tanks(id), label TEXT NOT NULL, totalizer REAL NOT NULL DEFAULT 0
  );
  CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY, station_id INTEGER NOT NULL REFERENCES stations(id),
    attendant TEXT NOT NULL, opened_at TEXT NOT NULL, closed_at TEXT,
    status TEXT NOT NULL DEFAULT 'open', litres REAL, cash_expected REAL, cash_actual REAL,
    variance REAL, notes TEXT
  );
  CREATE TABLE IF NOT EXISTS meter_readings (
    id INTEGER PRIMARY KEY, shift_id INTEGER NOT NULL REFERENCES shifts(id),
    nozzle_id INTEGER NOT NULL REFERENCES nozzles(id), opening REAL NOT NULL, closing REAL
  );
  CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL, phone TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'retail',
    city TEXT, opt_in INTEGER NOT NULL DEFAULT 1, language TEXT DEFAULT 'roman_urdu',
    loyalty_points INTEGER NOT NULL DEFAULT 0, segment TEXT, churn_score REAL DEFAULT 0,
    credit_limit REAL NOT NULL DEFAULT 0, balance REAL NOT NULL DEFAULT 0, risk_score REAL DEFAULT 0,
    notes TEXT, last_visit_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (tenant_id, phone)
  );
  CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
    plate_no TEXT NOT NULL, fuel TEXT, daily_limit_l REAL
  );
  CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY, station_id INTEGER NOT NULL REFERENCES stations(id),
    shift_id INTEGER, customer_id INTEGER, nozzle_id INTEGER, product TEXT NOT NULL,
    litres REAL NOT NULL, rate REAL NOT NULL, amount REAL NOT NULL,
    payment_method TEXT NOT NULL, vehicle_no TEXT, created_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_sales_created ON sales(created_at);
  CREATE INDEX IF NOT EXISTS idx_sales_customer ON sales(customer_id);
  CREATE TABLE IF NOT EXISTS khata_ledger (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
    type TEXT NOT NULL CHECK (type IN ('debit','credit')), amount REAL NOT NULL,
    ref TEXT, note TEXT, created_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY, tank_id INTEGER NOT NULL REFERENCES tanks(id),
    supplier TEXT, tanker_no TEXT, invoice_l REAL NOT NULL, received_l REAL NOT NULL,
    shortage_pct REAL NOT NULL, created_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS dip_readings (
    id INTEGER PRIMARY KEY, tank_id INTEGER NOT NULL REFERENCES tanks(id),
    measured_l REAL NOT NULL, book_l REAL NOT NULL, variance_pct REAL NOT NULL, created_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, customer_id INTEGER NOT NULL REFERENCES customers(id),
    product TEXT NOT NULL, litres REAL NOT NULL, rate REAL NOT NULL, amount REAL NOT NULL,
    address TEXT, deliver_at TEXT, payment TEXT DEFAULT 'khata',
    status TEXT NOT NULL DEFAULT 'pending', source TEXT DEFAULT 'whatsapp', created_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS complaints (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, customer_id INTEGER REFERENCES customers(id),
    station_id INTEGER, category TEXT, message TEXT NOT NULL, sentiment TEXT,
    status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, customer_id INTEGER NOT NULL REFERENCES customers(id),
    mode TEXT NOT NULL DEFAULT 'ai', status TEXT NOT NULL DEFAULT 'open',
    last_inbound_at TEXT, last_message_at TEXT, unread INTEGER NOT NULL DEFAULT 0, handoff_reason TEXT,
    UNIQUE (tenant_id, customer_id)
  );
  CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    direction TEXT NOT NULL CHECK (direction IN ('in','out')),
    sender TEXT NOT NULL, body TEXT NOT NULL, meta TEXT, wa_id TEXT, created_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
  CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, segment TEXT NOT NULL,
    message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', sent_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL, sent_at TEXT
  );
  CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER, type TEXT NOT NULL,
    severity TEXT NOT NULL, title TEXT NOT NULL, body TEXT, acknowledged INTEGER NOT NULL DEFAULT 0,
    dedupe_key TEXT, created_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS automations (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, key TEXT NOT NULL, name TEXT NOT NULL,
    description TEXT, cron TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT, last_result TEXT, UNIQUE (tenant_id, key)
  );
  CREATE TABLE IF NOT EXISTS settings (
    tenant_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY (tenant_id, key)
  );

  -- Wholesale supply: dealers / bulk buyers with their own rate card and running account
  CREATE TABLE IF NOT EXISTS wholesale_clients (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL, business_name TEXT, phone TEXT, city TEXT, address TEXT,
    credit_limit REAL NOT NULL DEFAULT 0, opening_balance REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1, notes TEXT, created_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS wholesale_rates (
    client_id INTEGER NOT NULL REFERENCES wholesale_clients(id), product TEXT NOT NULL,
    rate REAL NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY (client_id, product)
  );
  CREATE TABLE IF NOT EXISTS wholesale_rate_history (
    id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES wholesale_clients(id), product TEXT NOT NULL,
    old_rate REAL, new_rate REAL NOT NULL, changed_by TEXT, created_at TEXT NOT NULL
  );
  -- type: supply (fuel out, due up) | return (fuel back in, due down) | payment (due down) | adjustment (+/- due)
  CREATE TABLE IF NOT EXISTS wholesale_txns (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, client_id INTEGER NOT NULL REFERENCES wholesale_clients(id),
    type TEXT NOT NULL CHECK (type IN ('supply','return','payment','adjustment')),
    station_id INTEGER, tank_id INTEGER, product TEXT, litres REAL, rate REAL,
    amount REAL NOT NULL, method TEXT, vehicle_no TEXT, ref TEXT, note TEXT,
    voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT, created_by TEXT, txn_date TEXT NOT NULL, created_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_wtx_client ON wholesale_txns(client_id, txn_date);

  -- Expense management
  CREATE TABLE IF NOT EXISTS expense_categories (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, monthly_budget REAL,
    UNIQUE (tenant_id, name)
  );
  CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER, category TEXT NOT NULL,
    amount REAL NOT NULL, paid_to TEXT, method TEXT NOT NULL DEFAULT 'cash', note TEXT, receipt_ref TEXT,
    status TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('pending','approved','rejected')),
    created_by TEXT, approved_by TEXT, expense_date TEXT NOT NULL, created_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(tenant_id, expense_date);

  -- Suppliers (OMC depots etc.): what we owe for fuel purchased
  CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id), name TEXT NOT NULL,
    phone TEXT, opening_balance REAL NOT NULL DEFAULT 0, notes TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
  );
  -- type: purchase (we owe more) | payment (we paid) | adjustment (+/- owed)
  CREATE TABLE IF NOT EXISTS supplier_txns (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    type TEXT NOT NULL CHECK (type IN ('purchase','payment','adjustment')), delivery_id INTEGER,
    product TEXT, litres REAL, rate REAL, amount REAL NOT NULL, method TEXT, ref TEXT, note TEXT,
    created_by TEXT, txn_date TEXT NOT NULL, created_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_stx_supplier ON supplier_txns(supplier_id, txn_date);

  -- In-app notifications to staff (price changes, shift reminders, shift reports)
  CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
    type TEXT NOT NULL, title TEXT NOT NULL, body TEXT, data TEXT,
    ack_required INTEGER NOT NULL DEFAULT 0, acked_at TEXT, read_at TEXT, created_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, id);
  `);
  migrateUserRoles(); // old role list: rebuild users before any later columns are added to it
  addColumn("users", "phone", "TEXT");
  addColumn("users", "pin_hash", "TEXT"); // 4-digit quick login on the pump's shared tablet
  addColumn("users", "pin_fails", "INTEGER NOT NULL DEFAULT 0");
  addColumn("users", "pin_locked_until", "TEXT");
  addColumn("sales", "slip_no", "TEXT");
  addColumn("sales", "created_by", "INTEGER"); // user who entered it (for undo)
  addColumn("sales", "client_uid", "TEXT");
  addColumn("sales", "source", "TEXT"); // pos | meter (litres on the meter not entered on the POS) // id from the POS so an offline sale synced twice is saved once
  addColumn("sales", "account_id", "INTEGER"); // which bank's POS machine a card/digital sale went to (overrides the pos-map default)
  db.exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_uid ON sales(client_uid)");
  db.exec(`CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, kind TEXT NOT NULL, ref TEXT, mime TEXT NOT NULL, data BLOB NOT NULL,
    ai_result TEXT, created_by INTEGER, created_at TEXT NOT NULL)`); // meter / invoice / receipt photos kept as proof
  db.exec("CREATE INDEX IF NOT EXISTS idx_photos_ref ON photos(ref)");
  addColumn("expenses", "photo_id", "INTEGER");
  // photo of the khata slip (parchi) the customer handed over; on the khata entry it is a proof photo (photos.ref = khata:<id>)
  addColumn("sales", "photo_id", "INTEGER");
  // 1 = added at shift close (online total from the machine slip, or a khata slip missed in the rush), not entered live on the POS
  addColumn("sales", "at_close", "INTEGER");
  addColumn("deliveries", "photo_id", "INTEGER");
  // messages to contacts that are not CRM customers (wholesale clients, staff, owner)
  db.exec(`CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, to_phone TEXT NOT NULL, to_name TEXT, kind TEXT NOT NULL, ref TEXT,
    text TEXT NOT NULL, simulated INTEGER NOT NULL DEFAULT 1, ok INTEGER, created_at TEXT NOT NULL)`);
  addColumn("customers", "card_code", "TEXT"); // QR card for a khata account
  addColumn("vehicles", "card_code", "TEXT"); // QR card stuck on the vehicle
  db.exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_cust_card ON customers(card_code)");
  db.exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_veh_card ON vehicles(card_code)");
  addColumn("users", "salary", "REAL"); // monthly salary for the staff account
  db.exec(`CREATE TABLE IF NOT EXISTS staff_ledger (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
    type TEXT NOT NULL CHECK (type IN ('advance','shortage','repayment','deduction','salary','bonus')),
    amount REAL NOT NULL, note TEXT, ref TEXT, month TEXT, created_by TEXT, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS bank_deposits (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER, amount REAL NOT NULL, bank TEXT NOT NULL, slip_ref TEXT,
    photo_id INTEGER, note TEXT, deposited_by TEXT, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS cash_counts (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, amount REAL NOT NULL, expected REAL, variance REAL, note TEXT, counted_by TEXT, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, supplier_id INTEGER NOT NULL, station_id INTEGER NOT NULL, tank_id INTEGER NOT NULL,
    product TEXT NOT NULL, litres REAL NOT NULL, status TEXT NOT NULL DEFAULT 'ordered', note TEXT, ordered_by TEXT,
    delivery_id INTEGER, delivered_at TEXT, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS tank_charts (tank_id INTEGER NOT NULL, cm REAL NOT NULL, litres REAL NOT NULL, PRIMARY KEY (tank_id, cm))`);
  db.exec(`CREATE TABLE IF NOT EXISTS day_closes (id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, day TEXT NOT NULL, data TEXT NOT NULL, closed_at TEXT NOT NULL, UNIQUE (tenant_id, day))`);
  addColumn("dip_readings", "measured_cm", "REAL");
  // lubricants / tuck shop / tyre shop: items kept per station, every stock movement logged
  db.exec(`CREATE TABLE IF NOT EXISTS shop_items (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER NOT NULL, sku TEXT, barcode TEXT, name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other', unit TEXT NOT NULL DEFAULT 'pc', cost REAL NOT NULL DEFAULT 0, price REAL NOT NULL,
    stock REAL NOT NULL DEFAULT 0, reorder_level REAL NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS shop_sales (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER NOT NULL, shift_id INTEGER, customer_id INTEGER,
    payment_method TEXT NOT NULL, total REAL NOT NULL, cost_total REAL NOT NULL, client_uid TEXT UNIQUE, created_by INTEGER, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS shop_sale_lines (sale_id INTEGER NOT NULL, item_id INTEGER NOT NULL, qty REAL NOT NULL, price REAL NOT NULL, cost REAL NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS shop_moves (
    id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL, type TEXT NOT NULL, qty REAL NOT NULL, cost REAL, ref TEXT, note TEXT, created_by TEXT, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS loyalty_redemptions (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, points INTEGER NOT NULL, sale_id INTEGER, created_by TEXT, created_at TEXT NOT NULL)`);
  // licences / certificates with expiry
  db.exec(`CREATE TABLE IF NOT EXISTS licences (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER, name TEXT NOT NULL, number TEXT, authority TEXT,
    issued_on TEXT, expires_on TEXT NOT NULL, photo_id INTEGER, note TEXT, created_at TEXT NOT NULL)`);
  // daily / weekly checklist (safety, cleanliness, quality, calibration)
  db.exec(`CREATE TABLE IF NOT EXISTS checklist_items (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, title TEXT NOT NULL, urdu TEXT, frequency TEXT NOT NULL DEFAULT 'daily',
    kind TEXT NOT NULL DEFAULT 'check', unit TEXT, min_ok REAL, max_ok REAL, needs_photo INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0)`);
  db.exec(`CREATE TABLE IF NOT EXISTS checklist_entries (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER NOT NULL, item_id INTEGER NOT NULL, day TEXT NOT NULL,
    ok INTEGER NOT NULL, value REAL, note TEXT, photo_id INTEGER, done_by TEXT, created_at TEXT NOT NULL)`);
  // attendance and leave
  addColumn("users", "duty_start", "TEXT"); // "08:00"
  addColumn("users", "job_title", "TEXT"); // e.g. "Chowkidar (night)", "Cleaner" — shown in payroll
  addColumn("users", "weekly_off", "INTEGER"); // 0 = Sunday … 6 = Saturday
  db.exec(`CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL, station_id INTEGER, day TEXT NOT NULL,
    check_in TEXT NOT NULL, check_out TEXT, in_photo_id INTEGER, out_photo_id INTEGER, in_lat REAL, in_lng REAL, away_m REAL,
    late_minutes INTEGER NOT NULL DEFAULT 0, source TEXT, UNIQUE (user_id, day))`);
  addColumn("attendance", "in_acc", "REAL"); // GPS accuracy in metres
  addColumn("attendance", "out_lat", "REAL");
  addColumn("attendance", "out_lng", "REAL");
  addColumn("attendance", "out_away_m", "REAL");
  db.exec(`CREATE TABLE IF NOT EXISTS leaves (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL, from_day TEXT NOT NULL, to_day TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'paid', reason TEXT, status TEXT NOT NULL DEFAULT 'pending', decided_by TEXT, created_at TEXT NOT NULL)`);
  // khata control: hold when overdue, customer portal link version, government bills with PO numbers
  addColumn("customers", "khata_blocked", "INTEGER NOT NULL DEFAULT 0");
  addColumn("customers", "portal_v", "INTEGER NOT NULL DEFAULT 1");
  db.exec(`CREATE TABLE IF NOT EXISTS khata_bills (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, month TEXT NOT NULL, bill_no TEXT NOT NULL, amount REAL NOT NULL,
    po_number TEXT, submitted_on TEXT, status TEXT NOT NULL DEFAULT 'draft', paid_amount REAL NOT NULL DEFAULT 0, note TEXT, created_at TEXT NOT NULL,
    UNIQUE (customer_id, month))`);
  // car wash / oil change / tyre bookings
  db.exec(`CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, service TEXT NOT NULL,
    at TEXT NOT NULL, vehicle_no TEXT, status TEXT NOT NULL DEFAULT 'booked', note TEXT, created_by TEXT, reminded INTEGER NOT NULL DEFAULT 0,
    done_at TEXT, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS price_requests (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, prices TEXT NOT NULL, broadcast INTEGER NOT NULL DEFAULT 0, note TEXT,
    requested_by TEXT, requested_by_id INTEGER, status TEXT NOT NULL DEFAULT 'pending', decided_by TEXT, decided_at TEXT, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER, user_name TEXT, action TEXT NOT NULL,
    ref TEXT, data TEXT, created_at TEXT NOT NULL)`);
  addColumn("expenses", "shift_id", "INTEGER"); // paid in cash from a salesman's shift
  addColumn("meter_readings", "handover_prev", "REAL"); // previous shift's closing reading for this nozzle
  addColumn("wholesale_rates", "mode", "TEXT NOT NULL DEFAULT 'fixed'"); // fixed | discount (pump price − discount)
  addColumn("wholesale_rates", "discount", "REAL"); // Rs/L below the pump price; negative = above
  addColumn("wholesale_rate_history", "note", "TEXT");
  // wholesale fleet: tankers and drivers kept on file, and multi-drop tanker trips
  db.exec(`CREATE TABLE IF NOT EXISTS tankers (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, number TEXT NOT NULL, capacity_l REAL, chambers INTEGER,
    ownership TEXT NOT NULL DEFAULT 'own', owner_name TEXT, owner_phone TEXT, driver_id INTEGER, notes TEXT,
    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, UNIQUE (tenant_id, number))`);
  db.exec(`CREATE TABLE IF NOT EXISTS drivers (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, phone TEXT, cnic TEXT, licence_no TEXT,
    licence_expiry TEXT, address TEXT, notes TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS wholesale_trips (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER NOT NULL, tank_id INTEGER, product TEXT NOT NULL,
    tanker_id INTEGER, driver_id INTEGER, vehicle_no TEXT, driver_name TEXT, litres REAL NOT NULL, amount REAL NOT NULL,
    drops INTEGER NOT NULL, note TEXT, created_by TEXT, trip_date TEXT NOT NULL, created_at TEXT NOT NULL)`);
  addColumn("wholesale_txns", "trip_id", "INTEGER");
  // a trip straight from the depot to the clients (bypass): the fuel never enters our tanks, we buy it from the supplier on the way
  addColumn("wholesale_trips", "source", "TEXT NOT NULL DEFAULT 'pump'");
  addColumn("wholesale_trips", "supplier_id", "INTEGER");
  addColumn("wholesale_trips", "depot_ref", "TEXT");
  addColumn("wholesale_trips", "invoice_l", "REAL");
  addColumn("wholesale_trips", "cost", "REAL"); // what the supplier billed for the fuel
  addColumn("wholesale_trips", "freight", "REAL");
  addColumn("wholesale_trips", "freight_by", "TEXT"); // rate (in the purchase rate) | supplier (on their bill) | cash (paid, booked as an expense)
  addColumn("supplier_txns", "trip_id", "INTEGER"); // a depot-direct purchase: not stock, its cost goes straight against that trip's sales
  // A depot is the place we lift fuel from (name + address). Inside it several company men work
  // (Shell/PSO/Total …, each a person). The depot is only a grouping header; the khata lives on the
  // supplier row (the company + person), so orders and payments reach the right man.
  db.exec(`CREATE TABLE IF NOT EXISTS depots (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, address TEXT, city TEXT, phone TEXT,
    notes TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)`);
  addColumn("suppliers", "depot_id", "INTEGER");       // which depot this contact belongs to (null = standalone)
  addColumn("suppliers", "company", "TEXT");           // oil company he represents: Shell / PSO / Total …
  // what each role may do, changed per pump from Users & Roles (laid over the built-in defaults)
  // fuel products the admin manages (add/rename/recolour/hide/delete) — see products.ts
  db.exec(`CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
    short TEXT, colour TEXT, ur TEXT, sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, UNIQUE (tenant_id, code))`);
  // lists the admin manages from the app (customer types, machine types, shop categories…) — see routes/lookups.ts
  db.exec(`CREATE TABLE IF NOT EXISTS lookups (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, label TEXT NOT NULL,
    extra TEXT NOT NULL DEFAULT '{}', sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, UNIQUE (tenant_id, kind, key))`);
  db.exec(`CREATE TABLE IF NOT EXISTS role_permissions (
    tenant_id INTEGER NOT NULL, perm TEXT NOT NULL, role TEXT NOT NULL, allowed INTEGER NOT NULL,
    updated_by TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (tenant_id, perm, role))`);
  // the customer's own khata page behind a PIN (wholesale clients and khata customers)
  for (const tbl of ["wholesale_clients", "customers"]) {
    addColumn(tbl, "portal_v", `INTEGER NOT NULL DEFAULT ${tbl === "customers" ? 1 : 0}`);
    addColumn(tbl, "portal_off", "INTEGER NOT NULL DEFAULT 0");
    addColumn(tbl, "portal_fails", "INTEGER NOT NULL DEFAULT 0");
    addColumn(tbl, "portal_locked_until", "TEXT");
    addColumn(tbl, "portal_seen_at", "TEXT");
  }
  addColumn("wholesale_txns", "tanker_id", "INTEGER");
  addColumn("wholesale_txns", "driver_id", "INTEGER");
  addColumn("wholesale_txns", "driver_name", "TEXT");
  addColumn("wholesale_txns", "location", "TEXT"); // where this drop was unloaded
  addColumn("meter_readings", "handover_gap", "REAL");
  // meter numbers per station (No.1, No.2 …): existing meters get numbers in the order they were added,
  // new ones get the next free number automatically
  addColumn("nozzles", "meter_no", "INTEGER");
  db.exec(`UPDATE nozzles SET meter_no = (SELECT COUNT(*) FROM nozzles n2 WHERE n2.station_id=nozzles.station_id AND n2.id<=nozzles.id) WHERE meter_no IS NULL`);
  db.exec(`CREATE TRIGGER IF NOT EXISTS nozzle_meter_no AFTER INSERT ON nozzles WHEN NEW.meter_no IS NULL BEGIN
    UPDATE nozzles SET meter_no = (SELECT COALESCE(MAX(meter_no),0)+1 FROM nozzles WHERE station_id=NEW.station_id) WHERE id=NEW.id; END`); // litres the meter moved between the two shifts // indent / parchi number from police, schools, govt offices
  // khata entries keep the fuel detail at the time of sale (litres, rate then, vehicle, slip)
  for (const [c, t] of [["product", "TEXT"], ["litres", "REAL"], ["rate", "REAL"], ["vehicle_no", "TEXT"], ["slip_no", "TEXT"], ["station_id", "INTEGER"]])
    addColumn("khata_ledger", c, t);
  addColumn("meter_readings", "checkpoint", "REAL"); // last settled meter reading (e.g. at a price change)
  addColumn("meter_readings", "checkpoint_at", "TEXT");
  addColumn("deliveries", "supplier_id", "INTEGER");
  addColumn("deliveries", "purchase_rate", "REAL");
  // prepaid fuel: coupons sold in advance (single use) and company wallets (money deposited first)
  db.exec(`CREATE TABLE IF NOT EXISTS fuel_coupons (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, code TEXT NOT NULL UNIQUE, batch TEXT NOT NULL, value REAL NOT NULL, product TEXT,
    buyer TEXT, customer_id INTEGER, method TEXT NOT NULL DEFAULT 'cash', status TEXT NOT NULL DEFAULT 'active', expires_on TEXT,
    sold_by TEXT, sold_at TEXT NOT NULL, sale_id INTEGER, used_at TEXT, used_by TEXT, void_reason TEXT)`);
  addColumn("customers", "wallet_balance", "REAL NOT NULL DEFAULT 0");
  addColumn("customers", "wallet_low", "REAL"); // WhatsApp when the wallet falls below this
  addColumn("customers", "wallet_low_sent", "INTEGER NOT NULL DEFAULT 0");
  db.exec(`CREATE TABLE IF NOT EXISTS wallet_ledger (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, type TEXT NOT NULL CHECK (type IN ('deposit','fill','refund','adjustment')),
    amount REAL NOT NULL, method TEXT, ref TEXT, note TEXT, sale_id INTEGER, created_by TEXT, created_at TEXT NOT NULL)`);
  // customer rating after a fill (WhatsApp 1-5)
  db.exec(`CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, sale_id INTEGER, station_id INTEGER, salesman_id INTEGER,
    score INTEGER, comment TEXT, status TEXT NOT NULL DEFAULT 'asked', complaint_id INTEGER, asked_at TEXT NOT NULL, rated_at TEXT)`);
  // things waiting for the owner's "1" on WhatsApp (price change, big expense)
  db.exec(`CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, kind TEXT NOT NULL, ref_id INTEGER NOT NULL, summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', decided_by TEXT, decided_via TEXT, created_at TEXT NOT NULL, decided_at TEXT, UNIQUE (kind, ref_id))`);
  // rent, bijli, security… booked by themselves every month; utility bills read from a photo
  db.exec(`CREATE TABLE IF NOT EXISTS recurring_expenses (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER, category TEXT NOT NULL, amount REAL NOT NULL, paid_to TEXT,
    method TEXT NOT NULL DEFAULT 'cash', day_of_month INTEGER NOT NULL DEFAULT 1, note TEXT, active INTEGER NOT NULL DEFAULT 1,
    last_month TEXT, created_by TEXT, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS utility_bills (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER, kind TEXT NOT NULL DEFAULT 'electricity', month TEXT NOT NULL,
    units REAL, amount REAL NOT NULL, due_date TEXT, reference TEXT, photo_id INTEGER, expense_id INTEGER, prev_amount REAL, prev_units REAL,
    change_pct REAL, created_by TEXT, created_at TEXT NOT NULL)`);
  addColumn("sales", "coupon_id", "INTEGER");
  // money: tanker shortage claims, freight per tanker, withholding tax
  addColumn("deliveries", "freight", "REAL"); // Rs paid for transport of this tanker
  db.exec(`CREATE TABLE IF NOT EXISTS shortage_claims (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, delivery_id INTEGER NOT NULL UNIQUE, supplier_id INTEGER, litres REAL NOT NULL, rate REAL NOT NULL,
    amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open', claim_ref TEXT, claimed_on TEXT, recovered REAL NOT NULL DEFAULT 0, recovered_by TEXT,
    note TEXT, created_at TEXT NOT NULL, updated_at TEXT)`);
  // a claim can also be for a depot-direct trip (no delivery into our tanks): the depot billed more litres than the clients got
  if (all<{ name: string; notnull: number }>("PRAGMA table_info(shortage_claims)").find((c) => c.name === "delivery_id")?.notnull) {
    db.exec(`BEGIN;
      CREATE TABLE shortage_claims_new (
        id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, delivery_id INTEGER UNIQUE, supplier_id INTEGER, litres REAL NOT NULL, rate REAL NOT NULL,
        amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open', claim_ref TEXT, claimed_on TEXT, recovered REAL NOT NULL DEFAULT 0, recovered_by TEXT,
        note TEXT, created_at TEXT NOT NULL, updated_at TEXT);
      INSERT INTO shortage_claims_new (id,tenant_id,delivery_id,supplier_id,litres,rate,amount,status,claim_ref,claimed_on,recovered,recovered_by,note,created_at,updated_at)
        SELECT id,tenant_id,delivery_id,supplier_id,litres,rate,amount,status,claim_ref,claimed_on,recovered,recovered_by,note,created_at,updated_at FROM shortage_claims;
      DROP TABLE shortage_claims;
      ALTER TABLE shortage_claims_new RENAME TO shortage_claims;
      COMMIT;`);
  }
  addColumn("shortage_claims", "trip_id", "INTEGER");
  addColumn("shortage_claims", "product", "TEXT");
  addColumn("shortage_claims", "invoice_l", "REAL");
  addColumn("shortage_claims", "received_l", "REAL");
  db.exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_claim_trip ON shortage_claims(trip_id, product) WHERE trip_id IS NOT NULL");
  db.exec(`CREATE TABLE IF NOT EXISTS tax_withholdings (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, payee TEXT NOT NULL, supplier_id INTEGER, supplier_txn_id INTEGER, section TEXT,
    gross REAL NOT NULL, rate REAL, amount REAL NOT NULL, cpr_no TEXT, deposited_on TEXT, note TEXT, created_by TEXT, txn_date TEXT NOT NULL, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS bank_recons (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, bank TEXT, period_from TEXT, period_to TEXT, statement_closing REAL, lines INTEGER, matched INTEGER,
    bank_only REAL, books_only REAL, created_by TEXT, created_at TEXT NOT NULL)`);
  // staff: salary slips, loans with monthly instalments, training records
  db.exec(`CREATE TABLE IF NOT EXISTS salary_slips (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL, month TEXT NOT NULL, data TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL, UNIQUE (user_id, month))`);
  db.exec(`CREATE TABLE IF NOT EXISTS staff_loans (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL, amount REAL NOT NULL, instalment REAL NOT NULL, note TEXT,
    status TEXT NOT NULL DEFAULT 'active', created_by TEXT, created_at TEXT NOT NULL, closed_at TEXT)`);
  db.exec(`CREATE TABLE IF NOT EXISTS trainings (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL, topic TEXT NOT NULL, done_on TEXT NOT NULL, next_due TEXT,
    trainer TEXT, note TEXT, photo_id INTEGER, created_by TEXT, created_at TEXT NOT NULL)`);
  // machines register: dispensers, generator, compressor, fans, lights… with service schedule, faults and warranty
  db.exec(`CREATE TABLE IF NOT EXISTS machines (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER, name TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'other',
    make TEXT, model TEXT, serial_no TEXT, location TEXT, installed_on TEXT, cost REAL, vendor TEXT, vendor_phone TEXT, warranty_until TEXT,
    service_every_days INTEGER, service_every_hours REAL, last_service_on TEXT, next_service_on TEXT, hours REAL, last_service_hours REAL,
    status TEXT NOT NULL DEFAULT 'working', photo_id INTEGER, notes TEXT, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS machine_logs (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, machine_id INTEGER NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL, description TEXT NOT NULL,
    cost REAL, done_by TEXT, hours REAL, downtime_hours REAL, photo_id INTEGER, expense_id INTEGER, resolved_at TEXT, created_by TEXT, created_at TEXT NOT NULL)`);
  // the owner's bank accounts; every non-cash payment can say which account the money went to / came from
  db.exec(`CREATE TABLE IF NOT EXISTS bank_accounts (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, bank TEXT NOT NULL, branch TEXT, title TEXT, account_no TEXT, kind TEXT NOT NULL DEFAULT 'current',
    opening_balance REAL NOT NULL DEFAULT 0, opening_date TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, note TEXT, created_by TEXT, created_at TEXT NOT NULL)`);
  // money that moves only in the bank: cash taken out, transfers, charges, profit, owner money in / out
  db.exec(`CREATE TABLE IF NOT EXISTS bank_txns (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, account_id INTEGER NOT NULL, kind TEXT NOT NULL, amount REAL NOT NULL,
    party TEXT, ref TEXT, note TEXT, txn_date TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL)`);
  db.exec("CREATE INDEX IF NOT EXISTS idx_bank_txns_acc ON bank_txns(account_id)");
  for (const tbl of ["bank_deposits", "wholesale_txns", "khata_ledger", "supplier_txns", "expenses", "wallet_ledger"]) addColumn(tbl, "account_id", "INTEGER");
  // allow a 'carriage' txn (bypass supply on our depot ID: we only bill the client kiraya/commission, no fuel on our books)
  {
    const sql = (get("SELECT sql FROM sqlite_master WHERE type='table' AND name='wholesale_txns'")?.sql ?? "") as string;
    if (sql && !sql.includes("'carriage'")) {
      const cols = "id,tenant_id,client_id,type,station_id,tank_id,product,litres,rate,amount,method,vehicle_no,ref,note,voided,void_reason,created_by,txn_date,created_at,trip_id,tanker_id,driver_id,driver_name,location,account_id";
      db.exec("PRAGMA foreign_keys=OFF");
      db.exec(`CREATE TABLE wholesale_txns__new (
        id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, client_id INTEGER NOT NULL REFERENCES wholesale_clients(id),
        type TEXT NOT NULL CHECK (type IN ('supply','return','payment','adjustment','carriage')),
        station_id INTEGER, tank_id INTEGER, product TEXT, litres REAL, rate REAL,
        amount REAL NOT NULL, method TEXT, vehicle_no TEXT, ref TEXT, note TEXT,
        voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT, created_by TEXT, txn_date TEXT NOT NULL, created_at TEXT NOT NULL,
        trip_id INTEGER, tanker_id INTEGER, driver_id INTEGER, driver_name TEXT, location TEXT, account_id INTEGER)`);
      db.exec(`INSERT INTO wholesale_txns__new (${cols}) SELECT ${cols} FROM wholesale_txns`);
      db.exec("DROP TABLE wholesale_txns");
      db.exec("ALTER TABLE wholesale_txns__new RENAME TO wholesale_txns");
      db.exec("CREATE INDEX IF NOT EXISTS idx_wtx_client ON wholesale_txns(client_id, txn_date)");
      db.exec("PRAGMA foreign_keys=ON");
    }
  }
  // wholesale desk: order book, payment promises, cheque register
  db.exec(`CREATE TABLE IF NOT EXISTS wholesale_orders (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, client_id INTEGER NOT NULL, product TEXT NOT NULL, litres REAL NOT NULL, needed_on TEXT NOT NULL,
    location TEXT, note TEXT, status TEXT NOT NULL DEFAULT 'open', txn_id INTEGER, created_by TEXT, created_at TEXT NOT NULL, done_at TEXT)`);
  db.exec(`CREATE TABLE IF NOT EXISTS wholesale_promises (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, client_id INTEGER NOT NULL, amount REAL NOT NULL, promised_on TEXT NOT NULL, note TEXT,
    status TEXT NOT NULL DEFAULT 'open', created_by TEXT, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS wholesale_cheques (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, client_id INTEGER NOT NULL, amount REAL NOT NULL, bank TEXT NOT NULL, cheque_no TEXT NOT NULL,
    cheque_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'in_hand', account_id INTEGER, payment_txn_id INTEGER, bounce_reason TEXT, note TEXT,
    created_by TEXT, created_at TEXT NOT NULL, deposited_at TEXT, cleared_at TEXT, updated_at TEXT NOT NULL)`);
  // Supply requests a wholesale client places from their own portal link. Pure communication:
  // the client asks for X litres on a date, staff approve/reject, an approval pings the client on WhatsApp.
  // NOT linked to the ledger / khata or to the staff order book — it only carries the ask and its reply.
  db.exec(`CREATE TABLE IF NOT EXISTS wholesale_requests (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, client_id INTEGER NOT NULL, product TEXT NOT NULL, litres REAL NOT NULL, want_date TEXT NOT NULL,
    note TEXT, status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','cancelled')),
    reply TEXT, decided_by TEXT, decided_at TEXT, created_at TEXT NOT NULL)`);
  // Fuel money for a bypass-on-our-ID supply (separate from our kiraya): either the client paid the depot DIRECT
  // (we just keep the proof), or the client sent it to US and we FORWARD it to the depot (a net-zero pass-through
  // through our bank — "Depot money held" until forwarded). Never touches the client's kiraya due or our profit.
  db.exec(`CREATE TABLE IF NOT EXISTS bypass_fuel_payments (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, client_id INTEGER NOT NULL, supplier_id INTEGER NOT NULL, amount REAL NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('direct','through_us')), status TEXT NOT NULL CHECK (status IN ('direct','held','forwarded')),
    in_account_id INTEGER, in_ref TEXT, fwd_account_id INTEGER, fwd_ref TEXT, forwarded_at TEXT, forwarded_by TEXT,
    invoice_ref TEXT, note TEXT, created_by TEXT, txn_date TEXT NOT NULL, created_at TEXT NOT NULL)`);
  addColumn("bypass_fuel_payments", "voided", "INTEGER NOT NULL DEFAULT 0");
  addColumn("bypass_fuel_payments", "carriage_txn_id", "INTEGER"); // links the fuel money to the kiraya supply it belongs to

  /* ---- Carriage / kiraya business: its own party ("thekedar") and its own ledger, separate from wholesale ---- */
  // Carriage (bypass on our depot ID) is run with thekedars, not wholesale clients. A thekedar has his own running
  // account: we bill him the fixed kiraya (receivable, booked as income) and he settles it like any other party.
  db.exec(`CREATE TABLE IF NOT EXISTS thekedars (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL, phone TEXT, cnic TEXT, city TEXT, address TEXT,
    opening_balance REAL NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, notes TEXT, created_at TEXT NOT NULL)`);
  // type: carriage (kiraya billed, due up) | payment (thekedar pays, due down) | adjustment (+/- due)
  db.exec(`CREATE TABLE IF NOT EXISTS carriage_txns (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, thekedar_id INTEGER NOT NULL REFERENCES thekedars(id),
    type TEXT NOT NULL CHECK (type IN ('carriage','payment','adjustment')),
    supplier_id INTEGER, product TEXT, litres REAL, amount REAL NOT NULL,
    method TEXT, account_id INTEGER, vehicle_no TEXT, ref TEXT, note TEXT,
    voided INTEGER NOT NULL DEFAULT 0, void_reason TEXT, created_by TEXT, txn_date TEXT NOT NULL, created_at TEXT NOT NULL)`);
  db.exec("CREATE INDEX IF NOT EXISTS idx_carriage_thekedar ON carriage_txns(thekedar_id, txn_date)");
  addColumn("carriage_txns", "govt_pct", "REAL");       // govt cut taken off the kiraya (e.g. 10 = 10%)
  addColumn("carriage_txns", "gross_amount", "REAL");   // the kiraya before the govt cut; `amount` is the net billed
  // fuel-money records now belong to a thekedar (carriage). Add thekedar_id and relax the old NOT NULL on client_id/supplier_id.
  {
    const sql = (get("SELECT sql FROM sqlite_master WHERE type='table' AND name='bypass_fuel_payments'")?.sql ?? "") as string;
    if (sql && !sql.includes("thekedar_id")) {
      const cols = "id,tenant_id,client_id,supplier_id,amount,mode,status,in_account_id,in_ref,fwd_account_id,fwd_ref,forwarded_at,forwarded_by,invoice_ref,note,created_by,txn_date,created_at,voided,carriage_txn_id";
      db.exec("PRAGMA foreign_keys=OFF");
      db.exec(`CREATE TABLE bypass_fuel_payments__new (
        id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, thekedar_id INTEGER, client_id INTEGER, supplier_id INTEGER, amount REAL NOT NULL,
        mode TEXT NOT NULL CHECK (mode IN ('direct','through_us')), status TEXT NOT NULL CHECK (status IN ('direct','held','forwarded')),
        in_account_id INTEGER, in_ref TEXT, fwd_account_id INTEGER, fwd_ref TEXT, forwarded_at TEXT, forwarded_by TEXT,
        invoice_ref TEXT, note TEXT, created_by TEXT, txn_date TEXT NOT NULL, created_at TEXT NOT NULL,
        voided INTEGER NOT NULL DEFAULT 0, carriage_txn_id INTEGER)`);
      db.exec(`INSERT INTO bypass_fuel_payments__new (${cols}) SELECT ${cols} FROM bypass_fuel_payments`);
      db.exec("DROP TABLE bypass_fuel_payments");
      db.exec("ALTER TABLE bypass_fuel_payments__new RENAME TO bypass_fuel_payments");
      db.exec("PRAGMA foreign_keys=ON");
    }
  }
  /* ---- Bypass delivery: buy fuel from suppliers at the depot and deliver straight to wholesale clients ---- */
  // The supplier's bypass dealings keep their OWN account (separate from the pump-stock supplier payable): we buy at a
  // cost, deliver to clients at their rate, earn the margin. One delivery can source from several suppliers (each line
  // its own litres + cost) and drop to several clients; we can never deliver more litres than we purchased.
  db.exec(`CREATE TABLE IF NOT EXISTS bypass_deliveries (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER,
    tanker_id INTEGER, driver_id INTEGER, vehicle_no TEXT, driver_name TEXT,
    note TEXT, voided INTEGER NOT NULL DEFAULT 0, created_by TEXT, txn_date TEXT NOT NULL, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS bypass_purchases (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, delivery_id INTEGER, supplier_id INTEGER NOT NULL,
    product TEXT NOT NULL, litres REAL NOT NULL, cost_rate REAL NOT NULL, amount REAL NOT NULL,
    ref TEXT, note TEXT, voided INTEGER NOT NULL DEFAULT 0, created_by TEXT, txn_date TEXT NOT NULL, created_at TEXT NOT NULL)`);
  db.exec("CREATE INDEX IF NOT EXISTS idx_bypass_pur_sup ON bypass_purchases(supplier_id, txn_date)");
  // fuel delivered out of the bypass stock to a client — carries its share of the stock cost (weighted average).
  // Buying more than we deliver leaves the rest as bypass stock (an asset) to sell later when the rate goes up.
  db.exec(`CREATE TABLE IF NOT EXISTS bypass_drops (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, delivery_id INTEGER, wtx_id INTEGER, client_id INTEGER,
    product TEXT NOT NULL, litres REAL NOT NULL, unit_cost REAL NOT NULL, cost_amount REAL NOT NULL,
    voided INTEGER NOT NULL DEFAULT 0, created_by TEXT, txn_date TEXT NOT NULL, created_at TEXT NOT NULL)`);
  db.exec("CREATE INDEX IF NOT EXISTS idx_bypass_drops_prod ON bypass_drops(product, txn_date)");
  // what we pay the bypass supplier: we_pay (from our bank/cash), client_direct (client paid them for us),
  // through_us (client sent it to us, we forwarded) — the client legs also lower that client's due to us.
  db.exec(`CREATE TABLE IF NOT EXISTS bypass_supplier_payments (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, supplier_id INTEGER NOT NULL, amount REAL NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('we_pay','client_direct','through_us')),
    client_id INTEGER, wtx_id INTEGER, method TEXT, account_id INTEGER, ref TEXT, note TEXT,
    voided INTEGER NOT NULL DEFAULT 0, created_by TEXT, txn_date TEXT NOT NULL, created_at TEXT NOT NULL)`);
  db.exec("CREATE INDEX IF NOT EXISTS idx_bypass_pay_sup ON bypass_supplier_payments(supplier_id, txn_date)");
  addCashierRole();
  // cashier: cheques received from khata customers / others and cheques we issue (wholesale cheques have their own register)
  db.exec(`CREATE TABLE IF NOT EXISTS cheques (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, direction TEXT NOT NULL, party_type TEXT NOT NULL, party_id INTEGER, party_name TEXT NOT NULL,
    amount REAL NOT NULL, bank TEXT NOT NULL, cheque_no TEXT NOT NULL, cheque_date TEXT NOT NULL, status TEXT NOT NULL, account_id INTEGER,
    txn_ref TEXT, reason TEXT, note TEXT, created_by TEXT, created_at TEXT NOT NULL, deposited_at TEXT, cleared_at TEXT, updated_at TEXT NOT NULL)`);
  // every receipt / payment voucher made at the cashier desk (serial no. for the slip); 'other' cash ones feed the cash book
  db.exec(`CREATE TABLE IF NOT EXISTS cashier_vouchers (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, direction TEXT NOT NULL, party_type TEXT NOT NULL, party_id INTEGER, party_name TEXT NOT NULL,
    amount REAL NOT NULL, method TEXT NOT NULL, account_id INTEGER, category TEXT, ref TEXT, note TEXT, src TEXT, voided INTEGER NOT NULL DEFAULT 0,
    created_by TEXT, created_at TEXT NOT NULL)`);
  // cash note breakdown kept with a cash voucher: { "5000": 2, "1000": 10, ... } — for the slip and the record
  addColumn("cashier_vouchers", "notes_json", "TEXT");
  // property & rent: units inside the pump (shop, hotel, service bay) rented out = income; and the pump's own rent = expense
  db.exec(`CREATE TABLE IF NOT EXISTS rentals (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, station_id INTEGER, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'shop',
    tenant_name TEXT, phone TEXT, monthly_rent REAL NOT NULL DEFAULT 0, deposit REAL NOT NULL DEFAULT 0, start_day TEXT, active INTEGER NOT NULL DEFAULT 1,
    note TEXT, created_by TEXT, created_at TEXT NOT NULL)`);
  db.exec(`CREATE TABLE IF NOT EXISTS rental_payments (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, rental_id INTEGER NOT NULL, for_month TEXT NOT NULL, amount REAL NOT NULL,
    method TEXT NOT NULL DEFAULT 'cash', account_id INTEGER, ref TEXT, note TEXT, voucher_id INTEGER, received_by TEXT, created_at TEXT NOT NULL)`);
  db.exec("CREATE INDEX IF NOT EXISTS idx_rental_pay ON rental_payments(rental_id, for_month)");
  // the cashier takes the cash from the salesman after the shift closes
  for (const [c, t] of [["handed_amount", "REAL"], ["handed_to", "TEXT"], ["handed_at", "TEXT"], ["handover_note", "TEXT"]]) addColumn("shifts", c, t);
  addColumn("cash_counts", "notes_json", "TEXT");
  // money wiring: how a staff advance was paid, which bank got coupon money, what an expense cheque is for
  addColumn("staff_ledger", "method", "TEXT");
  addColumn("staff_ledger", "account_id", "INTEGER");
  addColumn("fuel_coupons", "account_id", "INTEGER");
  addColumn("cheques", "category", "TEXT");
  addColumn("tax_withholdings", "paid_method", "TEXT");
  addColumn("tax_withholdings", "paid_account_id", "INTEGER");
  // sign-in safety: wrong passwords lock the account for a while; a password change signs out old sessions
  addColumn("users", "pw_fails", "INTEGER NOT NULL DEFAULT 0");
  addColumn("users", "pw_locked_until", "TEXT");
  addColumn("users", "token_version", "INTEGER NOT NULL DEFAULT 0");
  // rush-hour shift close: litres put back in the tank after a nozzle test, and the notes counted in the cash bag
  addColumn("meter_readings", "test_l", "REAL");
  addColumn("shifts", "cash_notes", "TEXT");
  // the admin can close a station, retire a tank or a meter and switch off an expense category — history stays
  addColumn("stations", "active", "INTEGER NOT NULL DEFAULT 1");
  addColumn("tanks", "active", "INTEGER NOT NULL DEFAULT 1");
  addColumn("nozzles", "active", "INTEGER NOT NULL DEFAULT 1");
  addColumn("expense_categories", "active", "INTEGER NOT NULL DEFAULT 1");
  addColumn("customers", "active", "INTEGER NOT NULL DEFAULT 1"); // archive a dead / duplicate customer — history stays
  // one-time: existing salesmen get a default 8:00 AM duty start so the 24-hour shift attendance tracks them
  // (guarded so it never re-sets a duty time the owner later clears on purpose)
  if (!get("SELECT value FROM settings WHERE tenant_id=0 AND key='mig_salesman_duty8'")) {
    run("UPDATE users SET duty_start='08:00' WHERE role='salesman' AND (duty_start IS NULL OR duty_start='')");
    run("INSERT INTO settings (tenant_id,key,value) VALUES (0,'mig_salesman_duty8','1')");
  }
}

/** Allow the cashier role on databases made before it (the users table keeps every column it has today). */
function addCashierRole() {
  const ddl = get<{ sql: string }>("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")?.sql ?? "";
  if (ddl.includes("'cashier'") && ddl.includes("'staff'")) return;
  const indexes = all<{ sql: string }>("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='users' AND sql IS NOT NULL").map((i) => i.sql);
  const fresh = ddl.replace("'wholesale')", "'wholesale','cashier','staff')").replace("'wholesale','cashier')", "'wholesale','cashier','staff')").replace(/CREATE TABLE\s+"?users"?/i, "CREATE TABLE users_new");
  db.exec(`PRAGMA foreign_keys = OFF; BEGIN; ${fresh}; INSERT INTO users_new SELECT * FROM users; DROP TABLE users; ALTER TABLE users_new RENAME TO users; ${indexes.map((x) => x + ";").join(" ")} COMMIT; PRAGMA foreign_keys = ON;`);
}

/** Add a column to an existing table if it is missing (for databases created by older versions). */
function addColumn(table: string, column: string, type: string) {
  const cols = all<{ name: string }>(`PRAGMA table_info(${table})`).map((c) => c.name);
  if (!cols.includes(column)) db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${type}`);
}

/**
 * Upgrade the users table when the allowed roles change
 * (old owner/accountant/attendant roles, or databases created before the wholesale role).
 */
function migrateUserRoles() {
  const ddl = get<{ sql: string }>("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")?.sql ?? "";
  if (ddl.includes("'wholesale'")) return;
  const active = ddl.includes("active INTEGER") ? "active" : "1";
  db.exec(`
    PRAGMA foreign_keys = OFF;
    BEGIN;
    CREATE TABLE users_new (
      id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
      name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
      role TEXT NOT NULL CHECK (role IN ('admin','manager','salesman','wholesale')),
      station_id INTEGER, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    );
    INSERT INTO users_new (id,tenant_id,name,email,password_hash,role,station_id,active,created_at)
      SELECT id,tenant_id,name,email,password_hash,
        CASE role WHEN 'owner' THEN 'admin' WHEN 'admin' THEN 'admin' WHEN 'attendant' THEN 'salesman' WHEN 'salesman' THEN 'salesman' ELSE 'manager' END,
        station_id, ${active}, created_at FROM users;
    DROP TABLE users;
    ALTER TABLE users_new RENAME TO users;
    COMMIT;
    PRAGMA foreign_keys = ON;
  `);
}

export function getSetting(tenantId: number, key: string, fallback = ""): string {
  return get<{ value: string }>("SELECT value FROM settings WHERE tenant_id=? AND key=?", tenantId, key)?.value ?? fallback;
}
export function setSetting(tenantId: number, key: string, value: string) {
  run(
    "INSERT INTO settings (tenant_id,key,value) VALUES (?,?,?) ON CONFLICT(tenant_id,key) DO UPDATE SET value=excluded.value",
    tenantId, key, value,
  );
}
