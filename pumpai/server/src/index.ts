import express from "express";
import cors from "cors";
import path from "node:path";
import fs from "node:fs";
import { z } from "zod";
import { config, aiEnabled, waLive, APP_VERSION } from "./config.js";
import { migrate, get } from "./db.js";
import { requireAuth, errorHandler, login, pinLogin, pinUsers, h, parse, permissionsOf } from "./auth.js";
import { operations } from "./routes/operations.js";
import { crm } from "./routes/crm.js";
import { waWebhook, inbox } from "./routes/whatsapp.js";
import { insightsRouter } from "./routes/insights.js";
import { managerDesk } from "./routes/managerDesk.js";
import { banks } from "./routes/banks.js";
import { wholesaleDesk } from "./routes/wholesaleDesk.js";
import { wholesaleVoice } from "./routes/wholesaleVoice.js";
import { khataVoice } from "./routes/khataVoice.js";
import { cashier } from "./routes/cashier.js";
import { owner } from "./routes/owner.js";
import { users } from "./routes/users.js";
import { wholesale } from "./routes/wholesale.js";
import { pinPortalAdmin, pinPortalPublic, pinLink } from "./routes/pinPortal.js";
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
import { care, portalFromOldToken } from "./routes/customerCare.js";
import { system } from "./routes/system.js";
import { renderReceipt } from "./billing.js";
import { prepaid } from "./routes/prepaid.js";
import { approvals } from "./routes/approvals.js";
import { feedback } from "./routes/feedback.js";
import { board, boardData, renderBoard } from "./routes/board.js";
import { register } from "./routes/register.js";
import { recurring } from "./routes/recurring.js";
import { claims } from "./routes/claims.js";
import { tax } from "./routes/tax.js";
import { bankrec } from "./routes/bankrec.js";
import { ledger } from "./routes/ledger.js";
import { people, slipPdf } from "./routes/people.js";
import { setupPublic, business, applyStoredConfig } from "./routes/setup.js";
import { auditTrail, auditRouter } from "./routes/auditTrail.js";
import { machines } from "./routes/machines.js";
import { getSetting } from "./db.js";
import { startScheduler } from "./automation/scheduler.js";
import { seed } from "./seed.js";

migrate();
applyStoredConfig();
if (!get("SELECT id FROM tenants LIMIT 1")) {
  if (config.demoData) {
    console.log("[db] empty database — loading demo data");
    seed();
  } else console.log("[setup] new installation — open the app in a browser to run the setup wizard");
}

export const app = express();
app.use(cors());
app.use(express.json({ limit: "8mb", verify: (req, _res, buf) => { (req as any).rawBody = buf; } }));

