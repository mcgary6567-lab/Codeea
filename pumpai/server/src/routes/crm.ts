import { Router } from "express";
import { z } from "zod";
import { all, get, run, now } from "../db.js";
import { h, parse, tid, requireRole } from "../auth.js";
import { AppError, khataEntry, normalizePhone, paymentLink, pkr, recordSale } from "../services.js";
import { sendWhatsApp } from "../whatsapp/cloud.js";
import { writeCampaign } from "../ai/agent.js";

export const crm = Router();

function ownCustomer(tenantId: number, id: number) {
  const c = get("SELECT * FROM customers WHERE id=? AND tenant_id=?", id, tenantId);
  if (!c) throw new AppError(404, "Customer not found");
  return c;
}

/* ---------------- Customers ---------------- */
crm.get("/customers", h((req) => {
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
  name: z.string().min(2), phone: z.string().min(10), type: z.enum(["retail", "fleet", "farmer", "business"]).default("retail"),
  city: z.string().optional().nullable(), credit_limit: z.number().min(0).default(0), opt_in: z.boolean().default(true), notes: z.string().optional().nullable(),
});

crm.post("/customers", h((req) => {
  const b = parse(customerBody, req.body);
  const phone = normalizePhone(b.phone);
  if (get("SELECT id FROM customers WHERE tenant_id=? AND phone=?", tid(req), phone)) throw new AppError(400, "A customer with this phone already exists");
  const { id } = run("INSERT INTO customers (tenant_id,name,phone,type,city,credit_limit,opt_in,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
    tid(req), b.name, phone, b.type, b.city ?? null, b.credit_limit, b.opt_in ? 1 : 0, b.notes ?? null, now());
  return get("SELECT * FROM customers WHERE id=?", id);
}));

crm.patch("/customers/:id", h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  const b = parse(customerBody.partial(), req.body);
  if (b.credit_limit !== undefined && req.user!.role !== "owner" && b.credit_limit > c.credit_limit) throw new AppError(403, "Only the owner can raise credit limits");
  const m = { ...c, ...b, phone: b.phone ? normalizePhone(b.phone) : c.phone, opt_in: b.opt_in === undefined ? c.opt_in : b.opt_in ? 1 : 0 };
  run("UPDATE customers SET name=?, phone=?, type=?, city=?, credit_limit=?, opt_in=?, notes=? WHERE id=?", m.name, m.phone, m.type, m.city ?? null, m.credit_limit, m.opt_in, m.notes ?? null, c.id);
  return get("SELECT * FROM customers WHERE id=?", c.id);
}));

crm.get("/customers/:id", h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  return {
    ...c,
    vehicles: all("SELECT * FROM vehicles WHERE customer_id=?", c.id),
    ledger: all("SELECT * FROM khata_ledger WHERE customer_id=? ORDER BY id DESC LIMIT 100", c.id),
    sales: all("SELECT s.*, st.name station_name FROM sales s JOIN stations st ON st.id=s.station_id WHERE customer_id=? ORDER BY s.id DESC LIMIT 50", c.id),
    orders: all("SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 20", c.id),
    complaints: all("SELECT * FROM complaints WHERE customer_id=? ORDER BY id DESC LIMIT 20", c.id),
    conversation: get("SELECT * FROM conversations WHERE customer_id=?", c.id) ?? null,
  };
}));

crm.post("/customers/:id/vehicles", h((req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  const b = parse(z.object({ plate_no: z.string().min(3), fuel: z.enum(["PMG", "HOBC", "HSD"]).optional(), daily_limit_l: z.number().positive().optional() }), req.body);
  return get("SELECT * FROM vehicles WHERE id=?", run("INSERT INTO vehicles (customer_id,plate_no,fuel,daily_limit_l) VALUES (?,?,?,?)", c.id, b.plate_no.toUpperCase(), b.fuel ?? null, b.daily_limit_l ?? null).id);
}));

