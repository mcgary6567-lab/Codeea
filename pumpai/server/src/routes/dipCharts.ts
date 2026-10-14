/**
 * Dip charts (pro): the oil company's calibration table for a tank model — depth (mm) → litres.
 *  - One chart per tank size, assigned to any number of tanks (SaaS: every pump has its own charts).
 *  - Built-in templates for common U/G tanks; a photo of the printed sheet is read by AI into rows.
 *  - Rows are edited in a grid, pasted, imported as CSV, filled by step, exported and printed.
 *  - The dip entry on Tanks & Stock converts cm/mm → litres through the tank's assigned chart.
 */
import { Router } from "express";
import { z } from "zod";
import { all, get, run, tx, now } from "../db.js";
import { h, parse, tid, requirePerm } from "../auth.js";
import { AppError, audit, round2 } from "../services.js";
import { aiEnabled } from "../config.js";
import { readPhoto } from "../ai/vision.js";
import { DIP_CHART_TEMPLATES } from "../data/dipChartTemplates.js";

export const dipCharts = Router();
const MAX_BYTES = 8 * 1024 * 1024;

const rowSchema = z.object({ depth_mm: z.number().min(0).max(10_000), litres: z.number().min(0).max(1_000_000) });
const metaSchema = z.object({
  name: z.string().trim().min(2).max(120),
  product: z.string().trim().max(40).optional().nullable(),
  capacity_l: z.number().min(0).max(1_000_000).optional().nullable(),
  length_mm: z.number().min(0).max(100_000).optional().nullable(),
  diameter_mm: z.number().min(0).max(100_000).optional().nullable(),
  source: z.string().trim().max(300).optional().nullable(),
  notes: z.string().trim().max(1000).optional().nullable(),
});

/** Sorted, de-duplicated rows; depth must rise and litres must never fall. Returns the problems instead of throwing when `soft`. */
export function checkRows(input: { depth_mm: number; litres: number }[], soft = false) {
  const rows = [...input].map((r) => ({ depth_mm: round2(r.depth_mm), litres: round2(r.litres) })).sort((a, b) => a.depth_mm - b.depth_mm);
  const problems: { depth_mm: number; message: string }[] = [];
  for (let i = 1; i < rows.length; i++) {
    if (rows[i].depth_mm === rows[i - 1].depth_mm) problems.push({ depth_mm: rows[i].depth_mm, message: `${rows[i].depth_mm} mm is entered twice` });
    else if (rows[i].litres < rows[i - 1].litres) problems.push({ depth_mm: rows[i].depth_mm, message: `${rows[i].depth_mm} mm: ${rows[i].litres} L is less than ${rows[i - 1].depth_mm} mm (${rows[i - 1].litres} L)` });
  }
  if (!soft && problems.length) throw new AppError(400, `${problems.length} problem${problems.length > 1 ? "s" : ""} in the chart — ${problems[0].message}${problems.length > 1 ? " …" : ""}`);
  return { rows, problems };
}

/** Litres at a depth, straight-line between the two nearest chart rows. */
export function lookup(rows: { depth_mm: number; litres: number }[], mm: number) {
  if (rows.length < 2) throw new AppError(400, "Chart has fewer than 2 rows");
  if (mm < rows[0].depth_mm || mm > rows[rows.length - 1].depth_mm) throw new AppError(400, `Dip must be between ${rows[0].depth_mm} and ${rows[rows.length - 1].depth_mm} mm`);
  const i = rows.findIndex((r) => r.depth_mm >= mm);
  if (rows[i].depth_mm === mm) return rows[i].litres;
  const a = rows[i - 1], b = rows[i];
  return round2(a.litres + ((mm - a.depth_mm) / (b.depth_mm - a.depth_mm)) * (b.litres - a.litres));
}

