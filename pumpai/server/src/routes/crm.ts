import crypto from "node:crypto";
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now, pkStart, pkEnd, pkDate } from "../db.js";
import { linkPhotos, proofPhotos, proofCol, requireProof, isCheque } from "./capture.js";
import { bankAccountFor, accountIdField, chequeToRegister } from "./banks.js";
import { h, parse, tid, requirePerm, requireAny, can } from "../auth.js";
import { AppError, khataEntry, normalizePhone, paymentLink, pkr, recordSale } from "../services.js";
import { sendWhatsApp } from "../whatsapp/cloud.js";
import { billLink, sendKhataBill, prevMonth } from "../billing.js";
import { writeCampaign } from "../ai/agent.js";
import { announce } from "../notifications.js";

const TYPE_LABEL: Record<string, string> = { retail: "Customer", fleet: "Fleet", farmer: "Farmer", business: "Business", police: "Police", school: "School", government: "Govt. office", hospital: "Hospital" };
const TYPE_ICON: Record<string, string> = { police: "🚓", school: "🏫", government: "🏛️", hospital: "🚑", fleet: "🚚", farmer: "🚜", business: "🏢", retail: "🚗" };


export const crm = Router();

function ownCustomer(tenantId: number, id: number) {
  const c = get("SELECT * FROM customers WHERE id=? AND tenant_id=?", id, tenantId);
  if (!c) throw new AppError(404, "Customer not found");
  return c;
}

/* ---------------- Customers ---------------- */
crm.get("/customers", requirePerm("customers.view"), h((req) => {
  const q = `%${String(req.query.q ?? "").trim()}%`;
  const seg = String(req.query.segment ?? "");
  return all(
    `SELECT c.*, (SELECT COUNT(*) FROM vehicles v WHERE v.customer_id=c.id) vehicles,
       (SELECT ROUND(SUM(amount)) FROM sales s WHERE s.customer_id=c.id AND s.created_at >= date('now','-30 day')) spend_30d
     FROM customers c WHERE c.tenant_id=? AND (c.name LIKE ? OR c.phone LIKE ?) ${seg ? "AND c.segment=?" : ""}
     ORDER BY c.last_visit_at DESC NULLS LAST, c.id DESC LIMIT 500`,
    ...(seg ? [tid(req), q, q, seg] : [tid(req), q, q]),
  );
}));

const customerBody = z.object({
  name: z.string().min(2), phone: z.string().min(10), type: z.enum(["retail", "fleet", "farmer", "business", "police", "school", "government", "hospital"]).default("retail"),
  city: z.string().optional().nullable(), credit_limit: z.number().min(0).default(0), opt_in: z.boolean().default(true), notes: z.string().optional().nullable(),
});

const vehicleBody = z.object({ plate_no: z.string().min(3).max(40), fuel: z.enum(["PMG", "HOBC", "HSD"]).optional().nullable(), daily_limit_l: z.number().positive().optional().nullable() });

/** Add a customer or a khata (credit) account, optionally with its vehicles, and tell the team. */
crm.post("/customers", requirePerm("customers.create"), h(async (req) => {
  const b = parse(customerBody.extend({ vehicles: z.array(vehicleBody).max(50).optional() }), req.body);
  if (b.credit_limit > 0 && !can(req.user, "credit.set_limit")) throw new AppError(403, "Only the admin can give khata credit");
  const phone = normalizePhone(b.phone);
  if (get("SELECT id FROM customers WHERE tenant_id=? AND phone=?", tid(req), phone)) throw new AppError(400, "A customer with this phone already exists");
  const id = tx(() => {
    const { id } = run("INSERT INTO customers (tenant_id,name,phone,type,city,credit_limit,opt_in,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
      tid(req), b.name, phone, b.type, b.city ?? null, b.credit_limit, b.opt_in ? 1 : 0, b.notes ?? null, now());
    for (const v of b.vehicles ?? []) run("INSERT INTO vehicles (customer_id,plate_no,fuel,daily_limit_l) VALUES (?,?,?,?)", id, v.plate_no.toUpperCase().trim(), v.fuel ?? null, v.daily_limit_l ?? null);
    return id;
  });
  const plates = (b.vehicles ?? []).map((v) => v.plate_no.toUpperCase().trim());
  if (b.credit_limit > 0)
    await announce(tid(req), req.user!.id, ["salesman", "manager", "admin"], { type: "new_khata", data: { customer_id: id },
      title: `📒 New khata account: ${TYPE_ICON[b.type] ?? ""} ${b.name}`,
      body: `${TYPE_LABEL[b.type] ?? b.type}${b.city ? ` · ${b.city}` : ""}. Ab POS par "Khata" mein mil jayega.${plates.length ? `\nGaariyan: ${plates.join(", ")}` : ""}` });
  else
    await announce(tid(req), req.user!.id, ["manager", "admin"], { type: "new_customer", data: { customer_id: id },
      title: `New customer: ${b.name}`, body: `${TYPE_LABEL[b.type] ?? b.type} · added by ${req.user!.name}` });
  return { ...get("SELECT * FROM customers WHERE id=?", id)!, vehicles: all("SELECT * FROM vehicles WHERE customer_id=?", id) };
}));

