import { all, get, run, tx, now, type Row } from "./db.js";
import { config } from "./config.js";

export class AppError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Normalise Pakistani mobile numbers to E.164 digits without "+" (e.g. 03001234567 -> 923001234567). */
export function normalizePhone(raw: string): string {
  let d = String(raw).replace(/\D/g, "");
  if (d.startsWith("0092")) d = d.slice(2);
  if (d.startsWith("03") && d.length === 11) d = "92" + d.slice(1);
  if (d.startsWith("3") && d.length === 10) d = "92" + d;
  return d;
}

export const pkr = (n: number) => "Rs " + Math.round(n).toLocaleString("en-PK");
export const rateFmt = (n: number) => "Rs " + n.toFixed(2);

export function currentPrices(tenantId: number): Record<string, { price: number; effective_from: string }> {
  const rows = all(
    `SELECT p.product, p.price, p.effective_from FROM prices p
     WHERE p.tenant_id=? AND p.id = (SELECT id FROM prices p2 WHERE p2.tenant_id=p.tenant_id AND p2.product=p.product
       AND p2.effective_from <= ? ORDER BY p2.effective_from DESC, p2.id DESC LIMIT 1)`,
    tenantId, now(),
  );
  const out: Record<string, { price: number; effective_from: string }> = {};
  for (const r of rows) out[r.product] = { price: r.price, effective_from: r.effective_from };
  return out;
}

export function priceOf(tenantId: number, product: string): number {
  const p = currentPrices(tenantId)[product];
  if (!p) throw new AppError(400, `No price set for ${product}`);
  return p.price;
}

export function upsertCustomerByPhone(tenantId: number, phone: string, name?: string): Row {
  const p = normalizePhone(phone);
  let c = get("SELECT * FROM customers WHERE tenant_id=? AND phone=?", tenantId, p);
  if (!c) {
    const { id } = run(
      "INSERT INTO customers (tenant_id,name,phone,type,opt_in,created_at) VALUES (?,?,?,?,1,?)",
      tenantId, name || "WhatsApp " + p.slice(-4), p, "retail", now(),
    );
    c = get("SELECT * FROM customers WHERE id=?", id)!;
  }
  return c;
}

export function khataEntry(customerId: number, type: "debit" | "credit", amount: number, ref: string | null, note: string | null) {
  if (!(amount > 0)) throw new AppError(400, "Amount must be positive");
  return tx(() => {
    run("INSERT INTO khata_ledger (customer_id,type,amount,ref,note,created_at) VALUES (?,?,?,?,?,?)",
      customerId, type, amount, ref, note, now());
    run("UPDATE customers SET balance = balance + ? WHERE id=?", type === "debit" ? amount : -amount, customerId);
    return get("SELECT * FROM customers WHERE id=?", customerId)!;
  });
}

export interface SaleInput {
  station_id: number;
  product: string;
  litres?: number;
  amount?: number;
  payment_method: string;
  customer_id?: number | null;
  nozzle_id?: number | null;
  shift_id?: number | null;
  vehicle_no?: string | null;
  slip_no?: string | null;
  created_at?: string;
  /** Internal only: bill at this rate (e.g. litres pumped before a price change). Never taken from user input. */
  rate?: number;
}

export function recordSale(tenantId: number, s: SaleInput): Row {
  const station = get("SELECT * FROM stations WHERE id=? AND tenant_id=?", s.station_id, tenantId);
  if (!station) throw new AppError(404, "Station not found");
  const rate = s.rate ?? priceOf(tenantId, s.product);
  const litres = s.litres ?? (s.amount ? s.amount / rate : 0);
  if (!(litres > 0)) throw new AppError(400, "Litres or amount required");
  const amount = Math.round(litres * rate * 100) / 100;
  const customer = s.customer_id ? get("SELECT * FROM customers WHERE id=? AND tenant_id=?", s.customer_id, tenantId) : undefined;
  if (s.payment_method === "khata") {
    if (!customer) throw new AppError(400, "Khata sale needs a customer");
    if (customer.balance + amount > customer.credit_limit)
      throw new AppError(400, `Credit limit exceeded: balance ${pkr(customer.balance)}, limit ${pkr(customer.credit_limit)}`);
  }
  const tank = get("SELECT * FROM tanks WHERE station_id=? AND product=? ORDER BY current_l DESC LIMIT 1", s.station_id, s.product);
  if (!tank) throw new AppError(400, `No ${s.product} tank at this station`);
  if (tank.current_l < litres) throw new AppError(400, `Not enough stock in ${tank.name}`);
  const ts = s.created_at ?? now();
  return tx(() => {
    const { id } = run(
      `INSERT INTO sales (station_id,shift_id,customer_id,nozzle_id,product,litres,rate,amount,payment_method,vehicle_no,slip_no,created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
      s.station_id, s.shift_id ?? null, customer?.id ?? null, s.nozzle_id ?? null, s.product,
      round2(litres), rate, amount, s.payment_method, s.vehicle_no?.toUpperCase() ?? null, s.slip_no ?? null, ts,
    );
    run("UPDATE tanks SET current_l = current_l - ? WHERE id=?", litres, tank.id);
    if (s.nozzle_id) run("UPDATE nozzles SET totalizer = totalizer + ? WHERE id=?", litres, s.nozzle_id);
    if (customer) {
      run("UPDATE customers SET last_visit_at=?, loyalty_points = loyalty_points + ? WHERE id=?",
        ts, Math.floor(amount / 100), customer.id);
      if (s.payment_method === "khata") {
        run(`INSERT INTO khata_ledger (customer_id,type,amount,ref,note,product,litres,rate,vehicle_no,slip_no,station_id,created_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
          customer.id, "debit", amount, `SALE-${id}`, `${round2(litres)}L ${s.product} @ Rs ${rate}`, s.product, round2(litres), rate,
          s.vehicle_no?.toUpperCase() ?? null, s.slip_no ?? null, s.station_id, ts);
        run("UPDATE customers SET balance = balance + ? WHERE id=?", amount, customer.id);
      }
    }
    return get("SELECT * FROM sales WHERE id=?", id)!;
  });
}

export const round2 = (n: number) => Math.round(n * 100) / 100;

export function createAlert(
  tenantId: number,
  a: { station_id?: number | null; type: string; severity: "info" | "warning" | "critical"; title: string; body?: string; dedupe_key?: string },
): Row | null {
  if (a.dedupe_key) {
    const since = new Date(Date.now() - 20 * 3600_000).toISOString();
    const dup = get("SELECT id FROM alerts WHERE tenant_id=? AND dedupe_key=? AND created_at > ?", tenantId, a.dedupe_key, since);
    if (dup) return null;
  }
  const { id } = run(
    "INSERT INTO alerts (tenant_id,station_id,type,severity,title,body,dedupe_key,created_at) VALUES (?,?,?,?,?,?,?,?)",
    tenantId, a.station_id ?? null, a.type, a.severity, a.title, a.body ?? null, a.dedupe_key ?? null, now(),
  );
  return get("SELECT * FROM alerts WHERE id=?", id)!;
}

export function paymentLink(customer: Row, amount: number): string {
  const ref = `KH${customer.id}-${Date.now().toString(36).toUpperCase()}`;
  return `${config.paymentLinkBase}?ref=${ref}&amt=${Math.round(amount)}&phone=${customer.phone}`;
}

export function getStations(tenantId: number) {
  return all("SELECT * FROM stations WHERE tenant_id=? ORDER BY id", tenantId);
}
