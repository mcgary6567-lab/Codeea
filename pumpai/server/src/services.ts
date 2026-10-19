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
/** A party balance in words: positive = they owe us, negative = their advance lies with us. */
export const dueText = (n: number) => (n < -0.5 ? `Advance ${pkr(-n)} (humare paas jama)` : `Due ${pkr(n)}`);
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
  /** khata = lump-sum on a khata fill (default when the method is khata); direct = walk-in discount on cash / card / online. */
  discount_kind?: "khata" | "direct";
  /** direct discount: who got it / why (free text) — may be added right after the sale. */
  discount_ref?: string | null;
  /** payment_method 'wholesale': the fuel went out of the nozzle to a wholesale client at their rate.
   * The litres/stock/meter are this sales row; the money (receivable/revenue/payment) is a wholesale_txns supply the /sales route adds. */
  wholesale_client_id?: number | null;
}

export function recordSale(tenantId: number, s: SaleInput): Row {
  const station = get("SELECT * FROM stations WHERE id=? AND tenant_id=?", s.station_id, tenantId);
  if (!station) throw new AppError(404, "Station not found");
  if (s.client_uid) {
    const dup = get("SELECT * FROM sales WHERE client_uid=? AND station_id=?", s.client_uid, s.station_id);
    if (dup) return { ...dup, duplicate: true };
  }
  const rate = s.rate ?? priceOf(tenantId, s.product, s.created_at);
  // A wholesale client fuelled straight from the nozzle, at their own wholesale rate. The litres leave the tank and run
  // through the meter like any sale (so the stock register and shift settlement count them once), but the money is NOT
  // retail cash/khata — the /sales route books it to the client's wholesale khata (a wholesale_txns supply). So this row
  // is excluded from retail revenue and the ledger's Fuel-sales voucher; only its litres/stock matter here.
  if (s.payment_method === "wholesale") {
    const wlitres = round2(s.litres ?? 0);
    if (!(wlitres > 0)) throw new AppError(400, "Litres required");
    if (!s.wholesale_client_id) throw new AppError(400, "Choose the wholesale client");
    if (!(rate > 0)) throw new AppError(400, "No wholesale rate for this client/product");
    const wtank = get("SELECT * FROM tanks WHERE station_id=? AND product=? ORDER BY current_l DESC LIMIT 1", s.station_id, s.product);
    if (!wtank) throw new AppError(400, `No ${s.product} tank at this station`);
    if (wtank.current_l < wlitres) throw new AppError(400, `Not enough stock in ${wtank.name}`);
    const wamount = round2(wlitres * rate), wts = s.created_at ?? now();
    return tx(() => {
      const { id } = run(
        `INSERT INTO sales (station_id,shift_id,nozzle_id,product,litres,rate,amount,payment_method,vehicle_no,created_by,client_uid,source,at_close,wholesale_client_id,created_at)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
        s.station_id, s.shift_id ?? null, s.nozzle_id ?? null, s.product, wlitres, rate, wamount, "wholesale",
        s.vehicle_no?.toUpperCase() ?? null, s.created_by ?? null, s.client_uid ?? null, s.source ?? (s.created_by ? "pos" : null),
        s.at_close ? 1 : null, s.wholesale_client_id ?? null, wts,
      );
      run("UPDATE tanks SET current_l = current_l - ? WHERE id=?", wlitres, wtank.id);
      if (s.nozzle_id) run("UPDATE nozzles SET totalizer = totalizer + ? WHERE id=?", wlitres, s.nozzle_id);
      return get("SELECT * FROM sales WHERE id=?", id);
    });
  }
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
  const discountKind = discount > 0 ? (s.discount_kind ?? (s.payment_method === "khata" ? "khata" : "direct")) : null;
  if (discountKind === "khata" && s.payment_method !== "khata") throw new AppError(400, "A khata discount needs a khata customer — use Direct discount for a walk-in");
  if (discountKind === "direct" && ["khata", "coupon", "loyalty"].includes(s.payment_method)) throw new AppError(400, "Direct discount is for cash / card / online sales (a khata fill has its own discount)");
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
      throw new AppError(400, customer.balance < -0.5
        ? `Advance khatam: ${pkr(-customer.balance)} advance baqi hai, yeh sale ${pkr(amount)} ki hai${customer.credit_limit > 0 ? ` (limit ${pkr(customer.credit_limit)})` : " aur khata limit nahi"}`
        : `Credit limit exceeded: balance ${pkr(customer.balance)}, limit ${pkr(customer.credit_limit)}`);
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
      `INSERT INTO sales (station_id,shift_id,customer_id,nozzle_id,product,litres,rate,amount,discount,payment_method,vehicle_no,slip_no,created_by,client_uid,source,coupon_id,photo_id,at_close,account_id,pending,over_limit,created_at,discount_kind,discount_ref)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      s.station_id, s.shift_id ?? null, customer?.id ?? null, s.nozzle_id ?? null, s.product,
      round2(litres), rate, amount, discount, s.payment_method, s.vehicle_no?.toUpperCase() ?? null, s.slip_no ?? null, s.created_by ?? null, s.client_uid ?? null, s.source ?? (s.created_by ? "pos" : null), coupon?.id ?? null, photoId, s.at_close ? 1 : null, accountId, pending ? 1 : 0, s.override_limit ? 1 : 0, ts,
      discountKind, discountKind === "direct" ? (s.discount_ref?.trim() || null) : null,
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

/**
 * Undo everything recordSale (and a later /sales/clear) did for one sale, then delete the sale row.
 * Mirrors recordSale leg-for-leg so every book reverts exactly: the derived ledger re-reads these same
 * source tables, so restoring them leaves the rupee tally untouched. Must run inside the caller's tx.
 * Returns a short description of what was reversed (for the audit trail).
 */
export function reverseSale(tenantId: number, sale: Row): string[] {
  const did: string[] = [];
  // 1) khata leg — recordSale (and /sales/clear for a cleared card-hold) write khata_ledger rows ref SALE-<id>.
  //    Delete each and move the customer balance back by exactly what that row did.
  if (sale.customer_id) {
    for (const k of all("SELECT * FROM khata_ledger WHERE ref=? ", `SALE-${sale.id}`)) {
      run("DELETE FROM khata_ledger WHERE id=?", k.id);
      run("UPDATE customers SET balance = balance + ? WHERE id=? AND tenant_id=?", k.type === "credit" ? k.amount : -k.amount, sale.customer_id, tenantId);
      did.push(`khata ${k.type} ${pkr(k.amount)} wapas`);
    }
  }
  // 2) wallet leg
  if (sale.payment_method === "wallet" && sale.customer_id) {
    run("UPDATE customers SET wallet_balance = wallet_balance + ? WHERE id=? AND tenant_id=?", sale.amount, sale.customer_id, tenantId);
    run("DELETE FROM wallet_ledger WHERE sale_id=? AND tenant_id=?", sale.id, tenantId);
    did.push(`wallet ${pkr(sale.amount)} wapas`);
  }
  // 3) prepaid coupon — reactivate it so it can be used again
  if (sale.coupon_id) {
    run("UPDATE fuel_coupons SET status='active', sale_id=NULL, used_at=NULL, used_by=NULL WHERE id=? AND tenant_id=? AND status='used'", sale.coupon_id, tenantId);
    did.push("coupon dobara active");
  }
  // 4) loyalty points — give back any points redeemed, take back any points earned (points are not rupee-tallied,
  //    but keep them honest). recordSale: redeemed = ceil(amount) for a loyalty sale (a loyalty_redemptions row);
  //    earned = floor(amount/100) for any other non-pending customer sale.
  if (sale.customer_id && !sale.pending) {
    const redeemed = get("SELECT COALESCE(SUM(points),0) p FROM loyalty_redemptions WHERE sale_id=? AND tenant_id=?", sale.id, tenantId)!.p as number;
    if (redeemed) run("DELETE FROM loyalty_redemptions WHERE sale_id=? AND tenant_id=?", sale.id, tenantId);
    const earned = sale.payment_method === "loyalty" ? 0 : Math.floor(sale.amount / 100);
    const delta = redeemed - earned;
    if (delta) run("UPDATE customers SET loyalty_points = MAX(0, loyalty_points + ?) WHERE id=? AND tenant_id=?", delta, sale.customer_id, tenantId);
  }
  // 4b) a wholesale nozzle sale: void the linked wholesale supply + on-the-spot payment, pull back an in-hand cheque (as undoSale)
  if (sale.payment_method === "wholesale") {
    run("UPDATE wholesale_txns SET voided=1, void_reason='Sale voided (corrections)' WHERE sale_id=? AND voided=0", sale.id);
    run("UPDATE wholesale_cheques SET status='cancelled', updated_at=? WHERE sale_id=? AND status='in_hand'", now(), sale.id);
    did.push("wholesale supply/payment void");
  }
  // 5) physical stock + meter — add the litres back to the product's tank and wind the nozzle totalizer back.
  //    recordSale picked the fullest tank of that product at the station; reverse to the same selection.
  const tank = get("SELECT id FROM tanks WHERE station_id=? AND product=? ORDER BY current_l DESC LIMIT 1", sale.station_id, sale.product);
  if (tank) run("UPDATE tanks SET current_l = current_l + ? WHERE id=?", sale.litres, tank.id);
  if (sale.nozzle_id) run("UPDATE nozzles SET totalizer = MAX(0, totalizer - ?) WHERE id=?", sale.litres, sale.nozzle_id);
  // 6) finally remove the sale itself (cash / card money is derived straight from this row, so it goes with it)
  run("DELETE FROM sales WHERE id=? AND station_id IN (SELECT id FROM stations WHERE tenant_id=?)", sale.id, tenantId);
  return did;
}

export const round2 = (n: number) => Math.round(n * 100) / 100;

/* ---------------- Party balances (suppliers / wholesale clients / thekedars) ----------------
 * One parameterised balance engine behind supplierOwed / clientDue / thekedarDue.
 * Positive = they owe us; negative = their advance lies with us. opening_balance is the go-live carry.
 * Each kind keeps its own sign rule and voided handling — the SQL is byte-for-byte the old per-helper SQL. */
export type PartyKind = "supplier" | "client" | "thekedar";
const PARTY: Record<PartyKind, { table: string; fk: string; parent: string; sign: string; voided: boolean }> = {
  supplier: { table: "supplier_txns", fk: "supplier_id", parent: "suppliers",
    sign: "CASE type WHEN 'purchase' THEN amount WHEN 'payment' THEN -amount ELSE amount END", voided: false },
  client: { table: "wholesale_txns", fk: "client_id", parent: "wholesale_clients",
    sign: "CASE type WHEN 'supply' THEN amount WHEN 'return' THEN -amount WHEN 'payment' THEN -amount ELSE amount END", voided: true },
  thekedar: { table: "carriage_txns", fk: "thekedar_id", parent: "thekedars",
    sign: "CASE type WHEN 'payment' THEN -amount ELSE amount END", voided: true },
};

/** Balance of one party, optionally as of a date (txn_date < before). */
export function partyBalance(kind: PartyKind, id: number, before?: string): number {
  const p = PARTY[kind];
  const open = get(`SELECT opening_balance FROM ${p.parent} WHERE id=?`, id)!.opening_balance as number;
  const v = p.voided ? "AND voided=0 " : "";
  const r = get(`SELECT COALESCE(SUM(${p.sign}),0) s FROM ${p.table} WHERE ${p.fk}=? ${v}${before ? "AND txn_date < ?" : ""}`,
    ...(before ? [id, before] : [id]))!;
  return round2(open + Number(r.s));
}

/** Every party of one kind for a tenant → its balance, in a single query (no per-row N+1). */
export function partyBalanceAll(kind: PartyKind, tenantId: number): Map<number, number> {
  const p = PARTY[kind];
  const v = p.voided ? "AND x.voided=0 " : "";
  const m = new Map<number, number>();
  for (const r of all(`SELECT a.id, a.opening_balance + COALESCE((SELECT SUM(${p.sign}) FROM ${p.table} x WHERE x.${p.fk}=a.id ${v}),0) s
      FROM ${p.parent} a WHERE a.tenant_id=?`, tenantId)) m.set(Number(r.id), round2(Number(r.s)));
  return m;
}

/** The cash a fuel / shop sale put in the salesman's bag: only a 'cash' sale is counted at shift close. */
export const saleCash = (payment_method: string | null | undefined, amount: number | null | undefined) => (payment_method === "cash" ? round2(amount ?? 0) : 0);

/**
 * A correction (void / edit) on a sale that sits on a CLOSED shift changes the cash that shift's bag should have held.
 * The ledger is derived from the sale rows, but the cash book runs on the shift's stored figures (cash_actual /
 * handed_amount = what was physically counted; variance = counted − expected is the "short / over" journal line), so the
 * two drift apart unless the shift's expected cash follows. Mirrors rebookLateSale: `freedCash` > 0 (cash sale voided, or
 * moved to khata / card / a lower amount) lowers cash_expected and re-derives variance — the counted cash stays the truth,
 * the freed rupees show as "over". `freedCash` < 0 (khata → cash, or a higher cash amount) is refused: that cash was never
 * counted in the bag, so it cannot be added to a closed shift. An open shift (figures are computed at close) or no shift
 * (counter sale) is left alone. Must run inside the caller's tx. Returns what moved, for the audit row / response.
 */
export function adjustClosedShiftCash(shift: Row | null | undefined, freedCash: number, what: string) {
  if (!shift || shift.status !== "closed") return null;
  const freed = round2(freedCash);
  if (Math.abs(freed) < 0.005) return null;
  if (freed < 0)
    throw new AppError(400, `Band shift me cash nahi badh sakta — ${what} se shift #${shift.id} ka cash ${pkr(-freed)} barhta hai, jo close par gina nahi gaya tha. Is sale ko void karke aaj ki khuli shift me dobara daalein.`);
  const expected = round2((shift.cash_expected ?? 0) - freed);
  const variance = round2((shift.cash_actual ?? 0) - expected);
  run("UPDATE shifts SET cash_expected=?, variance=? WHERE id=?", expected, variance, shift.id);
  return { shift_id: shift.id as number, freed_cash: freed, cash_expected_before: round2(shift.cash_expected ?? 0), cash_expected: expected, variance };
}

/**
 * Volume Correction Factor to 15°C (simplified ASTM linear model): fuel expands in heat, so the same litres
 * measured on a hot afternoon read higher than on a cold morning. V15 = Vobserved × (1 − α·(T − 15)), where α
 * (per °C) is ~0.00120 for petrol, ~0.00083 for diesel. Used to show whether a dip "loss" is just temperature.
 */
const VCF_ALPHA: Record<string, number> = { PMG: 0.00120, HOBC: 0.00120, HSD: 0.00083, LDO: 0.00083, KERO: 0.00090 };
export const vcfAlpha = (product: string) => VCF_ALPHA[product] ?? 0.0011;
export const correctTo15 = (litres: number, product: string, tempC: number) => round2(litres * (1 - vcfAlpha(product) * (tempC - 15)));

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
    // a wholesale nozzle sale: reverse the linked wholesale supply + any on-the-spot payment, and pull back an in-hand cheque
    if (sale.payment_method === "wholesale") {
      run("UPDATE wholesale_txns SET voided=1, void_reason='POS sale undone' WHERE sale_id=? AND voided=0", sale.id);
      run("UPDATE wholesale_cheques SET status='cancelled', updated_at=? WHERE sale_id=? AND status='in_hand'", now(), sale.id);
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