crm.patch("/customers/:id", requirePerm("customers.edit"), h(async (req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  const b = parse(customerBody.partial(), req.body);
  if (b.credit_limit !== undefined && b.credit_limit !== c.credit_limit && !can(req.user, "credit.set_limit")) throw new AppError(403, "Only the admin can change credit limits");
  const m = { ...c, ...b, phone: b.phone ? normalizePhone(b.phone) : c.phone, opt_in: b.opt_in === undefined ? c.opt_in : b.opt_in ? 1 : 0 };
  run("UPDATE customers SET name=?, phone=?, type=?, city=?, credit_limit=?, opt_in=?, notes=? WHERE id=?", m.name, m.phone, m.type, m.city ?? null, m.credit_limit, m.opt_in, m.notes ?? null, c.id);
  // khata opened or closed: the salesmen need to know straight away
  if (c.credit_limit <= 0 && m.credit_limit > 0)
    await announce(tid(req), req.user!.id, ["salesman", "manager", "admin"], { type: "new_khata", data: { customer_id: c.id },
      title: `📒 Khata opened: ${TYPE_ICON[m.type] ?? ""} ${m.name}`, body: `Ab POS par "Khata" mein mil jayega.` });
  if (c.credit_limit > 0 && m.credit_limit <= 0)
    await announce(tid(req), req.user!.id, ["salesman", "manager", "admin"], { type: "khata_closed", data: { customer_id: c.id },
      title: `⛔ Khata closed: ${m.name}`, body: `Is account par ab udhaar tel na dein.` });
  return get("SELECT * FROM customers WHERE id=?", c.id);
}));

crm.get("/customers/:id", requirePerm("customers.view"), h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  return {
    ...c,
    vehicles: all("SELECT * FROM vehicles WHERE customer_id=?", c.id),
    ledger: all(`SELECT k.*, ${proofCol("'khata:'||k.id")} FROM khata_ledger k WHERE k.customer_id=? ORDER BY k.id DESC LIMIT 100`, c.id),
    sales: all("SELECT s.*, st.name station_name FROM sales s JOIN stations st ON st.id=s.station_id WHERE customer_id=? ORDER BY s.id DESC LIMIT 50", c.id),
    orders: all("SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 20", c.id),
    complaints: all("SELECT * FROM complaints WHERE customer_id=? ORDER BY id DESC LIMIT 20", c.id),
    conversation: get("SELECT * FROM conversations WHERE customer_id=?", c.id) ?? null,
  };
}));

crm.post("/customers/:id/vehicles", requirePerm("customers.create"), h(async (req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  const b = parse(vehicleBody, req.body);
  const plate = b.plate_no.toUpperCase().trim();
  if (get("SELECT id FROM vehicles WHERE customer_id=? AND plate_no=?", c.id, plate)) throw new AppError(400, "This vehicle is already on the account");
  const v = get("SELECT * FROM vehicles WHERE id=?", run("INSERT INTO vehicles (customer_id,plate_no,fuel,daily_limit_l) VALUES (?,?,?,?)", c.id, plate, b.fuel ?? null, b.daily_limit_l ?? null).id);
  if (c.credit_limit > 0)
    await announce(tid(req), req.user!.id, ["salesman", "manager"], { type: "new_vehicle", data: { customer_id: c.id },
      title: `🚗 New vehicle on ${c.name}'s khata`, body: `${plate} ab is khate par tel le sakti hai.` });
  return v;
}));

