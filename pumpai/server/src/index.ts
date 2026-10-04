import express from "express";
import cors from "cors";
import path from "node:path";
import fs from "node:fs";
import { z } from "zod";
import { config, aiEnabled, waLive } from "./config.js";
import { migrate, get } from "./db.js";
import { requireAuth, errorHandler, login, pinLogin, pinUsers, h, parse, permissionsOf } from "./auth.js";
import { operations } from "./routes/operations.js";
import { crm } from "./routes/crm.js";
import { waWebhook, inbox } from "./routes/whatsapp.js";
import { insightsRouter } from "./routes/insights.js";
import { users } from "./routes/users.js";
import { wholesale } from "./routes/wholesale.js";
import { expenses } from "./routes/expenses.js";
import { suppliers } from "./routes/suppliers.js";
import { reports } from "./routes/reports.js";
import { notifications } from "./routes/notifications.js";
import { pos } from "./routes/pos.js";
import { capture } from "./routes/capture.js";
import { renderBill } from "./billing.js";
import { staffRouter } from "./routes/staff.js";
import { backoffice, renderDay } from "./routes/backoffice.js";
import { shop } from "./routes/shop.js";
import { compliance } from "./routes/compliance.js";
import { analysis } from "./routes/analysis.js";
import { renderReceipt } from "./billing.js";
import { startScheduler } from "./automation/scheduler.js";
import { seed } from "./seed.js";

migrate();
if (!get("SELECT id FROM tenants LIMIT 1")) {
  console.log("[db] empty database — loading demo data");
  seed();
}

export const app = express();
app.use(cors());
app.use(express.json({ limit: "8mb", verify: (req, _res, buf) => { (req as any).rawBody = buf; } }));

app.get("/api/health", (_req, res) => res.json({ ok: true, ai: aiEnabled() ? "claude" : "rules", whatsapp: waLive() ? "live" : "simulated" }));
app.post("/api/auth/login", h((req) => {
  const b = parse(z.object({ email: z.string().email(), password: z.string().min(1) }), req.body);
  return login(b.email, b.password);
}));
app.get("/api/auth/pin-users", h((req) => pinUsers(req.headers["x-device"] as string | undefined)));
app.post("/api/auth/pin", h((req) => {
  const b = parse(z.object({ user_id: z.number(), pin: z.string().regex(/^\d{4}$/, "PIN is 4 digits") }), req.body);
  return pinLogin(req.headers["x-device"] as string | undefined, b.user_id, b.pin);
}));
// signed monthly bill / statement link sent on WhatsApp (no login needed)
app.get("/bill/:token", (req, res) => {
  const html = renderBill(req.params.token);
  res.status(html ? 200 : 404).type("html").send(html ?? "<p style='font-family:sans-serif'>This bill link is not valid or has expired.</p>");
});
app.get("/day/:token", (req, res) => {
  const html = renderDay(req.params.token);
  res.status(html ? 200 : 404).type("html").send(html ?? "<p style='font-family:sans-serif'>This report link is not valid.</p>");
});
// digital receipt for walk-in customers (QR on the POS)
app.get("/r/:token", (req, res) => {
  const html = renderReceipt(req.params.token);
  res.status(html ? 200 : 404).type("html").send(html ?? "<p style='font-family:sans-serif'>Receipt not found.</p>");
});
app.use("/webhooks/whatsapp", waWebhook);

const api = express.Router();
api.use(requireAuth);
api.get("/me", h((req) => ({
  user: { ...req.user, station_name: req.user!.station_id ? get("SELECT name FROM stations WHERE id=?", req.user!.station_id)?.name : null },
  tenant: get("SELECT id, name FROM tenants WHERE id=?", req.user!.tenant_id),
  permissions: permissionsOf(req.user!.role),
})));
api.use(operations);
api.use(crm);
api.use("/whatsapp", inbox);
api.use(insightsRouter);
api.use(users);
api.use(wholesale);
api.use(expenses);
api.use(suppliers);
api.use(reports);
api.use(notifications);
api.use(pos);
api.use(capture);
api.use(staffRouter);
api.use(backoffice);
api.use(shop);
api.use(compliance);
api.use(analysis);
app.use("/api", api);

// Serve the built dashboard in production
const webDist = path.resolve(process.env.WEB_DIST ?? "../web/dist");
if (fs.existsSync(webDist)) {
  app.use(express.static(webDist));
  app.get(/^\/(?!api|webhooks).*/, (_req, res) => res.sendFile(path.join(webDist, "index.html")));
}
app.use(errorHandler);

if (process.env.NODE_ENV !== "test") {
  app.listen(config.port, () => {
    console.log(`PumpAI API on http://localhost:${config.port}  (AI: ${aiEnabled() ? config.aiModel : "rule engine"}, WhatsApp: ${waLive() ? "live" : "simulated"})`);
    startScheduler();
  });
}
