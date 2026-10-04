/**
 * Lubricants, filters, tuck shop and tyre shop: items per station with stock, barcode sale from the
 * POS (cash / digital / khata), stock-in from suppliers, adjustments, low-stock alerts and profit.
 * Shop cash goes into the salesman's shift cash like fuel cash.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, pkDayStart, pkStart, pkEnd } from "../db.js";
import { h, parse, tid, requirePerm, scopedStation, can } from "../auth.js";
import { AppError, round2, pkr, createAlert, audit, UNDO_SECONDS } from "../services.js";
import { notify, staff } from "../notifications.js";
import { shiftSummary } from "../shifts.js";
import { receiptUrl } from "../billing.js";

export const shop = Router();
export const SHOP_CATEGORIES = ["lubricant", "filter", "coolant", "tyre", "battery", "tuck", "service", "other"] as const;

const itemBody = z.object({
  station_id: z.number(), name: z.string().min(2).max(80), category: z.enum(SHOP_CATEGORIES).default("other"),
  sku: z.string().max(40).optional().nullable(), barcode: z.string().max(40).optional().nullable(), unit: z.string().max(12).default("pc"),
  cost: z.number().min(0).default(0), price: z.number().positive(), reorder_level: z.number().min(0).default(0), stock: z.number().min(0).optional(),
});

function ownItem(t: number, id: number) {
  const i = get("SELECT * FROM shop_items WHERE id=? AND tenant_id=?", id, t);
  if (!i) throw new AppError(404, "Item not found");
  return i;
}

shop.get("/shop/items", requirePerm("sales.create"), h((req) => {
  const station = scopedStation(req, req.query.station_id ? Number(req.query.station_id) : null);
  const bc = req.query.barcode ? String(req.query.barcode) : null;
  return all(`SELECT i.*, s.name station_name FROM shop_items i JOIN stations s ON s.id=i.station_id WHERE i.tenant_id=?
    ${station ? "AND i.station_id=" + Number(station) : ""} ${bc ? "AND (i.barcode=? OR i.sku=?)" : ""} AND (i.active=1 OR ?) ORDER BY i.category, i.name`,
    tid(req), ...(bc ? [bc, bc] : []), req.query.all ? 1 : 0).map((i) => (can(req.user, "stock.manage") ? i : { ...i, cost: undefined }));
}));

shop.post("/shop/items", requirePerm("stock.manage"), h((req) => {
  const b = parse(itemBody, req.body);
  if (!get("SELECT id FROM stations WHERE id=? AND tenant_id=?", b.station_id, tid(req))) throw new AppError(400, "Station not found");
  if (b.barcode && get("SELECT id FROM shop_items WHERE tenant_id=? AND station_id=? AND barcode=?", tid(req), b.station_id, b.barcode)) throw new AppError(400, "This barcode is already used at this station");
  const { id } = run(`INSERT INTO shop_items (tenant_id,station_id,sku,barcode,name,category,unit,cost,price,stock,reorder_level,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
    tid(req), b.station_id, b.sku ?? null, b.barcode ?? null, b.name, b.category, b.unit, b.cost, b.price, b.stock ?? 0, b.reorder_level, now());
  if (b.stock) run("INSERT INTO shop_moves (item_id,type,qty,cost,note,created_by,created_at) VALUES (?,?,?,?,?,?,?)", id, "opening", b.stock, b.cost, "Opening stock", req.user!.name, now());
  return get("SELECT * FROM shop_items WHERE id=?", id);
}));

shop.patch("/shop/items/:id", requirePerm("stock.manage"), h((req) => {
  const i = ownItem(tid(req), Number(req.params.id));
  const b = parse(itemBody.omit({ station_id: true, stock: true }).partial().extend({ active: z.boolean().optional() }), req.body);
  const m = { ...i, ...b, active: b.active === undefined ? i.active : b.active ? 1 : 0 };
  run("UPDATE shop_items SET name=?, category=?, sku=?, barcode=?, unit=?, cost=?, price=?, reorder_level=?, active=? WHERE id=?",
    m.name, m.category, m.sku ?? null, m.barcode ?? null, m.unit, m.cost, m.price, m.reorder_level, m.active, i.id);
  return get("SELECT * FROM shop_items WHERE id=?", i.id);
}));

/** Stock received (purchase): the average cost is updated. */
shop.post("/shop/items/:id/stock-in", requirePerm("stock.manage"), h((req) => {
  const i = ownItem(tid(req), Number(req.params.id));
  const b = parse(z.object({ qty: z.number().positive(), cost: z.number().min(0).optional(), supplier: z.string().max(80).optional().nullable(), ref: z.string().max(40).optional().nullable() }), req.body);
  const unitCost = b.cost ?? i.cost;
  const avg = i.stock + b.qty > 0 ? round2((Math.max(0, i.stock) * i.cost + b.qty * unitCost) / (Math.max(0, i.stock) + b.qty)) : unitCost;
  tx(() => {
    run("UPDATE shop_items SET stock = stock + ?, cost=? WHERE id=?", b.qty, avg, i.id);
    run("INSERT INTO shop_moves (item_id,type,qty,cost,ref,note,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)", i.id, "purchase", b.qty, unitCost, b.ref ?? null, b.supplier ?? null, req.user!.name, now());
  });
  return get("SELECT * FROM shop_items WHERE id=?", i.id);
}));