/** Parse pasted text / CSV: any separators, optional header, several column pairs per line (as printed sheets have). */
export function parseText(text: string, unit: "mm" | "cm" = "mm") {
  const rows: { depth_mm: number; litres: number }[] = [];
  for (const line of text.split(/\r?\n/)) {
    const nums = line.replace(/,(?=\d{3}\b)/g, "").split(/[^\d.]+/).filter(Boolean).map(Number).filter((n) => !isNaN(n));
    for (let i = 0; i + 1 < nums.length; i += 2) rows.push({ depth_mm: unit === "cm" ? nums[i] * 10 : nums[i], litres: nums[i + 1] });
  }
  return rows;
}

const own = (t: number, id: number) => {
  const c = get("SELECT * FROM dip_charts WHERE id=? AND tenant_id=?", id, t);
  if (!c) throw new AppError(404, "Dip chart not found");
  return c;
};
const rowsOf = (id: number) => all("SELECT depth_mm, litres FROM dip_chart_rows WHERE chart_id=? ORDER BY depth_mm", id) as { depth_mm: number; litres: number }[];
const tanksOf = (t: number, id: number) => all("SELECT t.id, t.name, t.product, t.capacity_l, s.name station FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND t.dip_chart_id=? ORDER BY t.name", t, id);
const summary = (t: number, c: any) => {
  const agg = get("SELECT COUNT(*) n, MIN(depth_mm) min_mm, MAX(depth_mm) max_mm, MAX(litres) max_l FROM dip_chart_rows WHERE chart_id=?", c.id);
  return { ...c, rows: agg.n, min_mm: agg.min_mm, max_mm: agg.max_mm, max_l: agg.max_l, tanks: tanksOf(t, c.id) };
};
const who = (req: any) => req.user?.name ?? null;

/* ---------- list / templates ---------- */
dipCharts.get("/dip-charts", requirePerm("stock.manage"), h((req) => {
  const t = tid(req);
  return {
    charts: all("SELECT * FROM dip_charts WHERE tenant_id=? ORDER BY name", t).map((c) => summary(t, c)),
    tanks: all("SELECT t.id, t.name, t.product, t.capacity_l, t.dip_chart_id, s.name station, (SELECT COUNT(*) FROM tank_charts WHERE tank_id=t.id) legacy_rows FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? AND t.active=1 ORDER BY s.name, t.name", t),
    templates: DIP_CHART_TEMPLATES.map((x) => ({ key: x.key, name: x.name, product: x.product, capacity_l: x.capacity_l, length_mm: x.length_mm, diameter_mm: x.diameter_mm, source: x.source, rows: x.rows.length, max_mm: x.rows[x.rows.length - 1][0] })),
    ai: aiEnabled(),
  };
}));

dipCharts.get("/dip-charts/templates/:key", requirePerm("stock.manage"), h((req) => {
  const tpl = DIP_CHART_TEMPLATES.find((x) => x.key === req.params.key);
  if (!tpl) throw new AppError(404, "Template not found");
  return { ...tpl, rows: tpl.rows.map(([depth_mm, litres]) => ({ depth_mm, litres })) };
}));