/** Change a vehicle's fuel or daily litre limit. */
crm.patch("/customers/:id/vehicles/:vid", requirePerm("customers.edit"), h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  const b = parse(vehicleBody.omit({ plate_no: true }), req.body);
  run("UPDATE vehicles SET fuel=?, daily_limit_l=? WHERE id=? AND customer_id=?", b.fuel ?? null, b.daily_limit_l ?? null, Number(req.params.vid), c.id);
  return get("SELECT * FROM vehicles WHERE id=? AND customer_id=?", Number(req.params.vid), c.id);
}));

crm.delete("/customers/:id/vehicles/:vid", requirePerm("customers.edit"), h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  run("DELETE FROM vehicles WHERE id=? AND customer_id=?", Number(req.params.vid), c.id);
  return { ok: true };
}));

/* ---------------- Khata ---------------- */
crm.post("/customers/:id/khata", requireAny("khata.manage", "cash.receive"), h(async (req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  const b = parse(z.object({ type: z.enum(["debit", "credit"]), amount: z.number().positive(), note: z.string().optional(), method: z.string().optional(), notify: z.boolean().default(true), photo_ids: proofPhotos, account_id: accountIdField,
    cheque_bank: z.string().max(80).optional().nullable(), cheque_no: z.string().max(30).optional().nullable(), cheque_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional().nullable() }), req.body);
  if (b.type === "credit" && !b.method) b.method = "Cash"; // a payment with no method is cash — every book reads it the same way
  if (b.type === "debit" && !can(req.user, "khata.manage")) throw new AppError(403, "The cashier can only receive payments");
  if (b.type === "credit" && isCheque(b.method)) requireProof(tid(req), b.photo_ids, "cheque");
  // a cheque not yet in the bank waits in the cheque register; the khata goes down when it clears
  if (b.type === "credit" && isCheque(b.method) && !b.account_id) {
    const id = chequeToRegister(tid(req), { direction: "in", party_type: "khata", party_id: c.id, party_name: c.name, amount: b.amount, bank: b.cheque_bank, cheque_no: b.cheque_no, cheque_date: b.cheque_date, note: b.note, by: req.user!.name });
    linkPhotos(tid(req), b.photo_ids, `chq:${id}`);
    if (b.notify) await sendWhatsApp(tid(req), c, `✅ Shukriya ${c.name}! ${pkr(b.amount)} ka cheque mil gaya. Clear hone par khate mein jama ho jayega.`, "system", { kind: "payment_receipt" });
    return { ...get("SELECT * FROM customers WHERE id=?", c.id)!, cheque_pending: true, cheque_id: id };
  }
  const updated = khataEntry(c.id, b.type, b.amount, b.method ?? null, b.note ?? null, b.type === "credit" ? bankAccountFor(tid(req), b.account_id, b.method) : null);
  if (b.photo_ids?.length) linkPhotos(tid(req), b.photo_ids, `khata:${get("SELECT MAX(id) id FROM khata_ledger WHERE customer_id=?", c.id)!.id}`);
  if (b.notify && b.type === "credit")
    await sendWhatsApp(tid(req), updated, `✅ Shukriya ${updated.name}! ${pkr(b.amount)} ki payment mil gayi${b.method ? ` (${b.method})` : ""}. Naya khata balance: ${pkr(updated.balance)}.`, "system", { kind: "payment_receipt" });
  return updated;
}));