/** Stock count / damage / expiry: set the real quantity; the difference is logged. */
shop.post("/shop/items/:id/adjust", requirePerm("stock.manage"), h((req) => {
  const i = ownItem(tid(req), Number(req.params.id));
  const b = parse(z.object({ counted: z.number().min(0), reason: z.string().min(2).max(120) }), req.body);
  const diff = round2(b.counted - i.stock);
  tx(() => {
    run("UPDATE shop_items SET stock=? WHERE id=?", b.counted, i.id);
    run("INSERT INTO shop_moves (item_id,type,qty,cost,note,created_by,created_at) VALUES (?,?,?,?,?,?,?)", i.id, "adjust", diff, i.cost, b.reason, req.user!.name, now());
  });
  return { ...get("SELECT * FROM shop_items WHERE id=?", i.id)!, difference: diff };
}));

shop.get("/shop/items/:id/moves", requirePerm("stock.manage"), h((req) => {
  const i = ownItem(tid(req), Number(req.params.id));
  return all("SELECT * FROM shop_moves WHERE item_id=? ORDER BY id DESC LIMIT 100", i.id);
}));

/** Sell shop items from the POS. */
shop.post("/shop/sales", requirePerm("sales.create"), h(async (req) => {
  const t = tid(req);
  const b = parse(z.object({
    station_id: z.number(), lines: z.array(z.object({ item_id: z.number(), qty: z.number().positive().max(10_000) })).min(1).max(50),
    payment_method: z.enum(["cash", "card", "jazzcash", "easypaisa", "raast", "khata"]), customer_id: z.number().nullable().optional(),
    client_uid: z.string().min(8).max(64).nullable().optional(),
  }), req.body);
  const stationId = scopedStation(req, b.station_id)!;
  if (b.client_uid) { const dup = get("SELECT * FROM shop_sales WHERE client_uid=?", b.client_uid); if (dup) return { ...dup, duplicate: true }; }
  const salesman = req.user!.role === "salesman";
  const shift = salesman
    ? get("SELECT id FROM shifts WHERE station_id=? AND status='open' AND attendant=? ORDER BY id DESC LIMIT 1", stationId, req.user!.name)
    : get("SELECT id FROM shifts WHERE station_id=? AND status='open' ORDER BY id DESC LIMIT 1", stationId);
  if (salesman && !shift) throw new AppError(400, "Start your shift first");
  const items = b.lines.map((l) => {
    const i = get("SELECT * FROM shop_items WHERE id=? AND tenant_id=? AND station_id=? AND active=1", l.item_id, t, stationId);
    if (!i) throw new AppError(400, "Item not found at this station");
    if (i.stock < l.qty) throw new AppError(400, `Only ${i.stock} ${i.unit} of ${i.name} in stock`);
    return { i, qty: l.qty };
  });
  const total = round2(items.reduce((a, x) => a + x.qty * x.i.price, 0));
  const cost = round2(items.reduce((a, x) => a + x.qty * x.i.cost, 0));
  const customer = b.customer_id ? get("SELECT * FROM customers WHERE id=? AND tenant_id=?", b.customer_id, t) : null;
  if (b.payment_method === "khata") {
    if (!customer) throw new AppError(400, "Khata sale needs a customer");
    if (customer.balance + total > customer.credit_limit) throw new AppError(400, `Credit limit exceeded: balance ${pkr(customer.balance)}, limit ${pkr(customer.credit_limit)}`);
  }
  const low: string[] = [];
  const id = tx(() => {
    const { id } = run("INSERT INTO shop_sales (tenant_id,station_id,shift_id,customer_id,payment_method,total,cost_total,client_uid,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
      t, stationId, shift?.id ?? null, customer?.id ?? null, b.payment_method, total, cost, b.client_uid ?? null, req.user!.id, now());
    for (const { i, qty } of items) {
      run("INSERT INTO shop_sale_lines (sale_id,item_id,qty,price,cost) VALUES (?,?,?,?,?)", id, i.id, qty, i.price, i.cost);
      run("UPDATE shop_items SET stock = stock - ? WHERE id=?", qty, i.id);
      run("INSERT INTO shop_moves (item_id,type,qty,cost,ref,created_by,created_at) VALUES (?,?,?,?,?,?,?)", i.id, "sale", -qty, i.cost, `shop:${id}`, req.user!.name, now());
      if (i.reorder_level > 0 && i.stock - qty <= i.reorder_level) low.push(`${i.name} (${round2(i.stock - qty)} ${i.unit} left)`);
    }
    if (b.payment_method === "khata" && customer) {
      run(`INSERT INTO khata_ledger (customer_id,type,amount,ref,note,station_id,created_at) VALUES (?,?,?,?,?,?,?)`,
        customer.id, "debit", total, `SHOP-${id}`, items.map((x) => `${x.qty} × ${x.i.name}`).join(", "), stationId, now());
      run("UPDATE customers SET balance = balance + ? WHERE id=?", total, customer.id);
    }
    return id;
  });
  if (low.length) {
    const a = createAlert(t, { station_id: stationId, type: "shop_low_stock", severity: "warning", title: `Shop stock low: ${low.join(", ")}`, dedupe_key: `shoplow-${stationId}-${low.join("|")}` });
    if (a) await notify(t, staff(t, ["manager", "admin"]), { type: "shop_low_stock", title: a.title, body: "Order more from the supplier." });
  }
  return { ...get("SELECT * FROM shop_sales WHERE id=?", id)!, receipt_url: receiptUrl(t, "s", id), lines: all("SELECT l.*, i.name, i.unit FROM shop_sale_lines l JOIN shop_items i ON i.id=l.item_id WHERE l.sale_id=?", id),
    shift_summary: shift ? shiftSummary(shift.id) : null };
}));