/* ---------- create ---------- */
dipCharts.post("/dip-charts", requirePerm("stock.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({
    template_key: z.string().optional(),
    rows: z.array(rowSchema).max(5000).optional(),
    text: z.string().max(200_000).optional(), unit: z.enum(["mm", "cm"]).optional(),
    diameter_mm: z.number().min(300).max(6000).optional(), // approximate cylinder chart
    photo_id: z.number().int().positive().optional(),
    assign_tank_ids: z.array(z.number().int().positive()).max(50).optional(),
  }).merge(metaSchema.partial()), req.body);

  let meta: any = { name: b.name, product: b.product ?? null, capacity_l: b.capacity_l ?? null, length_mm: b.length_mm ?? null, diameter_mm: b.diameter_mm ?? null, source: b.source ?? null, notes: b.notes ?? null, template_key: null as string | null };
  let rows: { depth_mm: number; litres: number }[] = [];
  if (b.template_key) {
    const tpl = DIP_CHART_TEMPLATES.find((x) => x.key === b.template_key);
    if (!tpl) throw new AppError(404, "Template not found");
    meta = { name: b.name ?? tpl.name, product: b.product ?? tpl.product, capacity_l: b.capacity_l ?? tpl.capacity_l, length_mm: b.length_mm ?? tpl.length_mm ?? null, diameter_mm: b.diameter_mm ?? tpl.diameter_mm ?? null, source: b.source ?? tpl.source, notes: b.notes ?? tpl.notes ?? null, template_key: tpl.key };
    rows = tpl.rows.map(([depth_mm, litres]) => ({ depth_mm, litres }));
  } else if (b.rows?.length) rows = b.rows;
  else if (b.text) rows = parseText(b.text, b.unit ?? "mm");
  else if (b.diameter_mm && b.capacity_l) {
    const r = b.diameter_mm / 2, full = Math.PI * r * r;
    const area = (hh: number) => r * r * Math.acos((r - hh) / r) - (r - hh) * Math.sqrt(Math.max(0, 2 * r * hh - hh * hh));
    for (let hh = 0; hh < b.diameter_mm; hh += 10) rows.push({ depth_mm: hh, litres: Math.round((area(hh) / full) * b.capacity_l) });
    rows.push({ depth_mm: b.diameter_mm, litres: Math.round(b.capacity_l) });
    meta.source = meta.source ?? "Approximate (horizontal cylinder formula) — replace with the oil company's chart";
  }
  if (!meta.name) throw new AppError(400, "Give the chart a name (e.g. HSD tank 14,370 L)");
  if (rows.length < 2) throw new AppError(400, "A chart needs at least 2 rows (depth and litres)");
  const checked = checkRows(rows);
  const id = tx(() => {
    const { id } = run("INSERT INTO dip_charts (tenant_id,name,product,capacity_l,length_mm,diameter_mm,source,notes,template_key,photo_id,created_by,created_at,updated_by,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      t, meta.name, meta.product, meta.capacity_l, meta.length_mm, meta.diameter_mm, meta.source, meta.notes, meta.template_key, b.photo_id ?? null, who(req), now(), who(req), now());
    for (const r of checked.rows) run("INSERT INTO dip_chart_rows (chart_id,depth_mm,litres) VALUES (?,?,?)", id, r.depth_mm, r.litres);
    for (const tk of b.assign_tank_ids ?? []) run("UPDATE tanks SET dip_chart_id=? WHERE id=? AND station_id IN (SELECT id FROM stations WHERE tenant_id=?)", id, tk, t);
    return id;
  });
  audit(t, req.user!, "dip_chart_create", `dip_chart:${id}`, { name: meta.name, rows: checked.rows.length, template: meta.template_key });
  return summary(t, own(t, id));
}));

/* ---------- read ---------- */
dipCharts.get("/dip-charts/:id", requirePerm("stock.manage"), h((req) => {
  const t = tid(req), c = own(t, Number(req.params.id));
  return { ...summary(t, c), rows: rowsOf(c.id) };
}));

dipCharts.get("/dip-charts/:id/lookup", requirePerm("stock.manage"), h((req) => {
  const t = tid(req), c = own(t, Number(req.params.id));
  const mm = req.query.mm !== undefined ? Number(req.query.mm) : Number(req.query.cm) * 10;
  if (isNaN(mm)) throw new AppError(400, "Give the dip in mm or cm");
  const rows = rowsOf(c.id);
  const litres = lookup(rows, mm);
  // next 5 mm up and down, so the salesman sees how much one small slip of the stick is worth
  const step = rows.length > 1 ? rows[1].depth_mm - rows[0].depth_mm : 5;
  const up = mm + step <= rows[rows.length - 1].depth_mm ? lookup(rows, mm + step) : null, down = mm - step >= rows[0].depth_mm ? lookup(rows, mm - step) : null;
  return { mm, litres, step_mm: step, up, down, pct_full: c.capacity_l ? round2((litres / c.capacity_l) * 100) : null };
}));

