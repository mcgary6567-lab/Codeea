import { all, get, run, tx, now, type Row } from "./db.js";
import { config } from "./config.js";
import { digitalMethods } from "./routes/lookups.js";

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

/** Rupees the Pakistani way: Rs 15,20,945 (lakh / crore grouping, same as the app screens). */
export const pkr = (n: number) => "Rs " + Math.round(n).toLocaleString("en-IN");
export const rateFmt = (n: number) => "Rs " + n.toFixed(2);

export function currentPrices(tenantId: number, at: string = now()): Record<string, { price: number; effective_from: string }> {
  const rows = all(
    `SELECT p.product, p.price, p.effective_from FROM prices p
     WHERE p.tenant_id=? AND p.id = (SELECT id FROM prices p2 WHERE p2.tenant_id=p.tenant_id AND p2.product=p.product
       AND p2.effective_from <= ? ORDER BY p2.effective_from DESC, p2.id DESC LIMIT 1)`,
    tenantId, at,
  );
  const out: Record<string, { price: number; effective_from: string }> = {};
  for (const r of rows) out[r.product] = { price: r.price, effective_from: r.effective_from };
  return out;
}

export function priceOf(tenantId: number, product: string, at?: string): number {
  const p = currentPrices(tenantId, at)[product];
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

export function khataEntry(customerId: number, type: "debit" | "credit", amount: number, ref: string | null, note: string | null, accountId: number | null = null) {
  if (!(amount > 0)) throw new AppError(400, "Amount must be positive");
  return tx(() => {
    run("INSERT INTO khata_ledger (customer_id,type,amount,ref,note,created_at,account_id) VALUES (?,?,?,?,?,?,?)",
      customerId, type, amount, ref, note, now(), type === "credit" ? accountId : null);
    run("UPDATE customers SET balance = balance + ? WHERE id=?", type === "debit" ? amount : -amount, customerId);
    // a payment lifts an overdue hold
    if (type === "credit") run("UPDATE customers SET khata_blocked=0 WHERE id=?", customerId);
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
  created_by?: number | null;
  /** POS-generated id: the same sale synced twice (offline queue) is saved once. */
  client_uid?: string | null;
  /** pos (entered by staff) or meter (booked from the meter at settlement). */
  source?: "pos" | "meter";
  /** A manager may let a vehicle go over its daily litre limit. */
  override_limit?: boolean;
  /** Prepaid coupon scanned at the POS (payment_method "coupon"). */
  coupon_code?: string | null;
  /** Added at shift close (not entered live on the POS). */
  at_close?: boolean;
  /** Photo of the khata slip (parchi), taken with the POS camera. */
  photo_id?: number | null;
  /** Which bank's POS machine a card / digital sale went to (overrides the pos-map default). */
  account_id?: number | null;
  /** Internal only: bill at this rate (e.g. litres pumped before a price change). Never taken from user input. */
  rate?: number;
  /** Khata "card pending": fuel goes out now at the meter rate, but no rate is locked and no debt is raised —
   * it is billed to the khata only when the card / parchi is brought in, at that day's rate. */
  pending?: boolean;
  /** A fixed (lump-sum) rupee discount for a khata customer. The khata is billed the net: litres*rate − discount. */
  discount?: number;
}

export function recordSale(tenantId: number, s: SaleInput): Row {
  const station = get("SELECT * FROM stations WHERE id=? AND tenant_id=?", s.station_id, tenantId);
  if (!station) throw new AppError(404, "Station not found");
  if (s.client_uid) {
    const dup = get("SELECT * FROM sales WHERE client_uid=? AND station_id=?", s.client_uid, s.station_id);
    if (dup) return { ...dup, duplicate: true };
  }
  const rate = s.rate ?? priceOf(tenantId, s.product, s.created_at);
  // a prepaid coupon pays for exactly its value of fuel, once
  const coupon = s.payment_method === "coupon" ? get("SELECT * FROM fuel_coupons WHERE tenant_id=? AND code=?", tenantId, (s.coupon_code ?? "").toUpperCase().replace(/^PUMPAI-/, "").trim()) : null;
  if (s.payment_method === "coupon") {
    if (!coupon) throw new AppError(400, "Coupon not found. Scan it again or type the code.");
    if (coupon.status !== "active") throw new AppError(400, `Coupon ${coupon.code} is ${coupon.status === "used" ? `already used (${coupon.used_at?.slice(0, 10)})` : "cancelled"}`);
    if (coupon.expires_on && coupon.expires_on < new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10)) throw new AppError(400, `Coupon ${coupon.code} expired on ${coupon.expires_on}`);
    if (coupon.product && coupon.product !== s.product) throw new AppError(400, `Coupon ${coupon.code} is only for ${coupon.product}`);
  }
  const litres = coupon ? coupon.value / rate : s.litres ?? (s.amount ? s.amount / rate : 0);
  if (!(litres > 0)) throw new AppError(400, "Litres or amount required");
  const gross = Math.round(litres * rate * 100) / 100;
  // a fixed rupee discount for a khata customer: the khata is billed the net (litres*rate − discount)
  const discount = round2(s.discount ?? 0);
  if (discount < 0) throw new AppError(400, "Discount cannot be negative");
  if (discount > 0 && s.payment_method !== "khata") throw new AppError(400, "Discount is only for khata customers");
  if (discount >= gross) throw new AppError(400, `Discount ${pkr(discount)} is more than the fuel amount ${pkr(gross)}`);
  const amount = round2(gross - discount); // the net charged to the khata
  const customer = s.customer_id ? get("SELECT * FROM customers WHERE id=? AND tenant_id=?", s.customer_id, tenantId) : undefined;
  const pending = Boolean(s.pending);
  if (pending && s.payment_method !== "khata") throw new AppError(400, "Card-pending holds are only for khata accounts");
  if (pending && discount > 0) throw new AppError(400, "Give the discount when the card is cleared, not on a pending hold");
  if (s.payment_method === "khata") {
    if (!customer) throw new AppError(400, "Khata sale needs a customer");
    if (customer.khata_blocked) throw new AppError(400, `${customer.name}: khata is on hold because payment is overdue. Ask the manager.`);
    // a card-pending fill locks no rate and raises no debt yet, so the credit limit is only checked when it is cleared
    if (!pending && customer.balance + amount > customer.credit_limit)
      throw new AppError(400, `Credit limit exceeded: balance ${pkr(customer.balance)}, limit ${pkr(customer.credit_limit)}`);
  }
  if (s.payment_method === "wallet") {
    if (!customer) throw new AppError(400, "Choose the company whose wallet pays");
    if (customer.wallet_balance + 0.005 < amount) throw new AppError(400, `${customer.name}: wallet has ${pkr(customer.wallet_balance)}, this fill is ${pkr(amount)}. Ask them to top up.`);
  }
  // paying with loyalty points: 1 point = Rs 1
  const points = s.payment_method === "loyalty" ? Math.ceil(amount) : 0;
  if (s.payment_method === "loyalty") {
    if (!customer) throw new AppError(400, "Choose the customer whose points are used");
    if (customer.loyalty_points < points) throw new AppError(400, `${customer.name} has ${customer.loyalty_points} points (Rs ${customer.loyalty_points}); this sale needs ${points}`);
  }
  // a registered vehicle: right fuel, and not over its daily litre limit
  const veh = customer && s.vehicle_no ? get("SELECT * FROM vehicles WHERE customer_id=? AND UPPER(plate_no)=UPPER(?)", customer.id, s.vehicle_no.trim()) : null;
  if (veh?.fuel && veh.fuel !== s.product) throw new AppError(400, `${veh.plate_no} is registered for ${veh.fuel}, not ${s.product}`);
  if (veh?.daily_limit_l && !s.override_limit) {
    const since = new Date(Date.parse(new Date(Date.now() + 5 * 3600_000).toISOString().slice(0, 10) + "T00:00:00+05:00")).toISOString();
    const used = get("SELECT COALESCE(SUM(litres),0) l FROM sales WHERE customer_id=? AND UPPER(vehicle_no)=UPPER(?) AND created_at >= ?", customer!.id, veh.plate_no, since)!.l;
    if (used + litres > veh.daily_limit_l + 0.01)
      throw new AppError(400, `Daily limit for ${veh.plate_no} is ${veh.daily_limit_l} L; ${round2(used)} L already given today (${round2(Math.max(0, veh.daily_limit_l - used))} L left). Ask the manager.`);
  }
  const photoId = s.photo_id && get("SELECT id FROM photos WHERE id=? AND tenant_id=?", s.photo_id, tenantId) ? s.photo_id : null;
  const tank = get("SELECT * FROM tanks WHERE station_id=? AND product=? ORDER BY current_l DESC LIMIT 1", s.station_id, s.product);
  if (!tank) throw new AppError(400, `No ${s.product} tank at this station`);
  if (tank.current_l < litres) throw new AppError(400, `Not enough stock in ${tank.name}`);
  const ts = s.created_at ?? now();
  return tx(() => {
    // a bank account only applies to a card / digital (POS machine) sale — never cash / khata / coupon etc.
    const accountId = s.account_id && digitalMethods(tenantId).includes(s.payment_method) ? s.account_id : null;
    const { id } = run(
      `INSERT INTO sales (station_id,shift_id,customer_id,nozzle_id,product,litres,rate,amount,discount,payment_method,vehicle_no,slip_no,created_by,client_uid,source,coupon_id,photo_id,at_close,account_id,pending,over_limit,created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      s.station_id, s.shift_id ?? null, customer?.id ?? null, s.nozzle_id ?? null, s.product,
      round2(litres), rate, amount, discount, s.payment_method, s.vehicle_no?.toUpperCase() ?? null, s.slip_no ?? null, s.created_by ?? null, s.client_uid ?? null, s.source ?? (s.created_by ? "pos" : null), coupon?.id ?? null, photoId, s.at_close ? 1 : null, accountId, pending ? 1 : 0, s.override_limit ? 1 : 0, ts,
    );
    if (coupon && run("UPDATE fuel_coupons SET status='used', sale_id=?, used_at=?, used_by=? WHERE id=? AND status='active'", id, ts, String(s.created_by ?? ""), coupon.id).changes !== 1)
      throw new AppError(409, `Coupon ${coupon.code} was just used`);
    if (s.payment_method === "wallet") {
      run("UPDATE customers SET wallet_balance = wallet_balance - ? WHERE id=?", amount, customer!.id);
      run("INSERT INTO wallet_ledger (tenant_id,customer_id,type,amount,note,sale_id,created_at) VALUES (?,?,?,?,?,?,?)",
        tenantId, customer!.id, "fill", amount, `${round2(litres)}L ${s.product} @ Rs ${rate}${s.vehicle_no ? ` · ${s.vehicle_no.toUpperCase()}` : ""}`, id, ts);
    }
    run("UPDATE tanks SET current_l = current_l - ? WHERE id=?", litres, tank.id);
    if (s.nozzle_id) run("UPDATE nozzles SET totalizer = totalizer + ? WHERE id=?", litres, s.nozzle_id);
    if (customer && pending) {
      // card-pending: fuel is out (stock & meter already moved) but nothing is billed yet; just record the visit
      run("UPDATE customers SET last_visit_at=? WHERE id=?", ts, customer.id);
      // keep the slip photo (parchi) linked to the sale as proof until it is cleared
      if (photoId) run("UPDATE photos SET ref=? WHERE id=? AND ref IS NULL", `sale:${id}`, photoId);
    } else if (customer) {
      run("UPDATE customers SET last_visit_at=?, loyalty_points = loyalty_points + ? WHERE id=?",
        ts, points ? -points : Math.floor(amount / 100), customer.id);
      if (points) run("INSERT INTO loyalty_redemptions (tenant_id,customer_id,points,sale_id,created_at) VALUES (?,?,?,?,?)", tenantId, customer.id, points, id, ts);
      if (s.payment_method === "khata") {
        const k = run(`INSERT INTO khata_ledger (customer_id,type,amount,ref,note,product,litres,rate,vehicle_no,slip_no,station_id,created_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
          customer.id, "debit", amount, `SALE-${id}`, `${round2(litres)}L ${s.product} @ Rs ${rate}${discount > 0 ? ` − ${pkr(discount)} discount` : ""}`, s.product, round2(litres), rate,
          s.vehicle_no?.toUpperCase() ?? null, s.slip_no ?? null, s.station_id, ts);
        // the slip photo shows on the khata statement like any other proof photo
        if (photoId) run("UPDATE photos SET ref=? WHERE id=? AND ref IS NULL", `khata:${k.id}`, photoId);
        run("UPDATE customers SET balance = balance + ? WHERE id=?", amount, customer.id);
      }
    }
    return get("SELECT * FROM sales WHERE id=?", id)!;
  });
}

export const round2 = (n: number) => Math.round(n * 100) / 100;

/** A salesman can undo their own sale for this long. */
export const UNDO_SECONDS = 120;

/** Reverse a sale completely: stock, nozzle meter, khata balance and loyalty points. */
export function undoSale(sale: Row) {
  tx(() => {
    const tank = get("SELECT id FROM tanks WHERE station_id=? AND product=? ORDER BY current_l DESC LIMIT 1", sale.station_id, sale.product);
    if (tank) run("UPDATE tanks SET current_l = current_l + ? WHERE id=?", sale.litres, tank.id);
    if (sale.nozzle_id) run("UPDATE nozzles SET totalizer = totalizer - ? WHERE id=?", sale.litres, sale.nozzle_id);
    if (sale.customer_id) {
      if (sale.payment_method === "loyalty") {
        const r = get("SELECT points FROM loyalty_redemptions WHERE sale_id=?", sale.id);
        run("UPDATE customers SET loyalty_points = loyalty_points + ? WHERE id=?", r?.points ?? Math.ceil(sale.amount), sale.customer_id);
        run("DELETE FROM loyalty_redemptions WHERE sale_id=?", sale.id);
      } else run("UPDATE customers SET loyalty_points = MAX(0, loyalty_points - ?) WHERE id=?", Math.floor(sale.amount / 100), sale.customer_id);
      // a still-pending card hold raised no khata debit, so there is nothing to reverse; a cleared one was billed at its clear rate
      if (sale.payment_method === "khata" && !sale.pending) {
        const billed = sale.cleared_at ? round2(sale.litres * sale.clear_rate) : sale.amount;
        run("DELETE FROM khata_ledger WHERE customer_id=? AND ref=?", sale.customer_id, `SALE-${sale.id}`);
        run("UPDATE customers SET balance = balance - ? WHERE id=?", billed, sale.customer_id);
      }
    }
    if (sale.coupon_id) run("UPDATE fuel_coupons SET status='active', sale_id=NULL, used_at=NULL, used_by=NULL WHERE id=?", sale.coupon_id);
    if (sale.payment_method === "wallet" && sale.customer_id) {
      run("DELETE FROM wallet_ledger WHERE sale_id=? AND type='fill'", sale.id);
      run("UPDATE customers SET wallet_balance = wallet_balance + ? WHERE id=?", sale.amount, sale.customer_id);
    }
    run("DELETE FROM sales WHERE id=?", sale.id);
  });
}

export function audit(tenantId: number, user: { id: number; name: string } | null, action: string, ref: string, data: unknown) {
  run("INSERT INTO audit_log (tenant_id,user_id,user_name,action,ref,data,created_at) VALUES (?,?,?,?,?,?,?)",
    tenantId, user?.id ?? null, user?.name ?? null, action, ref, JSON.stringify(data), now());
}

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

/** The pump's online payment page with this khata's reference — "" when the pump has none (no link is sent then). */
export function paymentLink(customer: Row, amount: number): string {
  if (!config.paymentLinkBase) return "";
  const ref = `KH${customer.id}-${Date.now().toString(36).toUpperCase()}`;
  return `${config.paymentLinkBase}?ref=${ref}&amt=${Math.round(amount)}`;
}
