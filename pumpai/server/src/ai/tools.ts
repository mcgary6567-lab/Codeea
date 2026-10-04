/** Tools the WhatsApp AI agent can call. Every tool is scoped to the customer of the conversation. */
import type Anthropic from "@anthropic-ai/sdk";
import { all, get, run, now, type Row } from "../db.js";
import { PRODUCTS } from "../config.js";
import { currentPrices, paymentLink, createAlert, pkr, round2 } from "../services.js";
import { bus } from "../whatsapp/cloud.js";
import { createBooking, portalLink } from "../routes/customerCare.js";

export interface ToolCtx {
  tenantId: number;
  customer: Row;
  conversation: Row;
  actions: string[]; // human-readable log of side effects, shown in the inbox
}

type ToolDef = Anthropic.Tool & { run: (ctx: ToolCtx, input: any) => unknown };

const obj = (properties: Record<string, unknown>, required: string[] = []) =>
  ({ type: "object" as const, properties, required, additionalProperties: false });

export const customerTools: ToolDef[] = [
  {
    name: "get_fuel_prices",
    description: "Current per-litre prices of Petrol (PMG), Hi-Octane (HOBC) and Diesel (HSD) in PKR, with the date they took effect.",
    input_schema: obj({}),
    run: (ctx) => Object.entries(currentPrices(ctx.tenantId)).map(([p, v]) => ({ product: p, name: PRODUCTS[p], price_pkr: v.price, since: v.effective_from.slice(0, 10) })),
  },
  {
    name: "get_my_account",
    description: "The customer's own profile: name, khata (credit) balance and limit, available credit, loyalty points and registered vehicles.",
    input_schema: obj({}),
    run: (ctx) => {
      const c = get("SELECT * FROM customers WHERE id=?", ctx.customer.id)!;
      return {
        name: c.name, type: c.type, khata_balance_pkr: c.balance, credit_limit_pkr: c.credit_limit,
        available_credit_pkr: Math.max(0, c.credit_limit - c.balance), loyalty_points: c.loyalty_points,
        points_value_pkr: c.loyalty_points, // 1 point = Rs 1
        vehicles: all("SELECT plate_no, fuel FROM vehicles WHERE customer_id=?", c.id),
      };
    },
  },
  {
    name: "get_khata_statement",
    description: "Recent khata (credit ledger) entries for the customer: purchases on credit (debit) and payments (credit).",
    input_schema: obj({ limit: { type: "integer", description: "How many entries, default 8" } }),
    run: (ctx, i) => all("SELECT type, amount, ref, note, substr(created_at,1,10) date FROM khata_ledger WHERE customer_id=? ORDER BY id DESC LIMIT ?",
      ctx.customer.id, Math.min(30, i?.limit ?? 8)),
  },
  {
    name: "get_recent_purchases",
    description: "The customer's recent fuel purchases (date, product, litres, amount, payment method).",
    input_schema: obj({ limit: { type: "integer" } }),
    run: (ctx, i) => all(
      "SELECT substr(created_at,1,16) at, product, litres, amount, payment_method, vehicle_no FROM sales WHERE customer_id=? ORDER BY id DESC LIMIT ?",
      ctx.customer.id, Math.min(20, i?.limit ?? 5)),
  },
  {
    name: "create_payment_link",
    description: "Create a JazzCash/Easypaisa/Raast payment link for the customer to pay their khata. Defaults to the full outstanding balance.",
    input_schema: obj({ amount_pkr: { type: "number", description: "Amount to pay; omit for full balance" } }),
    run: (ctx, i) => {
      const c = get("SELECT * FROM customers WHERE id=?", ctx.customer.id)!;
      const amount = i?.amount_pkr ?? c.balance;
      if (!(amount > 0)) return { error: "Nothing outstanding to pay" };
      const link = paymentLink(c, amount);
      ctx.actions.push(`Payment link ${pkr(amount)}`);
      return { amount_pkr: Math.round(amount), link, methods: ["JazzCash", "Easypaisa", "Raast QR", "Debit card"] };
    },
  },
  {
    name: "book_fuel_order",
    description:
      "Book a bulk fuel delivery or advance order (e.g. diesel for a farm, fleet or generator). Only call AFTER the customer has explicitly confirmed product, litres, address and time. Khata orders must fit the available credit.",
    input_schema: obj({
      product: { type: "string", enum: ["PMG", "HOBC", "HSD"] },
      litres: { type: "number", description: "Litres, minimum 200 for delivery" },
      address: { type: "string" },
      deliver_at: { type: "string", description: "Requested delivery time, free text e.g. 'kal subah 9 baje' or ISO date" },
      payment: { type: "string", enum: ["khata", "cash", "digital"] },
    }, ["product", "litres", "address", "payment"]),
    run: (ctx, i) => {
      const prices = currentPrices(ctx.tenantId);
      const rate = prices[i.product]?.price;
      if (!rate) return { error: "Product not available" };
      if (i.litres < 200) return { error: "Minimum delivery order is 200 litres" };
      const amount = round2(rate * i.litres);
      const c = get("SELECT * FROM customers WHERE id=?", ctx.customer.id)!;
      if (i.payment === "khata" && c.balance + amount > c.credit_limit)
        return { error: `Insufficient credit. Available ${pkr(Math.max(0, c.credit_limit - c.balance))}, order ${pkr(amount)}` };
      const { id } = run(
        "INSERT INTO orders (tenant_id,customer_id,product,litres,rate,amount,address,deliver_at,payment,status,source,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ctx.tenantId, c.id, i.product, i.litres, rate, amount, i.address, i.deliver_at ?? null, i.payment, "pending", "whatsapp", now(),
      );
      createAlert(ctx.tenantId, { type: "order", severity: "info", title: `New WhatsApp order #${id}: ${i.litres}L ${i.product}`, body: `${c.name} · ${i.address} · ${i.deliver_at ?? "ASAP"} · ${pkr(amount)}` });
      bus.emit("event", { type: "order", tenant_id: ctx.tenantId, id });
      ctx.actions.push(`Order #${id} booked`);
      return { order_id: id, status: "pending", amount_pkr: amount, rate_pkr: rate };
    },
  },
  {
    name: "get_my_orders",
    description: "Status of the customer's recent fuel orders.",
    input_schema: obj({}),
    run: (ctx) => all("SELECT id, product, litres, amount, status, deliver_at, substr(created_at,1,10) date FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 5", ctx.customer.id),
  },
  {
    name: "register_complaint",
    description: "Log a complaint (short measure, rude staff, card issue, quality, etc). Use for any dissatisfaction.",
    input_schema: obj({
      category: { type: "string", enum: ["short_measure", "fuel_quality", "staff_behaviour", "payment", "billing", "facility", "other"] },
      summary: { type: "string", description: "One-line summary in English" },
      sentiment: { type: "string", enum: ["negative", "very_negative", "neutral"] },
      station_id: { type: "integer" },
    }, ["category", "summary", "sentiment"]),
    run: (ctx, i) => {
      const { id } = run("INSERT INTO complaints (tenant_id,customer_id,station_id,category,message,sentiment,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
        ctx.tenantId, ctx.customer.id, i.station_id ?? null, i.category, i.summary, i.sentiment, "open", now());
      createAlert(ctx.tenantId, {
        station_id: i.station_id, type: "complaint", severity: i.sentiment === "very_negative" || i.category === "short_measure" ? "critical" : "warning",
        title: `Complaint #${id} (${i.category.replace("_", " ")}) — ${ctx.customer.name}`, body: i.summary,
      });
      ctx.actions.push(`Complaint #${id} logged`);
      return { ticket: `C-${id}`, status: "open", sla: "Manager will call within 2 hours" };
    },
  },
  {
    name: "find_stations",
    description: "List our petrol stations with address, timings, services and Google Maps link.",
    input_schema: obj({ city: { type: "string" } }),
    run: (ctx, i) => all("SELECT id, name, city, address, timings, services, lat, lng FROM stations WHERE tenant_id=?", ctx.tenantId)
      .filter((s) => !i?.city || String(s.city).toLowerCase().includes(String(i.city).toLowerCase()))
      .map((s) => ({ ...s, map: s.lat ? `https://maps.google.com/?q=${s.lat},${s.lng}` : null })),
  },
  {
    name: "handoff_to_human",
    description: "Transfer the chat to a human manager. Use when the customer asks for a person, is angry, negotiates price/credit limits, or you cannot help.",
    input_schema: obj({ reason: { type: "string" } }, ["reason"]),
    run: (ctx, i) => {
      run("UPDATE conversations SET mode='human', handoff_reason=? WHERE id=?", i.reason, ctx.conversation.id);
      createAlert(ctx.tenantId, { type: "handoff", severity: "warning", title: `WhatsApp chat needs a human: ${ctx.customer.name}`, body: i.reason });
      ctx.actions.push("Handed off to human");
      return { ok: true, note: "A manager will reply shortly" };
    },
  },
  {
    name: "set_marketing_preference",
    description: "Opt the customer in or out of offers/broadcasts (e.g. when they say STOP / band karo).",
    input_schema: obj({ opt_in: { type: "boolean" } }, ["opt_in"]),
    run: (ctx, i) => {
      run("UPDATE customers SET opt_in=? WHERE id=?", i.opt_in ? 1 : 0, ctx.customer.id);
      ctx.actions.push(i.opt_in ? "Opted in" : "Opted out");
      return { ok: true };
    },
  },
  {
    name: "book_service",
    description: "Book a car wash, oil change, tyre/puncture or service/tuning slot at the pump. Ask for the day and time first; 'at' is an ISO time with +05:00 (Pakistan).",
    input_schema: obj({ service: { type: "string", enum: ["car_wash", "oil_change", "tyre", "service"] }, at: { type: "string", description: "e.g. 2026-10-06T17:00:00+05:00" }, vehicle_no: { type: "string" } }, ["service", "at"]),
    run: (ctx, i) => {
      const b = createBooking(ctx.tenantId, { customer_id: ctx.customer.id, service: i.service, at: i.at, vehicle_no: i.vehicle_no ?? null, by: "WhatsApp AI" });
      ctx.actions.push(`Booked ${i.service} #${b.id}`);
      return { booking_id: b.id, service: b.service, at_pakistan_time: new Date(b.at).toLocaleString("en-PK", { timeZone: "Asia/Karachi" }), station: b.station_name };
    },
  },
  {
    name: "get_my_bookings",
    description: "The customer's upcoming service bookings.",
    input_schema: obj({}),
    run: (ctx) => all("SELECT id, service, at, status FROM bookings WHERE customer_id=? AND status='booked' AND at >= ? ORDER BY at", ctx.customer.id, new Date().toISOString())
      .map((b) => ({ ...b, at_pakistan_time: new Date(b.at).toLocaleString("en-PK", { timeZone: "Asia/Karachi" }) })),
  },
  {
    name: "cancel_booking",
    description: "Cancel one of the customer's own upcoming bookings.",
    input_schema: obj({ booking_id: { type: "integer" } }, ["booking_id"]),
    run: (ctx, i) => {
      const r = run("UPDATE bookings SET status='cancelled' WHERE id=? AND customer_id=? AND status='booked'", i.booking_id, ctx.customer.id);
      if (r.changes) ctx.actions.push(`Booking #${i.booking_id} cancelled`);
      return r.changes ? { ok: true } : { error: "No such upcoming booking" };
    },
  },
  {
    name: "get_my_khata_page",
    description: "Private link where a khata customer can see their balance, every fill with slip numbers and monthly bills.",
    input_schema: obj({}),
    run: (ctx) => (ctx.customer.credit_limit > 0 ? { link: portalLink(ctx.customer as any) } : { error: "No khata account" }),
  },
  {
    name: "update_my_name",
    description: "Save the customer's name when they tell you it.",
    input_schema: obj({ name: { type: "string" } }, ["name"]),
    run: (ctx, i) => {
      run("UPDATE customers SET name=? WHERE id=?", String(i.name).slice(0, 80), ctx.customer.id);
      ctx.actions.push(`Name saved: ${i.name}`);
      return { ok: true };
    },
  },
];

export async function runTool(tools: ToolDef[], name: string, ctx: ToolCtx, input: unknown): Promise<{ content: string; is_error?: boolean }> {
  const t = tools.find((x) => x.name === name);
  if (!t) return { content: `Unknown tool ${name}`, is_error: true };
  try {
    const out = await t.run(ctx, input ?? {});
    return { content: JSON.stringify(out) };
  } catch (e: any) {
    return { content: `Tool failed: ${e.message}`, is_error: true };
  }
}

export const toolSchemas = (tools: ToolDef[]): Anthropic.Tool[] =>
  tools.map(({ run: _r, ...schema }) => schema);
