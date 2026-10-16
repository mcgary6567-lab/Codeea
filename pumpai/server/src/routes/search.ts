/**
 * Global search (Ctrl/⌘ K): one box finds a khata customer, vehicle, wholesale client, thekedar, supplier, staff member,
 * tanker / driver, machine, voucher number or cheque number — and jumps straight to it. Each group is gated by the same
 * permission its page needs, so a salesman only sees what he may open anyway.
 */
import { Router } from "express";
import { all, get } from "../db.js";
import { h, tid, requireAuth, can } from "../auth.js";
import { round2 } from "../services.js";
import { clientDue } from "./wholesale.js";
import { thekedarDue } from "./carriage.js";
import { supplierOwed } from "./suppliers.js";

export const search = Router();

type Hit = { kind: string; label: string; sub?: string; amount?: number | null; to: string };

search.get("/search", requireAuth, h((req) => {
  const t = tid(req);
  const raw = String(req.query.q ?? "").trim();
  if (raw.length < 2) return { q: raw, hits: [] as Hit[] };
  const q = `%${raw}%`;
  const u = req.user!;
  const hits: Hit[] = [];
  const push = (arr: Hit[]) => hits.push(...arr);

  // voucher number "RV-00060" / "PV-12" → the day book of that day
  const vm = /^(RV|PV)[-\s]?0*(\d+)$/i.exec(raw);
  if (vm && can(u, "cashier.desk")) {
    const v = get("SELECT id, direction, party_name, amount, created_at, voided FROM cashier_vouchers WHERE tenant_id=? AND id=? AND direction=?", t, Number(vm[2]), vm[1].toUpperCase() === "RV" ? "in" : "out");
    if (v) push([{ kind: "voucher", label: `${vm[1].toUpperCase()}-${String(v.id).padStart(5, "0")} · ${v.party_name}${v.voided ? " (void)" : ""}`, sub: String(v.created_at).slice(0, 10), amount: v.amount, to: `/cashier?tab=daybook&date=${String(v.created_at).slice(0, 10)}` }]);
  }
  if (can(u, "customers.view")) {
    push(all(`SELECT id, name, phone, balance FROM customers WHERE tenant_id=? AND (name LIKE ? OR COALESCE(phone,'') LIKE ?) ORDER BY (balance<>0) DESC, name LIMIT 8`, t, q, q)
      .map((c) => ({ kind: "customer", label: c.name, sub: c.phone ?? "khata customer", amount: round2(c.balance), to: `/customers/${c.id}` })));
    push(all(`SELECT v.plate_no, c.id cid, c.name FROM vehicles v JOIN customers c ON c.id=v.customer_id WHERE c.tenant_id=? AND v.plate_no LIKE ? LIMIT 5`, t, q)
      .map((v) => ({ kind: "vehicle", label: v.plate_no, sub: `vehicle · ${v.name}`, to: `/customers/${v.cid}` })));
  }
  if (can(u, "wholesale.view")) {
    push(all(`SELECT id, name, business_name, phone FROM wholesale_clients WHERE tenant_id=? AND active=1 AND (name LIKE ? OR COALESCE(business_name,'') LIKE ? OR COALESCE(phone,'') LIKE ?) ORDER BY name LIMIT 6`, t, q, q, q)
      .map((c) => ({ kind: "client", label: c.name, sub: c.business_name || c.phone || "wholesale client", amount: round2(clientDue(c.id)), to: `/wholesale/${c.id}` })));
    push(all(`SELECT id, number FROM tankers WHERE tenant_id=? AND number LIKE ? LIMIT 4`, t, q).map((x) => ({ kind: "tanker", label: x.number, sub: "tanker", to: "/wholesale?tab=fleet" })));
    push(all(`SELECT id, name, COALESCE(phone,'') phone FROM drivers WHERE tenant_id=? AND (name LIKE ? OR phone LIKE ?) LIMIT 4`, t, q, q).map((x) => ({ kind: "driver", label: x.name, sub: `driver${x.phone ? ` · ${x.phone}` : ""}`, to: "/wholesale?tab=fleet" })));
  }
  if (can(u, "carriage.view")) push(all(`SELECT id, name, phone FROM thekedars WHERE tenant_id=? AND (name LIKE ? OR COALESCE(phone,'') LIKE ?) ORDER BY name LIMIT 6`, t, q, q)
    .map((k) => ({ kind: "thekedar", label: k.name, sub: k.phone || "thekedar", amount: round2(thekedarDue(k.id)), to: `/carriage/${k.id}` })));
  if (can(u, "suppliers.manage")) push(all(`SELECT id, name, company, phone FROM suppliers WHERE tenant_id=? AND (name LIKE ? OR COALESCE(company,'') LIKE ? OR COALESCE(phone,'') LIKE ?) ORDER BY name LIMIT 6`, t, q, q, q)
    .map((s) => ({ kind: "supplier", label: s.name, sub: s.company || s.phone || "supplier", amount: round2(supplierOwed(s.id)), to: `/suppliers?q=${encodeURIComponent(s.name)}` })));
  if (can(u, "staff.manage")) push(all(`SELECT id, name, role, phone FROM users WHERE tenant_id=? AND active=1 AND (name LIKE ? OR COALESCE(phone,'') LIKE ?) ORDER BY name LIMIT 6`, t, q, q)
    .map((s) => ({ kind: "staff", label: s.name, sub: `${s.role}${s.phone ? ` · ${s.phone}` : ""}`, to: `/staff?q=${encodeURIComponent(s.name)}` })));
  if (can(u, "cheques.manage")) push(all(`SELECT id, direction, party_name, amount, bank, cheque_no, status FROM cheques WHERE tenant_id=? AND (cheque_no LIKE ? OR party_name LIKE ?) ORDER BY id DESC LIMIT 5`, t, q, q)
    .map((c) => ({ kind: "cheque", label: `Cheque ${c.cheque_no} · ${c.bank}`, sub: `${c.direction === "in" ? "received from" : "issued to"} ${c.party_name} · ${c.status}`, amount: c.amount, to: "/cashier?tab=cheques" })));
  if (can(u, "sales.create")) push(all(`SELECT id, name, type, serial_no, status FROM machines WHERE tenant_id=? AND (name LIKE ? OR COALESCE(serial_no,'') LIKE ? OR COALESCE(make,'') LIKE ?) LIMIT 4`, t, q, q, q)
    .map((m) => ({ kind: "machine", label: m.name, sub: `${m.type} · ${m.status}${m.serial_no ? ` · ${m.serial_no}` : ""}`, to: `/machines?q=${encodeURIComponent(m.name)}` })));
  return { q: raw, hits: hits.slice(0, 30) };
}));