dipCharts.get("/dip-charts/:id/export.csv", requirePerm("stock.manage"), h((req, res) => {
  const t = tid(req), c = own(t, Number(req.params.id));
  res.setHeader("content-type", "text/csv; charset=utf-8");
  res.setHeader("content-disposition", `attachment; filename="dip-chart-${c.id}.csv"`);
  res.send(`depth_mm,litres\n${rowsOf(c.id).map((r) => `${r.depth_mm},${r.litres}`).join("\n")}\n`);
}));

/* ---------- edit ---------- */
dipCharts.put("/dip-charts/:id", requirePerm("stock.manage"), h((req) => {
  const t = tid(req), c = own(t, Number(req.params.id));
  const b = parse(metaSchema.partial().extend({ rows: z.array(rowSchema).min(2).max(5000).optional() }), req.body);
  const checked = b.rows ? checkRows(b.rows) : null;
  tx(() => {
    run("UPDATE dip_charts SET name=?, product=?, capacity_l=?, length_mm=?, diameter_mm=?, source=?, notes=?, updated_by=?, updated_at=? WHERE id=?",
      b.name ?? c.name, b.product === undefined ? c.product : b.product, b.capacity_l === undefined ? c.capacity_l : b.capacity_l, b.length_mm === undefined ? c.length_mm : b.length_mm,
      b.diameter_mm === undefined ? c.diameter_mm : b.diameter_mm, b.source === undefined ? c.source : b.source, b.notes === undefined ? c.notes : b.notes, who(req), now(), c.id);
    if (checked) {
      run("DELETE FROM dip_chart_rows WHERE chart_id=?", c.id);
      for (const r of checked.rows) run("INSERT INTO dip_chart_rows (chart_id,depth_mm,litres) VALUES (?,?,?)", c.id, r.depth_mm, r.litres);
    }
  });
  audit(t, req.user!, "dip_chart_update", `dip_chart:${c.id}`, { name: b.name ?? c.name, rows: checked?.rows.length ?? null });
  return { ...summary(t, own(t, c.id)), rows: rowsOf(c.id) };
}));

/** Dry-run check of rows (grid validation without saving): problems, gaps in the step, and the implied full volume. */
dipCharts.post("/dip-charts/check", requirePerm("stock.manage"), h((req) => {
  const b = parse(z.object({ rows: z.array(rowSchema).max(5000), text: z.string().max(200_000).optional(), unit: z.enum(["mm", "cm"]).optional() }), req.body);
  const rows = b.text ? parseText(b.text, b.unit ?? "mm") : b.rows;
  const { rows: sorted, problems } = checkRows(rows, true);
  // the usual step, and the depths that step skips
  const steps = new Map<number, number>();
  for (let i = 1; i < sorted.length; i++) { const d = round2(sorted[i].depth_mm - sorted[i - 1].depth_mm); steps.set(d, (steps.get(d) ?? 0) + 1); }
  const step = [...steps.entries()].sort((a, b2) => b2[1] - a[1])[0]?.[0] ?? null;
  const gaps: number[] = [];
  if (step) for (let i = 1; i < sorted.length && gaps.length < 50; i++) { const d = sorted[i].depth_mm - sorted[i - 1].depth_mm; if (d > step * 1.5) for (let m = sorted[i - 1].depth_mm + step; m < sorted[i].depth_mm - 1e-9; m += step) gaps.push(round2(m)); }
  // a jump much bigger than its neighbours is usually a misread digit
  const jumps: { depth_mm: number; delta: number; typical: number }[] = [];
  // compared per mm, so a gap in the rows (a missing step) is not mistaken for a misread digit
  const slope = (i: number) => (sorted[i].litres - sorted[i - 1].litres) / Math.max(1e-9, sorted[i].depth_mm - sorted[i - 1].depth_mm);
  for (let i = 2; i < sorted.length - 1; i++) {
    const d = slope(i), around = (slope(i - 1) + slope(i + 1)) / 2, span = sorted[i].depth_mm - sorted[i - 1].depth_mm;
    if (around > 0 && (d > around * 3 + 4 / span || d < around / 4 - 4 / span)) jumps.push({ depth_mm: sorted[i].depth_mm, delta: round2(d * span), typical: round2(around * span) });
  }
  return { rows: sorted, count: sorted.length, problems, step_mm: step, gaps, jumps: jumps.slice(0, 50), max_mm: sorted[sorted.length - 1]?.depth_mm ?? null, max_l: sorted[sorted.length - 1]?.litres ?? null };
}));