app.get("/api/health", (_req, res) => res.json({ ok: true, version: APP_VERSION, ai: aiEnabled() ? "claude" : "rules", whatsapp: waLive() ? "live" : "simulated" }));
app.use("/api", setupPublic); // setup wizard + branding (no login)
// business logo (login page, receipts, bills, TV board, app icon)
app.get("/branding/logo", (_req, res) => {
  const t = get("SELECT id FROM tenants ORDER BY id LIMIT 1");
  const id = t ? Number(getSetting(t.id, "logo_photo_id")) : 0;
  const p = id ? get("SELECT mime, data FROM photos WHERE id=?", id) : null;
  if (!p) return res.status(404).end();
  res.setHeader("content-type", p.mime);
  res.setHeader("cache-control", "public, max-age=3600");
  res.end(Buffer.from(p.data as Uint8Array));
});
// install-as-app manifest with this pump's own name and logo
app.get("/manifest.webmanifest", (_req, res) => {
  const t = get("SELECT * FROM tenants ORDER BY id LIMIT 1");
  const logo = t && getSetting(t.id, "logo_photo_id");
  const color = (t && getSetting(t.id, "brand_color")) || "#064e3b";
  res.type("application/manifest+json").send(JSON.stringify({
    name: t?.name ? `${t.name} — PumpAI` : "PumpAI", short_name: t?.name?.slice(0, 12) ?? "PumpAI", start_url: "/", display: "standalone",
    background_color: "#ffffff", theme_color: color,
    icons: logo ? [{ src: "/branding/logo", sizes: "512x512", type: "image/jpeg", purpose: "any" }] : [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml" }],
  }));
});
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
// wholesale client's own khata (short link + PIN)
app.use(pinPortalPublic);
// khata customer's own page (private link)
app.get("/portal/:token", (req, res) => {
  // links sent before PINs: open the PIN page for the same customer
  const c = portalFromOldToken(req.params.token);
  c ? res.redirect(302, new URL(pinLink("k", c)).pathname) : res.status(404).type("html").send("<p style='font-family:sans-serif'>This link is no longer valid. Ask the pump for a new one.</p>");
});
// TV rate board at the pump (signed link, refreshes by itself)
app.get("/board/:token", (req, res) => {
  const html = renderBoard(req.params.token);
  res.status(html ? 200 : 404).type("html").send(html ?? "<p style='font-family:sans-serif'>This board link is not valid.</p>");
});
app.get("/board/:token/data", (req, res) => {
  const d = boardData(req.params.token);
  res.setHeader("cache-control", "no-store");
  d ? res.json(d) : res.status(404).json({ error: "Not found" });
});
// salary slip PDF (signed link sent to the staff member on WhatsApp)
app.get("/slip/:token", (req, res) => {
  const pdf = slipPdf(req.params.token);
  if (!pdf) return res.status(404).type("html").send("<p style='font-family:sans-serif'>This salary slip link is not valid.</p>");
  res.setHeader("content-type", "application/pdf");
  res.setHeader("content-disposition", "inline; filename=salary-slip.pdf");
  res.end(pdf);
});
app.use("/webhooks/whatsapp", waWebhook);

const api = express.Router();
api.use(requireAuth);
api.use(auditTrail); // every change is written to the audit log
api.get("/me", h((req) => ({
  user: { ...req.user, station_name: req.user!.station_id ? get("SELECT name FROM stations WHERE id=?", req.user!.station_id)?.name : null },
  tenant: get("SELECT id, name FROM tenants WHERE id=?", req.user!.tenant_id),
  permissions: permissionsOf(req.user!.role, req.user!.tenant_id),
})));
api.use(operations);
api.use(crm);
api.use("/whatsapp", inbox);
api.use(insightsRouter);
api.use(managerDesk);
api.use(banks);
api.use(wholesaleDesk);
api.use(wholesaleVoice);
api.use(khataVoice);
api.use(cashier);
api.use(owner);
api.use(users);
api.use(wholesale);
api.use(pinPortalAdmin);
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
api.use(care);
api.use(system);
api.use(prepaid);
api.use(approvals);
api.use(feedback);
api.use(board);
api.use(register);
api.use(recurring);
api.use(claims);
api.use(tax);
api.use(bankrec);
api.use(ledger);
api.use(people);
api.use(business);
api.use(auditRouter);
api.use(machines);
app.use("/api", api);

// Serve the built dashboard in production
const webDist = path.resolve(process.env.WEB_DIST ?? "../web/dist");
if (fs.existsSync(webDist)) {
  app.use(express.static(webDist));
  app.get(/^\/(?!api|webhooks).*/, (_req, res) => res.sendFile(path.join(webDist, "index.html")));
}
app.use(errorHandler);

// on Vercel the app runs as a serverless function (api/index.mjs): no listen, no background scheduler
if (process.env.NODE_ENV !== "test" && !process.env.VERCEL) {
  app.listen(config.port, () => {
    console.log(`PumpAI API on http://localhost:${config.port}  (AI: ${aiEnabled() ? config.aiModel : "rule engine"}, WhatsApp: ${waLive() ? "live" : "simulated"})`);
    startScheduler();
  });
}
