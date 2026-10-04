import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import fs from "node:fs";
import path from "node:path";
import { config } from "./config.js";

fs.mkdirSync(path.dirname(config.dbPath), { recursive: true });
export const db = new DatabaseSync(config.dbPath);
db.exec("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;");

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
export function tx<T>(fn: () => T): T {
  db.exec("BEGIN");
  try {
    const out = fn();
    db.exec("COMMIT");
    return out;
  } catch (e) {
    db.exec("ROLLBACK");
    throw e;
  }
}
export const now = () => new Date().toISOString();

export function migrate() {
  db.exec(`
  CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner_name TEXT, owner_phone TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
  );
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','manager','salesman','wholesale')),
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
  `);
  addColumn("deliveries", "supplier_id", "INTEGER");
  addColumn("deliveries", "purchase_rate", "REAL");
  migrateUserRoles();
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
