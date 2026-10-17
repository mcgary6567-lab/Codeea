import assert from "node:assert/strict";
/** Every closed shift's report: its meter lines (less test litres) add up to the litres it sold, fuel by fuel. */
export async function shiftMetersTally(label = "") {
  const db = await import("../../src/db.js");
  const { shiftReport } = await import("../../src/shifts.js");
  let n = 0;
  for (const sh of db.all("SELECT id FROM shifts WHERE status='closed' ORDER BY id DESC LIMIT 60")) {
    const r: any = shiftReport(sh.id);
    const byMeter: Record<string, number> = {};
    for (const x of r.readings) if (x.litres != null) byMeter[x.product] = (byMeter[x.product] ?? 0) + x.litres;
    for (const [p, l] of Object.entries(byMeter)) {
      const sold = db.get("SELECT COALESCE(SUM(litres),0) l FROM sales WHERE shift_id=? AND product=?", sh.id, p)!.l as number;
      assert.ok(Math.abs(sold - l) <= 0.6, `${label} shift ${sh.id} ${p}: meter lines ${l.toFixed(2)} L, sold ${sold.toFixed(2)} L`);
      n++;
    }
  }
  return n;
}
