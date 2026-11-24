/**
 * Documents (PDF + WhatsApp): see docs.ts. Who may open a document = who may see its page.
 *  GET  /docs/:kind/:id/pdf?from&to        the PDF (download / print)
 *  GET  /docs/:kind/:id/contacts?from&to   suggested numbers + the public link + what was sent before
 *  POST /docs/:kind/:id/send {phones[], note?, from?, to?}
 */
import { Router, type Request } from "express";
import { z } from "zod";
import { get } from "../db.js";
import { h, parse, tid, can } from "../auth.js";
import { AppError, audit } from "../services.js";
import { DOC_KINDS, renderDoc, docLink, docContacts, sendDoc, type DocKind, type DocOpts } from "../docs.js";

export const docs = Router();
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const q = z.object({ from: day.optional(), to: day.optional() });

/** The page's own permission, and a salesman only his own shift / sales. */
function allow(req: Request, kind: DocKind, id: number) {
  const u = req.user!;
  const anyOf = (...p: Parameters<typeof can>[1][]) => p.some((x) => can(u, x));
  const okByKind: Record<DocKind, boolean> = {
    voucher: anyOf("cashier.desk", "cash.receive", "cash.pay"), khata: anyOf("khata.manage", "customers.edit"), wholesale: anyOf("wholesale.view"), carriage: anyOf("carriage.view"),
    supplier: anyOf("suppliers.manage"), shift: anyOf("shifts.manage"), receipt: anyOf("sales.view", "sales.create"), slip: anyOf("staff.manage"),
  };
  if (!okByKind[kind]) throw new AppError(403, "You don't have permission for this document");
  if (u.role === "salesman") {
    if (kind === "shift" && get("SELECT attendant FROM shifts WHERE id=?", id)?.attendant !== u.name) throw new AppError(403, "Only your own shift");
    if (kind === "receipt" && get("SELECT created_by FROM sales WHERE id=?", id)?.created_by !== u.id) throw new AppError(403, "Only your own sales");
  }
  if (kind === "slip" && u.role !== "admin" && !can(u, "staff.manage")) throw new AppError(403, "Only the manager / CEO");
}
const kindOf = (s: string): DocKind => { if (!(DOC_KINDS as readonly string[]).includes(s)) throw new AppError(400, "Unknown document"); return s as DocKind; };

docs.get("/docs/:kind/:id/pdf", (req, res, next) => {
  try {
    const kind = kindOf(req.params.kind), id = Number(req.params.id);
    allow(req, kind, id);
    const o = parse(q, req.query) as DocOpts;
    const d = renderDoc(tid(req), kind, id, o);
    if (!d.pdf) return res.redirect(d.link!);
    res.setHeader("content-type", "application/pdf");
    res.setHeader("content-disposition", `${req.query.download ? "attachment" : "inline"}; filename="${d.filename}"`);
    res.end(d.pdf);
  } catch (e) { next(e); }
});

docs.get("/docs/:kind/:id/contacts", h((req) => {
  const kind = kindOf(req.params.kind), id = Number(req.params.id);
  allow(req, kind, id);
  const o = parse(q, req.query) as DocOpts;
  const d = renderDoc(tid(req), kind, id, o);
  return { ...docContacts(tid(req), d, kind, id), link: docLink(tid(req), kind, id, o), filename: d.filename };
}));

docs.post("/docs/:kind/:id/send", h(async (req) => {
  const kind = kindOf(req.params.kind), id = Number(req.params.id);
  allow(req, kind, id);
  const b = parse(q.extend({ phones: z.array(z.string().trim().min(1).max(20)).min(1).max(10), note: z.string().trim().max(300).optional() }), req.body);
  const o: DocOpts = { from: b.from, to: b.to };
  const d = renderDoc(tid(req), kind, id, o);
  const link = docLink(tid(req), kind, id, o);
  const r = await sendDoc(tid(req), kind, id, d, link, b.phones, b.note, req.user!);
  audit(tid(req), req.user!, "document_sent", `${kind}:${id}`, { title: d.title, to: r.results.map((x) => x.phone), mode: r.mode, ok: r.results.filter((x) => x.ok).length });
  return { title: d.title, ...r };
}));
