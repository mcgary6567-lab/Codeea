import crypto from "node:crypto";
import { EventEmitter } from "node:events";
import { config, waLive } from "../config.js";
import { get, run, now, type Row } from "../db.js";

/** In-process event bus; the dashboard listens over SSE for live inbox updates. */
export const bus = new EventEmitter();
bus.setMaxListeners(100);

export function ensureConversation(tenantId: number, customerId: number): Row {
  let c = get("SELECT * FROM conversations WHERE tenant_id=? AND customer_id=?", tenantId, customerId);
  if (!c) {
    const { id } = run("INSERT INTO conversations (tenant_id,customer_id,mode,status,last_message_at) VALUES (?,?,?,?,?)",
      tenantId, customerId, "ai", "open", now());
    c = get("SELECT * FROM conversations WHERE id=?", id)!;
  }
  return c;
}

export function storeMessage(conv: Row, direction: "in" | "out", sender: string, body: string, meta?: unknown, waId?: string) {
  const ts = now();
  const { id } = run(
    "INSERT INTO messages (conversation_id,direction,sender,body,meta,wa_id,created_at) VALUES (?,?,?,?,?,?,?)",
    conv.id, direction, sender, body, meta ? JSON.stringify(meta) : null, waId ?? null, ts,
  );
  if (direction === "in") {
    run("UPDATE conversations SET last_message_at=?, last_inbound_at=?, unread=unread+1, status='open' WHERE id=?", ts, ts, conv.id);
  } else {
    run("UPDATE conversations SET last_message_at=? WHERE id=?", ts, conv.id);
  }
  const msg = get("SELECT * FROM messages WHERE id=?", id)!;
  bus.emit("event", { type: "message", tenant_id: conv.tenant_id, conversation_id: conv.id, message: msg });
  return msg;
}

/** True when the customer wrote to us in the last 24h, so free-form messages are allowed by Meta. */
export function inServiceWindow(conv: Row): boolean {
  return Boolean(conv.last_inbound_at) && Date.now() - Date.parse(conv.last_inbound_at) < 24 * 3600_000;
}

async function graphSend(payload: unknown): Promise<{ ok: boolean; id?: string; error?: string }> {
  const url = `https://graph.facebook.com/${config.wa.graphVersion}/${config.wa.phoneNumberId}/messages`;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${config.wa.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data: any = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: data?.error?.message ?? `HTTP ${res.status}` };
    return { ok: true, id: data?.messages?.[0]?.id };
  } catch (e: any) {
    return { ok: false, error: e.message };
  }
}

/**
 * Send a WhatsApp message to a customer and log it in their conversation.
 * Inside the 24h window a free-form text is sent; outside it, the approved utility template is used
 * (template body has one {{1}} variable that carries the text). Without credentials it is simulated.
 */
export async function sendWhatsApp(
  tenantId: number,
  customer: Row,
  text: string,
  sender: "ai" | "agent" | "system" | "campaign" = "system",
  meta: Record<string, unknown> = {},
) {
  const conv = ensureConversation(tenantId, customer.id);
  if (!customer.opt_in && sender === "campaign") return { skipped: "opted_out" };
  let delivery: Record<string, unknown> = { simulated: true };
  if (waLive()) {
    const useTemplate = !inServiceWindow(conv);
    const payload = useTemplate
      ? {
          messaging_product: "whatsapp", to: customer.phone, type: "template",
          template: {
            name: config.wa.templateName, language: { code: config.wa.templateLang },
            components: [{ type: "body", parameters: [{ type: "text", text: text.slice(0, 1000) }] }],
          },
        }
      : { messaging_product: "whatsapp", to: customer.phone, type: "text", text: { body: text, preview_url: true } };
    const r = await graphSend(payload);
    delivery = { simulated: false, template: useTemplate, ok: r.ok, error: r.error };
    return { message: storeMessage(conv, "out", sender, text, { ...meta, delivery }, r.id) };
  }
  return { message: storeMessage(conv, "out", sender, text, { ...meta, delivery }) };
}

/** Send to an arbitrary number (e.g. the owner) that may not be a customer. */
/** Message a contact who is not a CRM customer (wholesale client, staff, owner); kept in the outbox. */
export async function sendDirect(tenantId: number, to: { phone: string | null; name?: string | null }, kind: string, ref: string | null, text: string) {
  if (!to.phone) return null;
  const r = waLive() ? await graphSend({ messaging_product: "whatsapp", to: to.phone, type: "text", text: { body: text } }) : null;
  run("INSERT INTO outbox (tenant_id,to_phone,to_name,kind,ref,text,simulated,ok,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
    tenantId, to.phone, to.name ?? null, kind, ref, text, r ? 0 : 1, r ? (r.ok ? 1 : 0) : null, new Date().toISOString());
  return r ?? { simulated: true };
}

export async function sendToPhone(phone: string, text: string) {
  if (!waLive()) return { simulated: true };
  return graphSend({ messaging_product: "whatsapp", to: phone, type: "text", text: { body: text } });
}

export function verifySignature(rawBody: Buffer, header: string | undefined): boolean {
  if (!config.wa.appSecret) return true; // signature check disabled when no secret configured
  if (!header?.startsWith("sha256=")) return false;
  const expected = crypto.createHmac("sha256", config.wa.appSecret).update(rawBody).digest("hex");
  const given = header.slice(7);
  return given.length === expected.length && crypto.timingSafeEqual(Buffer.from(given), Buffer.from(expected));
}

export interface InboundMessage {
  from: string;
  name?: string;
  id: string;
  type: string;
  text: string;
}

/** Extract user messages from a Meta webhook payload. */
export function parseWebhook(body: any): InboundMessage[] {
  const out: InboundMessage[] = [];
  for (const entry of body?.entry ?? []) {
    for (const change of entry?.changes ?? []) {
      const v = change?.value;
      const names = new Map<string, string>((v?.contacts ?? []).map((c: any) => [c.wa_id, c.profile?.name]));
      for (const m of v?.messages ?? []) {
        let text = "";
        if (m.type === "text") text = m.text?.body ?? "";
        else if (m.type === "button") text = m.button?.text ?? "";
        else if (m.type === "interactive") text = m.interactive?.button_reply?.title ?? m.interactive?.list_reply?.title ?? "";
        else if (m.type === "location") text = `[location] ${m.location?.latitude},${m.location?.longitude}`;
        else text = `[${m.type} message]`;
        out.push({ from: m.from, name: names.get(m.from), id: m.id, type: m.type, text });
      }
    }
  }
  return out;
}