/** Khata statement / monthly bill: every fuel entry with litres, the rate at that time, vehicle and slip, plus payments. */
export function khataStatement(tenantId: number, id: number, from?: string, to?: string) {
  const c = ownCustomer(tenantId, id);
  const before = from ? get("SELECT COALESCE(SUM(CASE WHEN type='debit' THEN amount ELSE -amount END),0) b FROM khata_ledger WHERE customer_id=? AND created_at < ?", c.id, pkStart(from))!.b : 0;
  let bal = before;
  const lines = all(
    `SELECT k.*, ${proofCol("'khata:'||k.id")}, s.name station_name FROM khata_ledger k LEFT JOIN stations s ON s.id=k.station_id
     WHERE k.customer_id=? ${from ? "AND k.created_at >= ?" : ""} ${to ? "AND k.created_at < ?" : ""} ORDER BY k.created_at, k.id`,
    ...[c.id, ...(from ? [pkStart(from)] : []), ...(to ? [pkEnd(to)] : [])],
  ).map((l) => { bal += l.type === "debit" ? l.amount : -l.amount; return { ...l, balance: Math.round(bal * 100) / 100 }; });
  const fuel = lines.filter((l) => l.type === "debit");
  return {
    customer: { id: c.id, name: c.name, type: c.type, phone: c.phone, city: c.city, credit_limit: c.credit_limit, balance: c.balance, khata_blocked: c.khata_blocked },
    from: from ?? null, to: to ?? null, opening_balance: before, closing_balance: bal, lines,
    totals: {
      by_product: Object.values(fuel.reduce((a: Record<string, any>, l) => {
        const k = l.product ?? "Other";
        a[k] ??= { product: k, litres: 0, amount: 0, entries: 0 };
        a[k].litres += l.litres ?? 0; a[k].amount += l.amount; a[k].entries++;
        return a;
      }, {})),
      charged: fuel.reduce((a, l) => a + l.amount, 0),
      paid: lines.filter((l) => l.type === "credit").reduce((a, l) => a + l.amount, 0),
    },
  };
}

/* QR cards: one for the account and one for each vehicle, scanned at the POS */
const CARD_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const newCode = () => Array.from(crypto.randomBytes(8), (x) => CARD_CHARS[x % CARD_CHARS.length]).join("");
crm.get("/customers/:id/cards", requirePerm("khata.manage"), h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  if (!(c.credit_limit > 0)) throw new AppError(400, "QR cards are for khata accounts");
  if (!c.card_code) run("UPDATE customers SET card_code=? WHERE id=?", newCode(), c.id);
  for (const v of all("SELECT id FROM vehicles WHERE customer_id=? AND card_code IS NULL", c.id)) run("UPDATE vehicles SET card_code=? WHERE id=?", newCode(), v.id);
  return {
    business: get("SELECT name FROM tenants WHERE id=?", tid(req))!.name,
    customer: { id: c.id, name: c.name, type: c.type, city: c.city },
    account_code: get("SELECT card_code FROM customers WHERE id=?", c.id)!.card_code,
    vehicles: all("SELECT id, plate_no, card_code code FROM vehicles WHERE customer_id=? ORDER BY plate_no", c.id),
  };
}));
/** Lost card: give the account (or one vehicle) a new code; the old card stops working. */
crm.post("/customers/:id/cards/reissue", requirePerm("khata.manage"), h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  const b = parse(z.object({ vehicle_id: z.number().optional() }), req.body);
  if (b.vehicle_id) run("UPDATE vehicles SET card_code=? WHERE id=? AND customer_id=?", newCode(), b.vehicle_id, c.id);
  else run("UPDATE customers SET card_code=? WHERE id=?", newCode(), c.id);
  return { ok: true };
}));

/* Monthly bill: printable link + WhatsApp */
const monthQ = z.object({ month: z.string().regex(/^\d{4}-\d{2}$/).optional() });
crm.get("/customers/:id/bill-link", requirePerm("khata.manage"), h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  const m = parse(monthQ, req.query).month ?? pkDate().slice(0, 7);
  return { month: m, url: billLink(tid(req), "k", c.id, m) };
}));
crm.post("/customers/:id/send-bill", requirePerm("khata.manage"), h(async (req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  return sendKhataBill(tid(req), c.id, parse(monthQ, req.body).month ?? prevMonth());
}));

const dateQ = z.object({ from: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(), to: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional() });

crm.get("/customers/:id/statement", requirePerm("khata.manage"), h((req) => {
  const q = parse(dateQ, req.query);
  return khataStatement(tid(req), Number(req.params.id), q.from, q.to);
}));