/** Fill the missing steps by straight line, e.g. a chart typed every 10 mm becomes every 5 mm. */
dipCharts.post("/dip-charts/:id/fill", requirePerm("stock.manage"), h((req) => {
  const t = tid(req), c = own(t, Number(req.params.id));
  const b = parse(z.object({ step_mm: z.number().min(0.5).max(100) }), req.body);
  const rows = rowsOf(c.id);
  if (rows.length < 2) throw new AppError(400, "Chart has fewer than 2 rows");
  const out: { depth_mm: number; litres: number }[] = [];
  for (let m = rows[0].depth_mm; m <= rows[rows.length - 1].depth_mm + 1e-9; m = round2(m + b.step_mm)) out.push({ depth_mm: round2(m), litres: lookup(rows, round2(m)) });
  if (round2(out[out.length - 1].depth_mm) !== rows[rows.length - 1].depth_mm) out.push(rows[rows.length - 1]);
  tx(() => {
    run("DELETE FROM dip_chart_rows WHERE chart_id=?", c.id);
    for (const r of out) run("INSERT INTO dip_chart_rows (chart_id,depth_mm,litres) VALUES (?,?,?)", c.id, r.depth_mm, r.litres);
    run("UPDATE dip_charts SET updated_by=?, updated_at=? WHERE id=?", who(req), now(), c.id);
  });
  audit(t, req.user!, "dip_chart_fill", `dip_chart:${c.id}`, { step_mm: b.step_mm, rows: out.length });
  return { rows: out.length };
}));