shop.post("/shop/sales/:id/undo", requirePerm("sales.create"), h((req) => {
  const s = get("SELECT ss.*, sh.status shift_status FROM shop_sales ss LEFT JOIN shifts sh ON sh.id=ss.shift_id WHERE ss.id=? AND ss.tenant_id=?", Number(req.params.id), tid(req));
  if (!s) throw new AppError(404, "Sale not found");
  if (req.user!.role === "salesman" && (s.created_by !== req.user!.id || Date.now() - Date.parse(s.created_at) > UNDO_SECONDS * 1000))
    throw new AppError(400, "Undo time (2 minutes) has passed. Ask the manager.");
  if (s.shift_id && s.shift_status !== "open") throw new AppError(400, "The shift is closed; this sale can no longer be undone");
  tx(() => {
    for (const l of all("SELECT * FROM shop_sale_lines WHERE sale_id=?", s.id)) {
      run("UPDATE shop_items SET stock = stock + ? WHERE id=?", l.qty, l.item_id);
      run("INSERT INTO shop_moves (item_id,type,qty,cost,ref,created_by,created_at) VALUES (?,?,?,?,?,?,?)", l.item_id, "undo", l.qty, l.cost, `shop:${s.id}`, req.user!.name, now());
    }
    if (s.payment_method === "khata" && s.customer_id) {
      run("DELETE FROM khata_ledger WHERE customer_id=? AND ref=?", s.customer_id, `SHOP-${s.id}`);
      run("UPDATE customers SET balance = balance - ? WHERE id=?", s.total, s.customer_id);
    }
    run("DELETE FROM shop_sale_lines WHERE sale_id=?", s.id);
    run("DELETE FROM shop_sales WHERE id=?", s.id);
  });
  audit(tid(req), req.user!, "shop_undo", `shop:${s.id}`, { total: s.total, payment: s.payment_method });
  return { ok: true };
}));

/** Shop sales, profit and stock for a period (default today). */
export function shopSummary(t: number, from = pkDayStart(), to = new Date().toISOString(), stationId?: number | null) {
  const st = stationId ? `AND ss.station_id=${Number(stationId)}` : "";
  const tot = get(`SELECT COALESCE(SUM(total),0) sales, COALESCE(SUM(cost_total),0) cost, COUNT(*) n FROM shop_sales ss WHERE tenant_id=? AND created_at >= ? AND created_at < ? ${st}`, t, from, to)!;
  return {
    sales: round2(tot.sales), cost: round2(tot.cost), profit: round2(tot.sales - tot.cost), count: tot.n,
    by_category: all(`SELECT i.category, ROUND(SUM(l.qty*l.price),2) sales, ROUND(SUM(l.qty*(l.price-l.cost)),2) profit FROM shop_sale_lines l
      JOIN shop_sales ss ON ss.id=l.sale_id JOIN shop_items i ON i.id=l.item_id WHERE ss.tenant_id=? AND ss.created_at >= ? AND ss.created_at < ? ${st} GROUP BY i.category ORDER BY sales DESC`, t, from, to),
    top: all(`SELECT i.name, ROUND(SUM(l.qty),2) qty, ROUND(SUM(l.qty*l.price),2) sales FROM shop_sale_lines l JOIN shop_sales ss ON ss.id=l.sale_id
      JOIN shop_items i ON i.id=l.item_id WHERE ss.tenant_id=? AND ss.created_at >= ? AND ss.created_at < ? ${st} GROUP BY i.id ORDER BY sales DESC LIMIT 8`, t, from, to),
    stock_value: round2(get("SELECT COALESCE(SUM(stock*cost),0) v FROM shop_items WHERE tenant_id=? AND active=1", t)!.v),
    low_stock: all("SELECT id, name, stock, unit, reorder_level FROM shop_items WHERE tenant_id=? AND active=1 AND reorder_level > 0 AND stock <= reorder_level ORDER BY stock", t),
  };
}
shop.get("/shop/summary", requirePerm("stock.manage"), h((req) => {
  const q = parse(z.object({ from: z.string().optional(), to: z.string().optional() }), req.query);
  return shopSummary(tid(req), q.from ? pkStart(q.from) : pkDayStart(), q.to ? pkEnd(q.to) : new Date().toISOString());
}));