crm.get("/customers/:id/statement.csv", requirePerm("khata.manage"), (req, res, next) => {
  try {
    const q = parse(dateQ, req.query);
    const s = khataStatement(tid(req), Number(req.params.id), q.from, q.to);
    const esc = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
    const out = [
      [`Khata statement — ${s.customer.name}`, s.from ?? "start", s.to ?? "today"].map(esc).join(","),
      ["Opening balance", s.opening_balance].map(esc).join(","),
      ["Date", "Time", "Vehicle", "Slip no.", "Product", "Litres", "Rate (Rs/L)", "Charged", "Paid", "Balance", "Station", "Note"].map(esc).join(","),
      ...s.lines.map((l) => [l.created_at.slice(0, 10), new Date(l.created_at).toLocaleTimeString("en-PK", { timeZone: "Asia/Karachi", hour: "2-digit", minute: "2-digit" }),
        l.vehicle_no, l.slip_no, l.product, l.litres, l.rate, l.type === "debit" ? l.amount : "", l.type === "credit" ? l.amount : "", l.balance, l.station_name, l.type === "credit" ? (l.ref ?? l.note) : ""].map(esc).join(",")),
      ["Closing balance (due)", s.closing_balance].map(esc).join(","),
    ].join("\n");
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="khata-${s.customer.name.replace(/[^\w-]+/g, "_")}.csv"`);
    res.send("\uFEFF" + out);
  } catch (e) { next(e); }
});

crm.post("/customers/:id/remind", requirePerm("khata.manage"), h(async (req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  if (c.balance <= 0) throw new AppError(400, "No balance due");
  const link = paymentLink(c, c.balance);
  await sendWhatsApp(tid(req), c, `Assalam-o-Alaikum ${c.name}! 📒 Aap ka khata balance ${pkr(c.balance)} hai.${link ? `\nPay karein: ${link}` : "\nMeharbani kar ke jald ada kar dein. Shukriya!"}`, "agent", { kind: "khata_reminder" });
  return { ok: true, link };
}));

crm.get("/khata", requirePerm("khata.manage"), h((req) => all(
  `SELECT c.id, c.name, c.phone, c.type, c.balance, c.credit_limit, c.risk_score, c.khata_blocked,
     (SELECT MAX(created_at) FROM khata_ledger k WHERE k.customer_id=c.id AND k.type='credit') last_payment
   FROM customers c WHERE c.tenant_id=? AND (c.balance > 0 OR c.credit_limit > 0) ORDER BY c.balance DESC`, tid(req))));

/* ---------------- Orders (from WhatsApp AI or manual) ---------------- */
crm.get("/orders", requirePerm("orders.manage"), h((req) => all(
  `SELECT o.*, c.name customer_name, c.phone FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.tenant_id=? ORDER BY o.id DESC LIMIT 200`, tid(req))));

const statusText: Record<string, string> = {
  confirmed: "✅ confirm ho gaya hai", dispatched: "🚚 rawana ho gaya hai", delivered: "📦 deliver ho gaya hai. Shukriya!", cancelled: "❌ cancel kar diya gaya hai",
};

crm.patch("/orders/:id", requirePerm("orders.manage"), h(async (req) => {
  const b = parse(z.object({ status: z.enum(["pending", "confirmed", "dispatched", "delivered", "cancelled"]), station_id: z.number().optional(), note: z.string().optional() }), req.body);
  const o = get("SELECT * FROM orders WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!o) throw new AppError(404, "Order not found");
  if (o.status === "delivered" || o.status === "cancelled") throw new AppError(400, `Order already ${o.status}`);
  if (b.status === "delivered") {
    const stationId = b.station_id ?? get("SELECT id FROM stations WHERE tenant_id=? ORDER BY id LIMIT 1", tid(req))!.id;
    recordSale(tid(req), { station_id: stationId, product: o.product, litres: o.litres, customer_id: o.customer_id,
      payment_method: o.payment === "khata" ? "khata" : o.payment === "digital" ? "raast" : "cash" });
  }
  run("UPDATE orders SET status=? WHERE id=?", b.status, o.id);
  const c = get("SELECT * FROM customers WHERE id=?", o.customer_id)!;
  if (statusText[b.status])
    await sendWhatsApp(tid(req), c, `Order #${o.id} (${o.litres}L ${o.product}) ${statusText[b.status]}${b.note ? `\n${b.note}` : ""}`, "system", { kind: "order_status" });
  return get("SELECT * FROM orders WHERE id=?", o.id);
}));

