import { Router, type Request } from "express";
import { z } from "zod";
import { all, get, run } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { config } from "../config.js";
import { AppError } from "../services.js";
import { parseWebhook, verifySignature, sendWhatsApp, bus } from "../whatsapp/cloud.js";
import { handleInbound, generateCustomerReply } from "../ai/agent.js";

/** Public Meta webhook (no auth). Messages are routed to the tenant set in WA_TENANT_ID (default 1). */
export const waWebhook = Router();
const webhookTenant = () => Number(process.env.WA_TENANT_ID ?? 1);

waWebhook.get("/", (req, res) => {
  if (req.query["hub.mode"] === "subscribe" && req.query["hub.verify_token"] === config.wa.verifyToken) {
    res.status(200).send(String(req.query["hub.challenge"] ?? ""));
  } else res.sendStatus(403);
});

waWebhook.post("/", (req: Request & { rawBody?: Buffer }, res) => {
  if (!verifySignature(req.rawBody ?? Buffer.from(""), req.header("x-hub-signature-256"))) return res.sendStatus(401);
  res.sendStatus(200); // acknowledge immediately; Meta retries slow webhooks
  for (const m of parseWebhook(req.body)) {
    handleInbound(webhookTenant(), { from: m.from, name: m.name, text: m.text, waId: m.id, image: m.image }).catch((e) => console.error("[wa] inbound failed", e));
  }
});

/** Authenticated inbox API for the dashboard. */
export const inbox = Router();
inbox.use(requirePerm("whatsapp.inbox"));

inbox.get("/conversations", h((req) => all(
  `SELECT cv.*, c.name, c.phone, c.type, c.segment, c.balance,
     (SELECT body FROM messages m WHERE m.conversation_id=cv.id ORDER BY id DESC LIMIT 1) last_body,
     (SELECT sender FROM messages m WHERE m.conversation_id=cv.id ORDER BY id DESC LIMIT 1) last_sender
   FROM conversations cv JOIN customers c ON c.id=cv.customer_id WHERE cv.tenant_id=? ORDER BY cv.last_message_at DESC LIMIT 300`, tid(req))));

function ownConv(req: Request) {
  const c = get("SELECT * FROM conversations WHERE id=? AND tenant_id=?", Number(req.params.id), tid(req));
  if (!c) throw new AppError(404, "Conversation not found");
  return c;
}

inbox.get("/conversations/:id/messages", h((req) => {
  const c = ownConv(req);
  run("UPDATE conversations SET unread=0 WHERE id=?", c.id);
  return {
    conversation: { ...c, customer: get("SELECT * FROM customers WHERE id=?", c.customer_id) },
    messages: all("SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 200", c.id).reverse()
      .map((m) => ({ ...m, meta: m.meta ? JSON.parse(m.meta) : null })),
  };
}));

inbox.post("/conversations/:id/reply", h(async (req) => {
  const c = ownConv(req);
  const b = parse(z.object({ text: z.string().min(1).max(4000) }), req.body);
  const customer = get("SELECT * FROM customers WHERE id=?", c.customer_id)!;
  return sendWhatsApp(tid(req), customer, b.text, "agent", { by: req.user!.name });
}));

/** AI drafts a reply for a human agent to review (copilot mode). */
inbox.post("/conversations/:id/suggest", h(async (req) => {
  const c = ownConv(req);
  const last = get("SELECT body FROM messages WHERE conversation_id=? AND direction='in' ORDER BY id DESC LIMIT 1", c.id);
  if (!last) throw new AppError(400, "No customer message to reply to");
  const customer = get("SELECT * FROM customers WHERE id=?", c.customer_id)!;
  // run with a copy marked as AI-mode so tools like handoff don't loop; side-effect tools still apply
  return generateCustomerReply(tid(req), customer, { ...c, mode: "ai" }, last.body);
}));

inbox.patch("/conversations/:id", h((req) => {
  const c = ownConv(req);
  const b = parse(z.object({ mode: z.enum(["ai", "human"]).optional(), status: z.enum(["open", "closed"]).optional() }), req.body);
  run("UPDATE conversations SET mode=COALESCE(?,mode), status=COALESCE(?,status), handoff_reason=CASE WHEN ?='ai' THEN NULL ELSE handoff_reason END WHERE id=?",
    b.mode ?? null, b.status ?? null, b.mode ?? null, c.id);
  bus.emit("event", { type: "conversation", tenant_id: tid(req), conversation_id: c.id });
  return get("SELECT * FROM conversations WHERE id=?", c.id);
}));

/** WhatsApp simulator: behaves exactly like an inbound Meta webhook message. */
inbox.post("/simulate", h(async (req) => {
  const b = parse(z.object({ phone: z.string().min(10), name: z.string().optional(), text: z.string().max(2000).default(""),
    // a payment screenshot can be pushed as a data-URL / base64 to test the payment-inbox flow end to end
    image: z.string().optional(), image_mime: z.string().optional(), image_caption: z.string().max(300).optional() }), req.body);
  if (!b.text && !b.image) throw new AppError(400, "text ya image chahiye");
  const base64 = b.image ? b.image.replace(/^data:image\/[a-z]+;base64,/, "") : undefined;
  return handleInbound(tid(req), { from: b.phone, name: b.name, text: b.text,
    image: base64 ? { base64, mime: b.image_mime ?? "image/jpeg", caption: b.image_caption } : undefined });
}));

/** Start a new outbound chat with a customer. */
inbox.post("/send", h(async (req) => {
  const b = parse(z.object({ customer_id: z.number(), text: z.string().min(1).max(4000) }), req.body);
  const customer = get("SELECT * FROM customers WHERE id=? AND tenant_id=?", b.customer_id, tid(req));
  if (!customer) throw new AppError(404, "Customer not found");
  return sendWhatsApp(tid(req), customer, b.text, "agent", { by: req.user!.name });
}));