/* ---------------- Khata ---------------- */
crm.post("/customers/:id/khata", requireRole("owner", "manager", "accountant"), h(async (req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  const b = parse(z.object({ type: z.enum(["debit", "credit"]), amount: z.number().positive(), note: z.string().optional(), method: z.string().optional(), notify: z.boolean().default(true) }), req.body);
  const updated = khataEntry(c.id, b.type, b.amount, b.method ?? null, b.note ?? null);
  if (b.notify && b.type === "credit")
    await sendWhatsApp(tid(req), updated, `✅ Shukriya ${updated.name}! ${pkr(b.amount)} ki payment mil gayi${b.method ? ` (${b.method})` : ""}. Naya khata balance: ${pkr(updated.balance)}.`, "system", { kind: "payment_receipt" });
  return updated;
}));

crm.post("/customers/:id/remind", h(async (req) => {
  const c = ownCustomer(tid(req), Number(req.params.id));
  if (c.balance <= 0) throw new AppError(400, "No balance due");
  const link = paymentLink(c, c.balance);
  await sendWhatsApp(tid(req), c, `Assalam-o-Alaikum ${c.name}! 📒 Aap ka khata balance ${pkr(c.balance)} hai.\nPay karein: ${link}`, "agent", { kind: "khata_reminder" });
  return { ok: true, link };
}));

crm.get("/khata", h((req) => all(
  `SELECT c.id, c.name, c.phone, c.type, c.balance, c.credit_limit, c.risk_score,
     (SELECT MAX(created_at) FROM khata_ledger k WHERE k.customer_id=c.id AND k.type='credit') last_payment
   FROM customers c WHERE c.tenant_id=? AND (c.balance > 0 OR c.credit_limit > 0) ORDER BY c.balance DESC`, tid(req))));

/* ---------------- Orders (from WhatsApp AI or manual) ---------------- */
crm.get("/orders", h((req) => all(
  `SELECT o.*, c.name customer_name, c.phone FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.tenant_id=? ORDER BY o.id DESC LIMIT 200`, tid(req))));

const statusText: Record<string, string> = {
  confirmed: "✅ confirm ho gaya hai", dispatched: "🚚 rawana ho gaya hai", delivered: "📦 deliver ho gaya hai. Shukriya!", cancelled: "❌ cancel kar diya gaya hai",
};

crm.patch("/orders/:id", h(async (req) => {
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
crm.get("/complaints", h((req) => all(
  `SELECT k.*, c.name customer_name, c.phone FROM complaints k LEFT JOIN customers c ON c.id=k.customer_id WHERE k.tenant_id=? ORDER BY k.id DESC LIMIT 200`, tid(req))));

crm.patch("/complaints/:id", h(async (req) => {
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

crm.get("/campaigns", h((req) => ({
  campaigns: all("SELECT * FROM campaigns WHERE tenant_id=? ORDER BY id DESC", tid(req)),
  segments: Object.keys(SEGMENTS).map((s) => ({ key: s, count: get(`SELECT COUNT(*) n FROM customers WHERE tenant_id=? AND opt_in=1 AND ${SEGMENTS[s]}`, tid(req))!.n })),
})));

crm.post("/campaigns/ai-write", h((req) => {
  const b = parse(z.object({ goal: z.string().min(3), segment: z.string() }), req.body);
  return writeCampaign(tid(req), b.goal, b.segment);
}));

crm.post("/campaigns", requireRole("owner", "manager"), h(async (req) => {
  const b = parse(z.object({ name: z.string().min(2), segment: z.string().refine((s) => s in SEGMENTS, "Unknown segment"), message: z.string().min(5).max(1000), send_now: z.boolean().default(false) }), req.body);
  const t = tid(req);
  const { id } = run("INSERT INTO campaigns (tenant_id,name,segment,message,status,created_at) VALUES (?,?,?,?,?,?)", t, b.name, b.segment, b.message, "draft", now());
  if (b.send_now) await sendCampaign(t, id);
  return get("SELECT * FROM campaigns WHERE id=?", id);
}));

crm.post("/campaigns/:id/send", requireRole("owner", "manager"), h(async (req) => {
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
