import { Router } from "express";
import { z } from "zod";
import { all, get, run, getSetting, setSetting } from "../db.js";
import { h, parse, tid, requireRole } from "../auth.js";
import { kpis, forecast, tankOutlook, insights, dailySeries, scoreCustomers } from "../ai/analytics.js";
import { askBusiness } from "../ai/agent.js";
import { runJob, ensureAutomations } from "../automation/scheduler.js";
import { buildDailyBrief } from "../automation/jobs.js";
import { bus } from "../whatsapp/cloud.js";
import { aiEnabled, waLive, config } from "../config.js";
import { normalizePhone } from "../services.js";

export const insightsRouter = Router();

insightsRouter.get("/dashboard", h((req) => {
  const t = tid(req);
  const series = dailySeries(t, 30);
  return {
    kpis: kpis(t),
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

insightsRouter.post("/ai/ask", h(async (req) => {
  const b = parse(z.object({ question: z.string().min(3).max(1000) }), req.body);
  return askBusiness(tid(req), b.question);
}));

insightsRouter.get("/ai/brief", h(async (req) => ({ text: await buildDailyBrief(tid(req)) })));

insightsRouter.post("/ai/rescore", h((req) => scoreCustomers(tid(req))));

/* ---------------- Alerts ---------------- */
insightsRouter.get("/alerts", h((req) => all("SELECT a.*, s.name station_name FROM alerts a LEFT JOIN stations s ON s.id=a.station_id WHERE a.tenant_id=? ORDER BY a.id DESC LIMIT 300", tid(req))));
insightsRouter.post("/alerts/:id/ack", h((req) => {
  run("UPDATE alerts SET acknowledged=1 WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  return { ok: true };
}));
insightsRouter.post("/alerts/ack-all", h((req) => {
  run("UPDATE alerts SET acknowledged=1 WHERE tenant_id=?", tid(req));
  return { ok: true };
}));

/* ---------------- Automations ---------------- */
insightsRouter.get("/automations", h((req) => {
  ensureAutomations(tid(req));
  return all("SELECT * FROM automations WHERE tenant_id=? ORDER BY id", tid(req));
}));
insightsRouter.patch("/automations/:key", requireRole("owner", "manager"), h((req) => {
  const b = parse(z.object({ enabled: z.boolean() }), req.body);
  run("UPDATE automations SET enabled=? WHERE tenant_id=? AND key=?", b.enabled ? 1 : 0, tid(req), req.params.key);
  return get("SELECT * FROM automations WHERE tenant_id=? AND key=?", tid(req), req.params.key);
}));
insightsRouter.post("/automations/:key/run", requireRole("owner", "manager"), h(async (req) => ({ result: await runJob(tid(req), String(req.params.key)) })));

/* ---------------- Settings ---------------- */
insightsRouter.get("/settings", h((req) => {
  const t = tid(req);
  const tenant = get("SELECT * FROM tenants WHERE id=?", t)!;
  return {
    business_name: tenant.name, owner_name: tenant.owner_name, owner_phone: getSetting(t, "owner_phone", tenant.owner_phone ?? ""),
    integrations: {
      claude: { connected: aiEnabled(), model: config.aiModel, effort: config.aiEffort },
      whatsapp: { connected: waLive(), phone_number_id: config.wa.phoneNumberId ? "…" + config.wa.phoneNumberId.slice(-4) : null, webhook_url: `${config.publicUrl}/webhooks/whatsapp`, verify_token_set: Boolean(config.wa.verifyToken), template: config.wa.templateName },
      payments: { link_base: config.paymentLinkBase },
    },
    users: all("SELECT id, name, email, role FROM users WHERE tenant_id=?", t),
  };
}));
insightsRouter.put("/settings", requireRole("owner"), h((req) => {
  const b = parse(z.object({ business_name: z.string().min(2).optional(), owner_name: z.string().optional(), owner_phone: z.string().optional() }), req.body);
  const t = tid(req);
  if (b.business_name) run("UPDATE tenants SET name=? WHERE id=?", b.business_name, t);
  if (b.owner_name !== undefined) run("UPDATE tenants SET owner_name=? WHERE id=?", b.owner_name, t);
  if (b.owner_phone !== undefined) setSetting(t, "owner_phone", b.owner_phone ? normalizePhone(b.owner_phone) : "");
  return { ok: true };
}));

/* ---------------- Live events (SSE) ---------------- */
insightsRouter.get("/events", (req, res) => {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
  res.write("retry: 3000\n\n");
  const t = tid(req);
  const onEvent = (e: { tenant_id: number }) => { if (e.tenant_id === t) res.write(`data: ${JSON.stringify(e)}\n\n`); };
  bus.on("event", onEvent);
  const ping = setInterval(() => res.write(": ping\n\n"), 25_000);
  req.on("close", () => { bus.off("event", onEvent); clearInterval(ping); });
});
