import express from "express";
import cors from "cors";
import path from "node:path";
import fs from "node:fs";
import { z } from "zod";
import { config, aiEnabled, waLive } from "./config.js";
import { migrate, get } from "./db.js";
import { requireAuth, errorHandler, login, h, parse, permissionsOf } from "./auth.js";
import { operations } from "./routes/operations.js";
import { crm } from "./routes/crm.js";
import { waWebhook, inbox } from "./routes/whatsapp.js";
import { insightsRouter } from "./routes/insights.js";
import { users } from "./routes/users.js";
import { startScheduler } from "./automation/scheduler.js";
import { seed } from "./seed.js";

migrate();
if (!get("SELECT id FROM tenants LIMIT 1")) {
  console.log("[db] empty database — loading demo data");
  seed();
}

export const app = express();
app.use(cors());
app.use(express.json({ limit: "2mb", verify: (req, _res, buf) => { (req as any).rawBody = buf; } }));

app.get("/api/health", (_req, res) => res.json({ ok: true, ai: aiEnabled() ? "claude" : "rules", whatsapp: waLive() ? "live" : "simulated" }));
app.post("/api/auth/login", h((req) => {
  const b = parse(z.object({ email: z.string().email(), password: z.string().min(1) }), req.body);
  return login(b.email, b.password);
}));
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
