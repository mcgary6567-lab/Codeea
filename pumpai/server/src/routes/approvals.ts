/**
 * Approvals on WhatsApp: when a manager changes the price (two-person rule) or enters a big expense,
 * the owner gets a message and replies "1" to approve or "2" to reject — no need to open the app.
 */
import { Router } from "express";
import { all, get, run, now, getSetting, type Row } from "../db.js";
import { h, tid, requirePerm } from "../auth.js";
import { normalizePhone } from "../services.js";
import { sendDirect } from "../whatsapp/cloud.js";

export const approvals = Router();

type Decider = (t: number, refId: number, approve: boolean, by: string) => Promise<string>;
const deciders: Record<string, Decider> = {};
/** Each module registers how its requests are approved (avoids circular imports). */
export const registerDecider = (kind: string, fn: Decider) => { deciders[kind] = fn; };

/** Owner phone (settings) and admins with a phone — the people who can approve on WhatsApp. */
function approvers(t: number) {
  const out: { phone: string; name: string }[] = [];
  const owner = getSetting(t, "owner_phone") || get("SELECT owner_phone FROM tenants WHERE id=?", t)?.owner_phone;
  if (owner) out.push({ phone: normalizePhone(owner), name: get("SELECT owner_name FROM tenants WHERE id=?", t)?.owner_name ?? "Owner" });
  for (const u of all("SELECT name, phone FROM users WHERE tenant_id=? AND role='admin' AND active=1 AND phone IS NOT NULL", t))
    if (!out.some((o) => o.phone === normalizePhone(u.phone))) out.push({ phone: normalizePhone(u.phone), name: u.name });
  return out;
}

export async function requestApproval(t: number, kind: string, refId: number, summary: string) {
  if (getSetting(t, "wa_approvals", "1") === "0") return null;
  run("INSERT OR IGNORE INTO approvals (tenant_id,kind,ref_id,summary,created_at) VALUES (?,?,?,?,?)", t, kind, refId, summary, now());
  const a = get("SELECT * FROM approvals WHERE kind=? AND ref_id=?", kind, refId)!;
  const others = get("SELECT COUNT(*) n FROM approvals WHERE tenant_id=? AND status='pending' AND id<>?", t, a.id)!.n;
  const text = `🔔 *Approval #${a.id}*\n${summary}\n\nReply *1* to approve, *2* to reject${others ? ` (or "1 ${a.id}" — ${others} more waiting)` : ""}.`;
  for (const p of approvers(t)) await sendDirect(t, p, "approval_request", `approval:${a.id}`, text);
  return a;
}

/** Called when a request is decided in the app, so a later WhatsApp "1" does not act on it again. */
export function closeApproval(kind: string, refId: number, approved: boolean, by: string, via = "app") {
  run("UPDATE approvals SET status=?, decided_by=?, decided_via=?, decided_at=? WHERE kind=? AND ref_id=? AND status='pending'",
    approved ? "approved" : "rejected", by, via, now(), kind, refId);
}

const YES = /^(1|ok|okay|yes|haan|han|ji|approve|approved|theek|thik)$/i;
const NO = /^(2|no|nahi|nahin|reject|rejected|cancel)$/i;

/**
 * A WhatsApp message from the owner/admin that answers an approval. Returns the reply to send,
 * or null when the message is not an approval answer (it then goes to the business assistant).
 */
export async function handleApprovalReply(t: number, from: string, text: string): Promise<string | null> {
  const m = text.trim().match(/^([a-z]+|\d)\s*#?\s*(\d+)?\s*[.!]*$/i);
  if (!m || !(YES.test(m[1]) || NO.test(m[1]))) return null;
  const who = approvers(t).find((p) => p.phone === normalizePhone(from));
  const pending = all("SELECT * FROM approvals WHERE tenant_id=? AND status='pending' ORDER BY id DESC", t);
  if (!pending.length) return m[1].length === 1 && /\d/.test(m[1]) ? "Koi approval baqi nahi hai. ✅" : null;
  if (!who) return "Approvals sirf owner / admin ke number se ho sakti hain.";
  const a: Row | undefined = m[2] ? pending.find((p) => p.id === Number(m[2])) : pending[0];
  if (!a) return `Approval #${m[2]} nahi mili ya pehle hi decide ho chuki hai.`;
  const approve = YES.test(m[1]);
  const decide = deciders[a.kind];
  if (!decide) return null;
  closeApproval(a.kind, a.ref_id, approve, who.name, "whatsapp");
  let result: string;
  try { result = await decide(t, a.ref_id, approve, `${who.name} (WhatsApp)`); }
  catch (e) { return `#${a.id}: ${(e as Error).message}`; }
  const left = pending.length - 1;
  return `${approve ? "✅ Approved" : "❌ Rejected"} #${a.id}: ${a.summary.split("\n")[0]}\n${result}${left ? `\n\n${left} aur approval baqi: ${pending.filter((p) => p.id !== a.id).slice(0, 3).map((p) => `#${p.id}`).join(", ")}` : ""}`;
}

approvals.get("/approvals", requirePerm("alerts.view"), h((req) => ({
  pending: all("SELECT * FROM approvals WHERE tenant_id=? AND status='pending' ORDER BY id DESC", tid(req)),
  recent: all("SELECT * FROM approvals WHERE tenant_id=? AND status<>'pending' ORDER BY decided_at DESC LIMIT 20", tid(req)),
  enabled: getSetting(tid(req), "wa_approvals", "1") === "1",
})));