/* ---------------- Complaints ---------------- */
crm.get("/complaints", requirePerm("complaints.manage"), h((req) => all(
  `SELECT k.*, c.name customer_name, c.phone FROM complaints k LEFT JOIN customers c ON c.id=k.customer_id WHERE k.tenant_id=? ORDER BY k.id DESC LIMIT 200`, tid(req))));

crm.patch("/complaints/:id", requirePerm("complaints.manage"), h(async (req) => {
  const b = parse(z.object({ status: z.enum(["open", "in_progress", "resolved"]), reply: z.string().optional() }), req.body);
  const k = get("SELECT * FROM complaints WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!k) throw new AppError(404, "Complaint not found");
  run("UPDATE complaints SET status=? WHERE id=?", b.status, k.id);
  if (k.customer_id && (b.reply || b.status === "resolved")) {
    const c = get("SELECT * FROM customers WHERE id=?", k.customer_id)!;
    await sendWhatsApp(tid(req), c, b.reply ?? `Aap ki shikayat C-${k.id} hal kar di gayi hai. Shukriya aap ke sabr ka! 🙏`, "agent", { kind: "complaint_update" });
  }
  return get("SELECT * FROM complaints WHERE id=?", k.id);
}));

/* ---------------- Campaigns ---------------- */
const SEGMENTS: Record<string, string> = {
  all: "1=1", VIP: "segment='VIP'", "At risk": "churn_score >= 0.6", Fleet: "type='fleet'", Agri: "type='farmer'",
  Regular: "segment='Regular'", New: "segment='New'", Debtors: "balance > 0",
};

crm.get("/campaigns", requirePerm("campaigns.manage"), h((req) => ({
  campaigns: all("SELECT * FROM campaigns WHERE tenant_id=? ORDER BY id DESC", tid(req)),
  segments: Object.keys(SEGMENTS).map((s) => ({ key: s, count: get(`SELECT COUNT(*) n FROM customers WHERE tenant_id=? AND opt_in=1 AND ${SEGMENTS[s]}`, tid(req))!.n })),
})));

crm.post("/campaigns/ai-write", requirePerm("campaigns.manage"), h((req) => {
  const b = parse(z.object({ goal: z.string().min(3), segment: z.string() }), req.body);
  return writeCampaign(tid(req), b.goal, b.segment);
}));

crm.post("/campaigns", requirePerm("campaigns.manage"), h(async (req) => {
  const b = parse(z.object({ name: z.string().min(2), segment: z.string().refine((s) => s in SEGMENTS, "Unknown segment"), message: z.string().min(5).max(1000), send_now: z.boolean().default(false) }), req.body);
  const t = tid(req);
  const { id } = run("INSERT INTO campaigns (tenant_id,name,segment,message,status,created_at) VALUES (?,?,?,?,?,?)", t, b.name, b.segment, b.message, "draft", now());
  if (b.send_now) await sendCampaign(t, id);
  return get("SELECT * FROM campaigns WHERE id=?", id);
}));

crm.post("/campaigns/:id/send", requirePerm("campaigns.manage"), h(async (req) => {
  await sendCampaign(tid(req), Number(req.params.id));
  return get("SELECT * FROM campaigns WHERE id=?", Number(req.params.id));
}));

async function sendCampaign(tenantId: number, id: number) {
  const camp = get("SELECT * FROM campaigns WHERE id=? AND tenant_id=?", id, tenantId);
  if (!camp) throw new AppError(404, "Campaign not found");
  if (camp.status === "sent") throw new AppError(400, "Campaign already sent");
  const audience = all(`SELECT * FROM customers WHERE tenant_id=? AND opt_in=1 AND ${SEGMENTS[camp.segment] ?? "0"}`, tenantId);
  run("UPDATE campaigns SET status='sending' WHERE id=?", id);
  let sent = 0;
  for (const c of audience) {
    const text = camp.message.replaceAll("{name}", String(c.name).split(" ")[0]).replaceAll("{balance}", pkr(c.balance)).replaceAll("{points}", String(c.loyalty_points));
    await sendWhatsApp(tenantId, c, text, "campaign", { kind: "campaign", campaign_id: id });
    sent++;
  }
  run("UPDATE campaigns SET status='sent', sent_count=?, sent_at=? WHERE id=?", sent, now(), id);
}