dipCharts.post("/dip-charts/:id/copy", requirePerm("stock.manage"), h((req) => {
  const t = tid(req), c = own(t, Number(req.params.id));
  const b = parse(z.object({ name: z.string().trim().min(2).max(120) }), req.body);
  const id = tx(() => {
    const { id } = run("INSERT INTO dip_charts (tenant_id,name,product,capacity_l,length_mm,diameter_mm,source,notes,template_key,photo_id,created_by,created_at,updated_by,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      t, b.name, c.product, c.capacity_l, c.length_mm, c.diameter_mm, c.source, c.notes, c.template_key, c.photo_id, who(req), now(), who(req), now());
    run("INSERT INTO dip_chart_rows (chart_id,depth_mm,litres) SELECT ?, depth_mm, litres FROM dip_chart_rows WHERE chart_id=?", id, c.id);
    return id;
  });
  return summary(t, own(t, id));
}));

/* ---------- assign to tanks ---------- */
dipCharts.put("/dip-charts/:id/tanks", requirePerm("stock.manage"), h((req) => {
  const t = tid(req), c = own(t, Number(req.params.id));
  const b = parse(z.object({ tank_ids: z.array(z.number().int().positive()).max(100) }), req.body);
  tx(() => {
    run("UPDATE tanks SET dip_chart_id=NULL WHERE dip_chart_id=? AND station_id IN (SELECT id FROM stations WHERE tenant_id=?)", c.id, t);
    for (const tk of b.tank_ids) {
      const r = run("UPDATE tanks SET dip_chart_id=? WHERE id=? AND station_id IN (SELECT id FROM stations WHERE tenant_id=?)", c.id, tk, t);
      if (!r.changes) throw new AppError(404, `Tank ${tk} not found`);
    }
  });
  audit(t, req.user!, "dip_chart_assign", `dip_chart:${c.id}`, { tank_ids: b.tank_ids });
  return summary(t, c);
}));

/** One tank → one chart (or none); used from the Tanks & Stock page. */
dipCharts.put("/tanks/:id/dip-chart", requirePerm("stock.manage"), h((req) => {
  const t = tid(req);
  const b = parse(z.object({ chart_id: z.number().int().positive().nullable() }), req.body);
  if (b.chart_id) own(t, b.chart_id);
  const r = run("UPDATE tanks SET dip_chart_id=? WHERE id=? AND station_id IN (SELECT id FROM stations WHERE tenant_id=?)", b.chart_id, Number(req.params.id), t);
  if (!r.changes) throw new AppError(404, "Tank not found");
  audit(t, req.user!, "dip_chart_assign", `tank:${req.params.id}`, { chart_id: b.chart_id });
  return { ok: true };
}));

dipCharts.delete("/dip-charts/:id", requirePerm("stock.manage"), h((req) => {
  const t = tid(req), c = own(t, Number(req.params.id));
  const used = tanksOf(t, c.id);
  if (used.length && req.query.force !== "1") throw new AppError(400, `${used.length} tank${used.length > 1 ? "s" : ""} use this chart (${used.map((x) => x.name).join(", ")}). Assign them another chart first, or delete with force.`);
  tx(() => {
    run("UPDATE tanks SET dip_chart_id=NULL WHERE dip_chart_id=?", c.id);
    run("DELETE FROM dip_chart_rows WHERE chart_id=?", c.id);
    run("DELETE FROM dip_charts WHERE id=?", c.id);
  });
  audit(t, req.user!, "dip_chart_delete", `dip_chart:${c.id}`, { name: c.name, rows_lost: true });
  return { ok: true };
}));

/* ---------- read a photo of the printed sheet ---------- */
dipCharts.post("/dip-charts/read-photo", requirePerm("stock.manage"), h(async (req) => {
  const b = parse(z.object({ image: z.string().regex(/^data:image\/(jpeg|png|webp);base64,/, "Send a JPEG, PNG or WebP photo"), hint: z.string().max(300).optional() }), req.body);
  if (!aiEnabled()) throw new AppError(400, "Photo reading needs the AI key — Settings → Integrations. Until then paste or type the rows.");
  const [, mime, data] = b.image.match(/^data:(image\/[a-z]+);base64,(.*)$/s)!;
  const bytes = Buffer.from(data, "base64");
  if (bytes.length > MAX_BYTES) throw new AppError(400, "Photo is too large");
  const real = bytes.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff])) ? "image/jpeg"
    : bytes.subarray(0, 4).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47])) ? "image/png"
    : bytes.subarray(0, 4).toString("latin1") === "RIFF" && bytes.subarray(8, 12).toString("latin1") === "WEBP" ? "image/webp" : null;
  if (!real) throw new AppError(400, "Send a JPEG, PNG or WebP photo");
  const result = await readPhoto("dipchart", data, mime, b.hint ?? "") as any;
  const { id } = run("INSERT INTO photos (tenant_id,kind,mime,data,ai_result,created_by,created_at) VALUES (?,?,?,?,?,?,?)", tid(req), "dipchart", real, bytes, result ? JSON.stringify(result) : null, req.user!.id, now());
  if (!result?.rows?.length) throw new AppError(400, "Could not read the chart from this photo. Try a straighter, brighter photo, or paste the rows.");
  const unit = result.unit === "cm" ? "cm" : "mm";
  const rows = (result.rows as { depth: number; litres: number }[]).filter((r) => typeof r.depth === "number" && typeof r.litres === "number").map((r) => ({ depth_mm: unit === "cm" ? r.depth * 10 : r.depth, litres: r.litres }));
  const { rows: sorted, problems } = checkRows(rows, true);
  return {
    photo_id: id, rows: sorted, problems, unclear_mm: (result.unclear ?? []).map((x: number) => unit === "cm" ? x * 10 : x), confidence: result.confidence ?? "low",
    header: { title: result.title ?? null, length_mm: result.length_mm ?? null, diameter_mm: result.diameter_mm ?? null, capacity_petrol_l: result.capacity_petrol_l ?? null, capacity_hsd_l: result.capacity_hsd_l ?? null, unit },
  };
}));
