/**
 * The depot → company → banda picker, used everywhere a supplier/depot is chosen (stock delivery, orders,
 * wholesale bypass, carriage). Suppliers are grouped under their depot (an <optgroup> header), and each option
 * is labelled "Company — Person" so the right man at the depot is picked.
 */
export function supplierOpts(list: any[], flagNoWa = false) {
  const groups = new Map<string, { name: string; rows: any[] }>();
  for (const s of list) {
    const key = s.depot_id ? `d${s.depot_id}` : "none";
    if (!groups.has(key)) groups.set(key, { name: s.depot_id ? (s.depot_name ?? "Depot") : "No depot", rows: [] });
    groups.get(key)!.rows.push(s);
  }
  const opt = (s: any) => <option key={s.id} value={s.id}>{[s.company, s.name].filter(Boolean).join(" — ")}{flagNoWa && !s.phone ? " (no WhatsApp)" : ""}</option>;
  const gs = [...groups.values()];
  // when nothing is grouped under a depot, a flat list reads better than one "No depot" group
  if (gs.length === 1 && gs[0].name === "No depot") return gs[0].rows.map(opt);
  return gs.map((g, i) => <optgroup key={i} label={g.name}>{g.rows.map(opt)}</optgroup>);
}

/** A ready-made depot/supplier <select> (depot header → company — banda). */
export function SupplierSelect({ list, value, onChange, required, placeholder = "— choose depot / supplier —", flagNoWa }: {
  list: any[]; value: string | number | ""; onChange: (id: string) => void; required?: boolean; placeholder?: string; flagNoWa?: boolean;
}) {
  return (
    <select className="input" required={required} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{placeholder}</option>
      {supplierOpts(list, flagNoWa)}
    </select>
  );
}
