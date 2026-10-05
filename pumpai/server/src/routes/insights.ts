import { Router } from "express";
import { z } from "zod";
import { all, get, run, getSetting, setSetting } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { kpis, forecast, tankOutlook, insights, dailySeries, scoreCustomers } from "../ai/analytics.js";
import { askBusiness } from "../ai/agent.js";
import { runJob, ensureAutomations } from "../automation/scheduler.js";
import { buildDailyBrief } from "../automation/jobs.js";
import { bus } from "../whatsapp/cloud.js";
import { aiEnabled, waLive, config } from "../config.js";
import { normalizePhone } from "../services.js";
import { dayBook } from "./reports.js";

export const insightsRouter = Router();

insightsRouter.get("/dashboard", requirePerm("dashboard.view"), h((req) => {
  const t = tid(req);
  const series = dailySeries(t, 30);
  return {
    kpis: kpis(t),
    day: dayBook(t),
    series,
    forecast: forecast(t, 7),
    tanks: tankOutlook(t),
    insights: insights(t),
    alerts: all("SELECT * FROM alerts WHERE tenant_id=? AND acknowledged=0 ORDER BY id DESC LIMIT 8", t),
    payment_mix: all(
      `SELECT payment_method k, ROUND(SUM(amount)) v FROM sales s JOIN stations st ON st.id=s.station_id
       WHERE st.tenant_id=? AND s.created_at >= date('now','-7 day') GROUP BY payment_method ORDER BY v DESC`, t),
  };
}));

insightsRouter.post("/ai/ask", requirePerm("ai.ask"), h(async (req) => {
  const b = parse(z.object({ question: z.string().min(3).max(1000) }), req.body);
  return askBusiness(tid(req), b.question);
}));

insightsRouter.get("/ai/brief", requirePerm("ai.ask"), h(async (req) => ({ text: await buildDailyBrief(tid(req)) })));

insightsRouter.post("/ai/rescore", requirePerm("automations.manage"), h((req) => scoreCustomers(tid(req))));

/* ---------------- Alerts ---------------- */
insightsRouter.get("/alerts", requirePerm("alerts.view"), h((req) => all("SELECT a.*, s.name station_name FROM alerts a LEFT JOIN stations s ON s.id=a.station_id WHERE a.tenant_id=? ORDER BY a.id DESC LIMIT 300", tid(req))));
insightsRouter.post("/alerts/:id/ack", requirePerm("alerts.view"), h((req) => {
  run("UPDATE alerts SET acknowledged=1 WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  return { ok: true };
}));
insightsRouter.post("/alerts/ack-all", requirePerm("alerts.view"), h((req) => {
  run("UPDATE alerts SET acknowledged=1 WHERE tenant_id=?", tid(req));
  return { ok: true };
}));

/* ---------------- Automations ---------------- */
insightsRouter.get("/automations", requirePerm("automations.manage"), h((req) => {
  ensureAutomations(tid(req));
  return all("SELECT * FROM automations WHERE tenant_id=? ORDER BY id", tid(req));
}));
insightsRouter.patch("/automations/:key", requirePerm("automations.manage"), h((req) => {
  const b = parse(z.object({ enabled: z.boolean() }), req.body);
  run("UPDATE automations SET enabled=? WHERE tenant_id=? AND key=?", b.enabled ? 1 : 0, tid(req), req.params.key);
  return get("SELECT * FROM automations WHERE tenant_id=? AND key=?", tid(req), req.params.key);
}));
insightsRouter.post("/automations/:key/run", requirePerm("automations.manage"), h(async (req) => ({ result: await runJob(tid(req), String(req.params.key)) })));

/* ---------------- Settings ---------------- */
/** On/off switches for the automatic messages and bookkeeping (all on by default). */
const AUTO_SETTINGS = ["khata_receipts", "wholesale_messages", "shortage_to_staff", "khata_auto_block", "ask_rating", "wa_approvals"] as const;
insightsRouter.get("/settings", requirePerm("settings.manage"), h((req) => {
  const t = tid(req);
  const tenant = get("SELECT * FROM tenants WHERE id=?", t)!;
  return {
    business_name: tenant.name, owner_name: tenant.owner_name, owner_phone: getSetting(t, "owner_phone", tenant.owner_phone ?? ""),
    automation: Object.fromEntries(AUTO_SETTINGS.map((k) => [k, getSetting(t, k, "1") !== "0"])),
    google_review_url: getSetting(t, "google_review_url", ""),
    khata_rules: { block_days: Number(getSetting(t, "khata_block_days", "60")), block_institutions: getSetting(t, "khata_block_institutions", "0") === "1", late_fee_pct: Number(getSetting(t, "khata_late_fee_pct", "0")) },
    integrations: {
      claude: { connected: aiEnabled(), model: config.aiModel, effort: config.aiEffort },
      whatsapp: { connected: waLive(), phone_number_id: config.wa.phoneNumberId ? "…" + config.wa.phoneNumberId.slice(-4) : null, webhook_url: `${config.publicUrl}/webhooks/whatsapp`, verify_token_set: Boolean(config.wa.verifyToken), template: config.wa.templateName },
      payments: { link_base: config.paymentLinkBase },
    },
  };
}));
insightsRouter.put("/settings", requirePerm("settings.manage"), h((req) => {
  const b = parse(z.object({ business_name: z.string().min(2).optional(), owner_name: z.string().optional(), owner_phone: z.string().optional(),
    automation: z.record(z.enum(AUTO_SETTINGS), z.boolean()).optional(), google_review_url: z.string().url().or(z.literal("")).optional(),
    khata_rules: z.object({ block_days: z.number().int().min(15).max(365).optional(), block_institutions: z.boolean().optional(), late_fee_pct: z.number().min(0).max(5).optional() }).optional() }), req.body);
  if (b.google_review_url !== undefined) setSetting(tid(req), "google_review_url", b.google_review_url);
  if (b.khata_rules) {
    if (b.khata_rules.block_days !== undefined) setSetting(tid(req), "khata_block_days", String(b.khata_rules.block_days));
    if (b.khata_rules.block_institutions !== undefined) setSetting(tid(req), "khata_block_institutions", b.khata_rules.block_institutions ? "1" : "0");
    if (b.khata_rules.late_fee_pct !== undefined) setSetting(tid(req), "khata_late_fee_pct", String(b.khata_rules.late_fee_pct));
  }
  for (const [k, v] of Object.entries(b.automation ?? {})) setSetting(tid(req), k, v ? "1" : "0");
  const t = tid(req);
  if (b.business_name) run("UPDATE tenants SET name=? WHERE id=?", b.business_name, t);
  if (b.owner_name !== undefined) run("UPDATE tenants SET owner_name=? WHERE id=?", b.owner_name, t);
  if (b.owner_phone !== undefined) setSetting(t, "owner_phone", b.owner_phone ? normalizePhone(b.owner_phone) : "");
  return { ok: true };
}));

/* ---------------- Live events (SSE) ---------------- */
insightsRouter.get("/events", requirePerm("whatsapp.inbox"), (req, res) => {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
  res.write("retry: 3000\n\n");
  const t = tid(req);
  const onEvent = (e: { tenant_id: number }) => { if (e.tenant_id === t) res.write(`data: ${JSON.stringify(e)}\n\n`); };
  bus.on("event", onEvent);
  const ping = setInterval(() => res.write(": ping\n\n"), 25_000);
  req.on("close", () => { bus.off("event", onEvent); clearInterval(ping); });
});
