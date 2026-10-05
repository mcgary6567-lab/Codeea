/** Demo data: one business, two stations, 8 weeks of realistic sales, customers, khata and WhatsApp chats. */
import bcrypt from "bcryptjs";
import { db, migrate, run, all, get, tx, setSetting } from "./db.js";
import { scoreCustomers, detectAnomalies } from "./ai/analytics.js";
import { createAlert } from "./services.js";
import { ensureAutomations } from "./automation/scheduler.js";
import { DEFAULT_CATEGORIES } from "./routes/expenses.js";
import { cylinderChart } from "./routes/backoffice.js";

let s = 42;
const rnd = () => ((s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296);
const pick = <T>(a: T[]) => a[Math.floor(rnd() * a.length)];
const DAY = 86_400_000;
const iso = (t: number) => new Date(t).toISOString();

const NAMES = [
  ["Muhammad Asif", "retail"], ["Ali Raza", "retail"], ["Bilal Ahmed", "retail"], ["Usman Tariq", "retail"], ["Hamza Iqbal", "retail"],
  ["Ayesha Khan", "retail"], ["Fatima Noor", "retail"], ["Zubair Hussain", "retail"], ["Kashif Mehmood", "retail"], ["Sana Javed", "retail"],
  ["Imtiaz Ali", "retail"], ["Naveed Akhtar", "retail"], ["Shahid Butt", "retail"], ["Rizwan Qureshi", "retail"], ["Adnan Malik", "retail"],
  ["Faisal Chaudhry", "retail"], ["Saima Bibi", "retail"], ["Waqas Anwar", "retail"], ["Junaid Shah", "retail"], ["Tahir Mahmood", "retail"],
  ["Rana Arshad (Tube-well)", "farmer"], ["Chaudhry Nazir Farms", "farmer"], ["Malik Sarwar Agri", "farmer"], ["Haji Bashir Zamindar", "farmer"],
  ["Daewoo Cargo Lahore", "fleet"], ["Bismillah Goods Transport", "fleet"], ["Al-Karam Rent a Car", "fleet"], ["Shaheen Rickshaw Union", "fleet"],
  ["Lahore School Vans", "fleet"], ["Punjab Builders (Pvt) Ltd", "business"], ["City Bakers Generators", "business"], ["Green Valley Hospital", "business"],
  ["Police Station Kahna", "police"], ["Police Station Model Town", "police"], ["Rescue 1122 Lahore", "government"],
  ["Govt. High School No. 1 Kahna", "school"], ["District Health Office Lahore", "hospital"],
] as const;

export function seed() {
  migrate();
  const exists = get("SELECT id FROM tenants LIMIT 1");
  if (exists) return;
  s = 42;
  const T0 = Date.now();
  tx(() => {
    const tenantId = run("INSERT INTO tenants (name, owner_name, owner_phone) VALUES (?,?,?)", "Al-Madina Petroleum", "Haji Abdul Rehman", "923001234567").id;
    const hash = bcrypt.hashSync("demo1234", 8);
    run("INSERT INTO users (tenant_id,name,email,password_hash,role) VALUES (?,?,?,?,?)", tenantId, "Haji Abdul Rehman (CEO)", "admin@pumpai.pk", hash, "admin");
    run("INSERT INTO users (tenant_id,name,email,password_hash,role) VALUES (?,?,?,?,?)", tenantId, "Kamran Shah", "manager@pumpai.pk", hash, "manager");
    run("INSERT INTO users (tenant_id,name,email,password_hash,role,station_id,phone) VALUES (?,?,?,?,?,1,?)", tenantId, "Imran", "salesman@pumpai.pk", hash, "salesman", "923011234567");
    run("INSERT INTO users (tenant_id,name,email,password_hash,role) VALUES (?,?,?,?,?)", tenantId, "Tariq Wholesale", "wholesale@pumpai.pk", hash, "wholesale");
    // demo quick-login PINs: admin 1111, manager 2222, salesman 3333, wholesale 4444
    for (const [email, pin] of [["admin", "1111"], ["manager", "2222"], ["salesman", "3333"], ["wholesale", "4444"]])
      run("UPDATE users SET pin_hash=? WHERE email=?", bcrypt.hashSync(pin, 8), `${email}@pumpai.pk`);
    // staff salaries and an advance, so the staff accounts page has a starting point
    for (const [email, salary] of [["manager", 65000], ["salesman", 32000], ["wholesale", 45000]] as const)
      run("UPDATE users SET salary=? WHERE email=?", salary, `${email}@pumpai.pk`);
    const imran = get("SELECT id FROM users WHERE email='salesman@pumpai.pk'")!.id;
    run("INSERT INTO staff_ledger (tenant_id,user_id,type,amount,note,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
      tenantId, imran, "advance", 5000, "Bachon ki school fees", "Kamran Shah", iso(T0 - 9 * DAY));

    const st1 = run("INSERT INTO stations (tenant_id,name,city,address,omc,lat,lng,timings,services) VALUES (?,?,?,?,?,?,?,?,?)",
      tenantId, "Al-Madina Ferozepur Road", "Lahore", "Ferozepur Road, near Kalma Chowk, Lahore", "PSO", 31.5003, 74.3311, "24 hours", "Petrol, Hi-Octane, Diesel, Tuck shop, Air, Car wash").id;
    const st2 = run("INSERT INTO stations (tenant_id,name,city,address,omc,lat,lng,timings,services) VALUES (?,?,?,?,?,?,?,?,?)",
      tenantId, "Al-Madina Okara Bypass", "Okara", "GT Road Bypass, Okara", "PSO", 30.8081, 73.4458, "24 hours", "Petrol, Diesel, Tyre shop, Lubricants, Truck parking").id;

    const tankDefs: [number, string, string, number, number][] = [
      [st1, "Tank-1 Petrol", "PMG", 30000, 21500], [st1, "Tank-2 Hi-Octane", "HOBC", 10000, 6200], [st1, "Tank-3 Diesel", "HSD", 30000, 9800],
      [st2, "Tank-1 Petrol", "PMG", 20000, 4100], [st2, "Tank-2 Diesel", "HSD", 40000, 27500],
    ];
    const tanks = tankDefs.map(([sid, name, p, cap, cur]) => {
      const id = run("INSERT INTO tanks (station_id,name,product,capacity_l,current_l,reorder_pct) VALUES (?,?,?,?,?,25)", sid, name, p, cap, cur).id;
      // dip chart (cm → litres) for a horizontal cylindrical tank
      for (const r of cylinderChart(cap >= 30000 ? 280 : 240, cap)) run("INSERT INTO tank_charts (tank_id,cm,litres) VALUES (?,?,?)", id, r.cm, r.litres);
      const noz = [1, 2].map((i) => run("INSERT INTO nozzles (station_id,tank_id,label,totalizer) VALUES (?,?,?,?)", sid, id, `${p}-${sid}${i}`, 100000 + Math.round(rnd() * 50000)).id);
      return { id, sid, p, noz };
    });

    // Price history (demo values — update to the current OGRA notification from the Prices page)
    const priceSteps = [
      { d: 70, PMG: 252.1, HOBC: 280.5, HSD: 258.4 }, { d: 55, PMG: 255.6, HOBC: 284.0, HSD: 261.9 },
      { d: 40, PMG: 253.0, HOBC: 281.4, HSD: 263.5 }, { d: 25, PMG: 258.2, HOBC: 286.6, HSD: 266.8 }, { d: 10, PMG: 262.5, HOBC: 291.0, HSD: 268.9 },
    ];
    for (const step of priceSteps) for (const p of ["PMG", "HOBC", "HSD"] as const)
      run("INSERT INTO prices (tenant_id,product,price,effective_from,created_by) VALUES (?,?,?,?,?)", tenantId, p, step[p], iso(T0 - step.d * DAY), "OGRA");
    const priceAt = (p: string, t: number) => [...priceSteps].reverse().find((x) => T0 - x.d * DAY <= t)?.[p as "PMG"] ?? priceSteps[0][p as "PMG"];

    // Customers
    const customers = NAMES.map(([name, type], i) => {
      const phone = `923${String(10 + (i % 40)).padStart(2, "0")}${String(1000000 + Math.floor(rnd() * 8999999))}`;
      const limit = type === "fleet" ? pick([300000, 500000, 800000]) : type === "farmer" ? pick([200000, 400000]) : type === "business" ? 250000
        : ({ police: 600000, government: 500000, school: 150000, hospital: 400000 } as Record<string, number>)[type] ?? (i % 5 === 0 ? 30000 : 0);
      const institution = ["police", "school", "government", "hospital"].includes(type);
      const id = run("INSERT INTO customers (tenant_id,name,phone,type,city,credit_limit,opt_in,loyalty_points,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        tenantId, name, phone, type, institution || i % 3 !== 0 ? "Lahore" : "Okara", limit, i % 11 === 10 || institution ? 0 : 1, 0, iso(T0 - 120 * DAY)).id;
      const plates: { plate: string; fuel: string }[] = [];
      const addVehicle = (plate: string, fuel: string, limitL?: number) => { plates.push({ plate, fuel }); run("INSERT INTO vehicles (customer_id,plate_no,fuel,daily_limit_l) VALUES (?,?,?,?)", id, plate, fuel, limitL ?? null); };
      if (type === "police") for (let v = 1; v <= 4; v++) addVehicle(`LEJ-${1100 + Math.floor(rnd() * 800)} (Mobile ${v})`, v === 4 ? "HSD" : "PMG", 40);
      if (type === "government") for (let v = 1; v <= 3; v++) addVehicle(`LEC-1122-${v}`, "HSD", 80);
      if (type === "school") addVehicle(`LES-${2000 + Math.floor(rnd() * 900)} (School van)`, "PMG", 30);
      if (type === "hospital") for (let v = 1; v <= 2; v++) addVehicle(`LED-${3000 + Math.floor(rnd() * 900)} (Ambulance)`, "HSD", 60);
      if (type === "fleet") for (let v = 0; v < 3; v++) addVehicle(`${pick(["LES", "LEA", "LEB", "LZT"])}-${1000 + Math.floor(rnd() * 8999)}`, pick(["HSD", "PMG"]), 120);
      else if (type === "retail") addVehicle(`${pick(["LEC", "LEH", "LXR", "OKA"])}-${100 + Math.floor(rnd() * 899)}`, "PMG");
      // some regulars churn: last visit set by sales below; mark a few as "stopped coming" 25-40 days ago
      return { id, type, limit, institution, plates, churnFrom: i % 7 === 3 ? 20 + Math.floor(rnd() * 20) : 0, freq: type === "retail" ? 0.25 + rnd() * 0.3 : 0.6 };
    });

    // 8 weeks of sales
    const base: Record<string, number> = { [`${st1}PMG`]: 5200, [`${st1}HOBC`]: 650, [`${st1}HSD`]: 3800, [`${st2}PMG`]: 2600, [`${st2}HSD`]: 6400 };
    const weekday = [1.08, 0.94, 0.96, 0.98, 1.12, 1.15, 0.92]; // Sun..Sat (Friday/Saturday peaks)
    const insertSale = db.prepare(`INSERT INTO sales (station_id,shift_id,customer_id,nozzle_id,product,litres,rate,amount,payment_method,vehicle_no,slip_no,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`);
    let slipNo = 4100;
    const ledger: Record<number, { debit: number; last: number }> = {};
    for (let d = 56; d >= 0; d--) {
      const dayStart = new Date(T0 - d * DAY); dayStart.setUTCHours(0, 0, 0, 0);
      const ds = dayStart.getTime();
      const growth = 1 + (56 - d) * 0.0025;
      for (const tk of tanks) {
        const key = `${tk.sid}${tk.p}`;
        let target = base[key] * weekday[dayStart.getUTCDay()] * growth * (0.9 + rnd() * 0.2);
        if (tk.p === "HSD" && tk.sid === st2 && d < 14) target *= 1.18; // harvest season diesel demand
        if (d === 0) target *= Math.min(1, (T0 - ds) / DAY); // today so far
        let sold = 0;
        while (sold < target) {
          const t = ds + Math.floor(rnd() * Math.min(DAY, T0 - ds));
          const cust = rnd() < 0.28 ? pick(customers) : null;
          const usable = cust && !(cust.churnFrom && d < cust.churnFrom) && (cust.type !== "retail" || tk.p !== "HSD")
            && !(cust.institution && (tk.sid !== st1 || !cust.plates.some((v) => v.fuel === tk.p))) ? cust : null;
          const litres = Math.round((usable?.institution ? (tk.p === "HSD" ? 30 + rnd() * 70 : 15 + rnd() * 30)
            : tk.p === "HSD" ? (usable && usable.type !== "retail" ? 60 + rnd() * 240 : 15 + rnd() * 60) : 3 + rnd() * 30) * 100) / 100;
          const rate = priceAt(tk.p, t);
          const amount = Math.round(litres * rate * 100) / 100;
          let method = pick(["cash", "cash", "cash", "cash", "jazzcash", "easypaisa", "card", "raast"]);
          if (usable && usable.limit > 0 && (usable.institution || rnd() < 0.75)) method = "khata";
          const fits = usable?.plates.filter((v) => v.fuel === tk.p) ?? [];
          const vehicle = fits.length ? pick(fits).plate : usable?.plates.length && !usable.institution ? pick(usable.plates).plate : null;
          const slip = usable?.institution ? `${usable.type === "police" ? "PS" : usable.type === "school" ? "SCH" : "GOV"}-${slipNo++}` : null;
          insertSale.run(tk.sid, null, usable?.id ?? null, pick(tk.noz), tk.p, litres, rate, amount, method, vehicle, slip, iso(t));
          if (usable) {
            run("UPDATE customers SET last_visit_at = MAX(COALESCE(last_visit_at,''), ?), loyalty_points = loyalty_points + ? WHERE id=?", iso(t), Math.floor(amount / 100), usable.id);
            if (method === "khata") {
              run(`INSERT INTO khata_ledger (customer_id,type,amount,ref,note,product,litres,rate,vehicle_no,slip_no,station_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
                usable.id, "debit", amount, "SALE", `${litres}L ${tk.p} @ Rs ${rate}`, tk.p, litres, rate, vehicle, slip, tk.sid, iso(t));
              (ledger[usable.id] ??= { debit: 0, last: 0 }).debit += amount;
            }
          }
          sold += litres;
        }
      }
      // weekly payments from credit customers
      if (d % 7 === 2) for (const c of customers) {
        const l = ledger[c.id];
        if (!l || l.debit <= 0) continue;
        if (c.type === "business" && d < 45) continue; // businesses that stopped paying ~6-7 weeks ago (aging demo)
        if (c.type === "farmer" && c.id % 2 === 0 && d < 75) continue; // pays after harvest: last payment ~11 weeks ago
        const payRatio = c.id % 4 === 0 ? 0.2 : 0.85; // some slow payers
        const pay = Math.floor(l.debit * payRatio / 1000) * 1000;
        if (pay > 0) {
          run("INSERT INTO khata_ledger (customer_id,type,amount,ref,note,created_at) VALUES (?,?,?,?,?,?)", c.id, "credit", pay, pick(["Cash", "Bank transfer", "JazzCash", "Raast"]), "Payment received", iso(ds + 15 * 3600_000));
          l.debit -= pay;
        }
      }
      // shifts (2 per station per day)
      if (d > 0) for (const sid of [st1, st2]) for (const [hStart, att] of [[6, sid === st1 ? "Imran" : "Shakeel"], [18, sid === st1 ? "Asghar" : "Nadeem"]] as const) {
        const opened = ds + hStart * 3600_000;
        const cash = 600000 + rnd() * 400000;
        let variance = Math.round((rnd() - 0.55) * 600);
        if (att === "Imran" && d <= 2) variance = -6400; // recent shortage for the anomaly detector
        run("INSERT INTO shifts (station_id,attendant,opened_at,closed_at,status,litres,cash_expected,cash_actual,variance) VALUES (?,?,?,?,?,?,?,?,?)",
          sid, att, iso(opened), iso(opened + 12 * 3600_000), "closed", Math.round(cash / 260), Math.round(cash), Math.round(cash + variance), variance);
      }
    }
    // balances from ledger
    run(`UPDATE customers SET balance = COALESCE((SELECT SUM(CASE WHEN type='debit' THEN amount ELSE -amount END) FROM khata_ledger k WHERE k.customer_id=customers.id),0)`);
    run("UPDATE customers SET credit_limit = MAX(credit_limit, ROUND(balance/10000+1)*10000) WHERE balance > credit_limit");

    // WhatsApp conversations
    const conv = (cid: number, mode: string, msgs: [string, string, number][]) => {
      const id = run("INSERT INTO conversations (tenant_id,customer_id,mode,status,last_inbound_at,last_message_at,unread) VALUES (?,?,?,?,?,?,?)",
        tenantId, cid, mode, "open", iso(T0 - msgs[msgs.length - 1][2] * 60_000), iso(T0 - msgs[msgs.length - 1][2] * 60_000), mode === "human" ? 1 : 0).id;
      for (const [sender, body, minsAgo] of msgs)
        run("INSERT INTO messages (conversation_id,direction,sender,body,meta,created_at) VALUES (?,?,?,?,?,?)", id, sender === "customer" ? "in" : "out", sender, body,
          sender === "ai" ? JSON.stringify({ engine: "rules", delivery: { simulated: true } }) : null, iso(T0 - minsAgo * 60_000));
    };
    const farmer = customers.find((c) => c.type === "farmer")!;
    run("UPDATE customers SET credit_limit = ROUND((balance + 800000) / 100000) * 100000 WHERE id=?", farmer.id);
    const fleet = customers.find((c) => c.type === "fleet")!;
    conv(customers[0].id, "ai", [
      ["customer", "aoa bhai aaj petrol ka kya rate hai?", 180],
      ["ai", "Wa Alaikum Assalam! ⛽ Aaj ke rates:\n• Petrol (Super): Rs 262/L\n• Hi-Octane: Rs 291/L\n• Diesel (HSD): Rs 269/L", 179],
      ["customer", "shukriya", 178], ["ai", "Aap ka bhi shukriya! Safar mubarak 🚗⛽", 178],
    ]);
    conv(farmer.id, "ai", [
      ["customer", "kal subah 2000 litre diesel chahiye farm pe, Okara road chak 32", 95],
      ["ai", "Ji zaroor! 2000L Diesel @ Rs 268.9/L = Rs 5,37,800. Aap ka khata available credit kaafi hai. Delivery kal subah 9 baje Chak 32, Okara Road. Confirm karein? ✅", 94],
      ["customer", "haan confirm", 92],
      ["ai", "✅ Order #1 book ho gaya: 2000L Diesel = Rs 5,37,800 (khata). Delivery se pehle driver ka number bhej diya jayega.", 91],
    ]);
    run("INSERT INTO orders (tenant_id,customer_id,product,litres,rate,amount,address,deliver_at,payment,status,source,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
      tenantId, farmer.id, "HSD", 2000, 268.9, 537800, "Chak 32, Okara Road", "Kal subah 9 baje", "khata", "pending", "whatsapp", iso(T0 - 91 * 60_000));
    conv(fleet.id, "human", [
      ["customer", "hamari 3 gaariyan aur add karni hain aur credit limit 10 lakh karni hai", 25],
      ["ai", "Ji, credit limit ke liye main aap ko hamare manager se mila raha hoon. Woh thori der mein yahin reply karenge. 🙏", 24],
    ]);
    run("UPDATE conversations SET handoff_reason='Credit limit increase request' WHERE customer_id=?", fleet.id);
    conv(customers[5].id, "ai", [
      ["customer", "kal raat Ferozepur road pump par 2000 ka petrol dalwaya tha lekin meter pe kam aaya, bohat ghalat baat hai", 50],
      ["ai", "Hamein bohat afsos hai Ayesha sahiba. Aap ki shikayat darj kar li gayi hai — Ticket C-1. Manager 2 ghante ke andar aap se rabta karenge.", 49],
    ]);
    run("INSERT INTO complaints (tenant_id,customer_id,station_id,category,message,sentiment,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
      tenantId, customers[5].id, st1, "short_measure", "Customer says Rs 2000 petrol fill showed less on meter at Ferozepur Road last night", "very_negative", "open", iso(T0 - 49 * 60_000));
    createAlert(tenantId, { station_id: st1, type: "complaint", severity: "critical", title: "Complaint #1 (short measure) — Ayesha Khan", body: "Rs 2000 petrol fill showed less on meter at Ferozepur Road last night" });

    seedWholesaleAndExpenses(tenantId, st1, st2, T0);
    seedShop(tenantId, [st1, st2], T0);
    seedCompliance(tenantId, [st1, st2], T0);
    simulateStock(tenantId, st1, st2, T0);
    seedPrepaidAndMore(tenantId, st1, st2, T0);
    seedMoney(tenantId);
    ensureAutomations(tenantId);
  });
  const tenantId = get("SELECT id FROM tenants LIMIT 1")!.id;
  scoreCustomers(tenantId);
  for (const f of detectAnomalies(tenantId)) createAlert(tenantId, { station_id: f.station_id, type: f.type, severity: f.severity, title: f.title, body: f.body, dedupe_key: f.key });
  console.log(`[seed] demo data ready: ${all("SELECT COUNT(*) n FROM sales")[0].n} sales, ${all("SELECT COUNT(*) n FROM customers")[0].n} customers`);
}


/** Wholesale clients with their own rate cards and ~2 months of ledger; ~3 months of expenses. */
/** Lubricants, filters and tuck-shop items with ~2 weeks of shop sales. */
function seedShop(tenantId: number, stations: number[], T0: number) {
  const items: [string, string, string, string, number, number, number, number][] = [
    // name, category, unit, barcode, cost, price, stock, reorder
    ["Shell Helix HX7 10W-40 (4 L)", "lubricant", "can", "8901234500011", 5200, 5950, 24, 6],
    ["PSO Deo 8000 20W-50 (4 L)", "lubricant", "can", "8901234500028", 4300, 4900, 30, 8],
    ["Total Quartz 5000 (1 L)", "lubricant", "bottle", "8901234500035", 1350, 1600, 40, 10],
    ["ZIC X5 Motorcycle Oil (0.7 L)", "lubricant", "bottle", "8901234500042", 780, 950, 60, 15],
    ["Oil filter (Corolla / Civic)", "filter", "pc", "8901234500059", 650, 900, 25, 5],
    ["Air filter (Suzuki)", "filter", "pc", "8901234500066", 550, 800, 15, 4],
    ["Coolant green (1 L)", "coolant", "bottle", "8901234500073", 420, 550, 20, 5],
    ["Mineral water (1.5 L)", "tuck", "bottle", "8901234500080", 70, 100, 120, 24],
    ["Juice pack (250 ml)", "tuck", "pc", "8901234500097", 45, 70, 90, 24],
    ["Chips (large)", "tuck", "pc", "8901234500103", 80, 120, 70, 20],
  ];
  for (const sid of stations) for (const [name, cat, unit, bc, cost, price, stock, reorder] of items) {
    const id = run(`INSERT INTO shop_items (tenant_id,station_id,barcode,name,category,unit,cost,price,stock,reorder_level,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
      tenantId, sid, bc, name, cat, unit, cost, price, stock + 20, reorder, iso(T0 - 20 * DAY)).id;
    run("INSERT INTO shop_moves (item_id,type,qty,cost,note,created_by,created_at) VALUES (?,?,?,?,?,?,?)", id, "opening", stock + 20, cost, "Opening stock", "Kamran Shah", iso(T0 - 20 * DAY));
    // a few sales a day for two weeks
    for (let d = 14; d >= 1; d--) {
      const qty = Math.round(rnd() * (cat === "tuck" ? 4 : 1));
      if (!qty) continue;
      const at = iso(T0 - d * DAY + Math.round(rnd() * 12) * 3600_000);
      const sale = run("INSERT INTO shop_sales (tenant_id,station_id,payment_method,total,cost_total,created_at) VALUES (?,?,?,?,?,?)", tenantId, sid, rnd() < 0.7 ? "cash" : "easypaisa", qty * price, qty * cost, at).id;
      run("INSERT INTO shop_sale_lines (sale_id,item_id,qty,price,cost) VALUES (?,?,?,?,?)", sale, id, qty, price, cost);
      run("UPDATE shop_items SET stock = stock - ? WHERE id=?", qty, id);
      run("INSERT INTO shop_moves (item_id,type,qty,cost,ref,created_by,created_at) VALUES (?,?,?,?,?,?,?)", id, "sale", -qty, cost, `shop:${sale}`, "Imran", at);
    }
  }
}

/** Licences with expiry dates, the standard daily/weekly checklist, duty times and past attendance. */
function seedCompliance(tenantId: number, stations: number[], T0: number) {
  const day = (offset: number) => new Date(T0 + offset * DAY + 5 * 3600_000).toISOString().slice(0, 10);
  const lic: [string, string, string, number, number | null][] = [
    ["Explosives licence (petroleum storage)", "EXP/LHR/2231", "Dept. of Explosives", 210, stations[0]],
    ["OGRA marketing licence", "OGRA-RO-8841", "OGRA", 95, null],
    ["Fire safety NOC", "FD-7712", "Rescue 1122 / Fire Dept.", 6, stations[0]],
    ["Nozzle calibration (nap-tol) certificate", "WM-55102", "Weights & Measures", 25, stations[0]],
    ["Fire extinguisher refill", null as unknown as string, "Safety contractor", 40, stations[1]],
    ["Trade licence", "TL-3390", "District council", -3, stations[1]],
  ];
  for (const [name, num, auth, days, st] of lic)
    run("INSERT INTO licences (tenant_id,station_id,name,number,authority,issued_on,expires_on,created_at) VALUES (?,?,?,?,?,?,?,?)",
      tenantId, st, name, num ?? null, auth, day(days - 365), day(days), iso(T0 - 60 * DAY));
  const items: [string, string, string, string, string | null, number | null, number | null, number][] = [
    // title, urdu, frequency, kind, unit, min_ok, max_ok, needs_photo
    ["Forecourt and canopy clean", "فورکورٹ صاف", "daily", "check", null, null, null, 1],
    ["Water in tanks (water-finding paste)", "ٹینک میں پانی", "daily", "number", "mm", 0, 10, 1],
    ["Petrol density at 15°C", "پیٹرول ڈینسٹی", "daily", "number", "kg/m³", 720, 775, 0],
    ["Diesel density at 15°C", "ڈیزل ڈینسٹی", "daily", "number", "kg/m³", 815, 870, 0],
    ["5-litre measure test (difference)", "5 لیٹر ناپ", "daily", "number", "ml", -25, 25, 0],
    ["Fire extinguishers & sand buckets in place", "آگ بجھانے کا سامان", "daily", "check", null, null, null, 0],
    ["Washrooms clean", "واش روم صاف", "daily", "check", null, null, null, 1],
    ["Air & water machine working", "ہوا اور پانی", "daily", "check", null, null, null, 0],
    ["Generator oil & fuel check", "جنریٹر", "weekly", "check", null, null, null, 0],
    ["Emergency shut-off & earthing check", "ایمرجنسی بند", "weekly", "check", null, null, null, 0],
  ];
  items.forEach(([title, urdu, freq, kind, unit, min, max, photo], i) =>
    run("INSERT INTO checklist_items (tenant_id,title,urdu,frequency,kind,unit,min_ok,max_ok,needs_photo,sort) VALUES (?,?,?,?,?,?,?,?,?,?)", tenantId, title, urdu, freq, kind, unit, min, max, photo, i));
  // duty times; Friday off for the salesman; ~40 days of check-ins and one paid leave
  for (const [email, start, off] of [["salesman", "08:00", 5], ["manager", "09:00", 0], ["wholesale", "09:00", 0]] as const) {
    const u = get("SELECT id, station_id FROM users WHERE email=?", `${email}@pumpai.pk`)!;
    run("UPDATE users SET duty_start=?, weekly_off=? WHERE id=?", start, off, u.id);
    const leaveDay = day(-12);
    run("INSERT INTO leaves (tenant_id,user_id,from_day,to_day,type,reason,status,decided_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
      tenantId, u.id, leaveDay, leaveDay, "paid", "Family wedding", "approved", "Kamran Shah", iso(T0 - 14 * DAY));
    for (let d = -40; d <= -1; d++) {
      const dd = day(d);
      if (new Date(`${dd}T12:00:00+05:00`).getUTCDay() === off || dd === leaveDay) continue;
      const late = rnd() < 0.15 ? 20 + Math.round(rnd() * 40) : Math.round(rnd() * 10);
      const inAt = Date.parse(`${dd}T${start}:00+05:00`) + late * 60_000;
      run("INSERT INTO attendance (tenant_id,user_id,station_id,day,check_in,check_out,late_minutes,source) VALUES (?,?,?,?,?,?,?,?)",
        tenantId, u.id, u.station_id ?? stations[0], dd, iso(inAt), iso(inAt + 12 * 3600_000), late, "app");
    }
  }
}

function seedWholesaleAndExpenses(tenantId: number, st1: number, st2: number, T0: number) {
  const clients = [
    { name: "Malik Petroleum Services", business: "Sub-dealer, Pattoki", phone: "923004561230", city: "Pattoki", limit: 3000000, rates: { PMG: 258.5, HSD: 264.0 }, below: { PMG: 4, HSD: 4 } as Record<string, number>, freq: 3, size: [3000, 6000] },
    { name: "Shah Transport Company", business: "Goods transport fleet", phone: "923214567890", city: "Lahore", limit: 2500000, rates: { HSD: 265.5 }, below: { HSD: 2 } as Record<string, number>, freq: 2, size: [2000, 5000] },
    { name: "Green Fields Agri Farms", business: "Tube-wells & tractors", phone: "923334445556", city: "Okara", limit: 1500000, rates: { HSD: 266.0, PMG: 259.0 }, freq: 5, size: [1500, 3000] },
  ];
  for (const [ci, c] of clients.entries()) {
    const id = run("INSERT INTO wholesale_clients (tenant_id,name,business_name,phone,city,credit_limit,opening_balance,created_at) VALUES (?,?,?,?,?,?,?,?)",
      tenantId, c.name, c.business, c.phone, c.city, c.limit, ci === 0 ? 250000 : 0, iso(T0 - 70 * DAY)).id;
    for (const [p, r] of Object.entries(c.rates)) {
      run("INSERT INTO wholesale_rates (client_id,product,rate,updated_at,updated_by) VALUES (?,?,?,?,?)", id, p, r, iso(T0 - 10 * DAY), "Haji Abdul Rehman (CEO)");
      run("INSERT INTO wholesale_rate_history (client_id,product,old_rate,new_rate,changed_by,created_at) VALUES (?,?,?,?,?,?)", id, p, r - 4, r, "Haji Abdul Rehman (CEO)", iso(T0 - 10 * DAY));
    }
    // these clients get a fixed margin below the pump price, so their rate follows every price change
    for (const [p, disc] of Object.entries(c.below ?? {}))
      run("UPDATE wholesale_rates SET mode='discount', discount=? WHERE client_id=? AND product=?", disc, id, p);
    let due = ci === 0 ? 250000 : 0;
    for (let d = 60; d >= 1; d--) {
      const t = T0 - d * DAY + 11 * 3600_000;
      if (d % c.freq === 0) {
        const p = pick(Object.keys(c.rates));
        const rate = (c.rates as Record<string, number>)[p] - (d > 10 ? 4 : 0);
        const litres = Math.round((c.size[0] + rnd() * (c.size[1] - c.size[0])) / 500) * 500;
        const station = c.city === "Okara" ? st2 : st1;
        const tank = get("SELECT id FROM tanks WHERE station_id=? AND product=? LIMIT 1", station, p)!;
        run(`INSERT INTO wholesale_txns (tenant_id,client_id,type,station_id,tank_id,product,litres,rate,amount,vehicle_no,ref,created_by,txn_date,created_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, tenantId, id, "supply", station, tank.id, p, litres, rate, Math.round(litres * rate * 100) / 100,
          `TLR-${3000 + Math.floor(rnd() * 6000)}`, `DN-${1000 + d * 3 + ci}`, "Tariq Wholesale", iso(t), iso(t));
        due += litres * rate;
      }
      if (d % 7 === ci + 1 && due > 0) {
        const pay = Math.floor((due * (ci === 1 ? 0.7 : 0.8)) / 10000) * 10000;
        if (pay > 0) {
          run(`INSERT INTO wholesale_txns (tenant_id,client_id,type,amount,method,ref,created_by,txn_date,created_at) VALUES (?,?,?,?,?,?,?,?,?)`,
            tenantId, id, "payment", pay, pick(["Bank transfer", "Cheque", "Cash", "Raast"]), `RCPT-${5000 + d * 7 + ci}`, "Tariq Wholesale", iso(t + 3 * 3600_000), iso(t + 3 * 3600_000));
          due -= pay;
        }
      }
    }
    if (ci === 2) {
      const tank = get("SELECT id FROM tanks WHERE station_id=? AND product='HSD' LIMIT 1", st2)!;
      run(`INSERT INTO wholesale_txns (tenant_id,client_id,type,station_id,tank_id,product,litres,rate,amount,note,created_by,txn_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`,
        tenantId, id, "return", st2, tank.id, "HSD", 300, 266.0, 79800, "Excess delivered — returned", "Tariq Wholesale", iso(T0 - 4 * DAY), iso(T0 - 4 * DAY));
    }
  }

  // Expenses
  for (const [name] of DEFAULT_CATEGORIES)
    run("INSERT INTO expense_categories (tenant_id,name,monthly_budget) VALUES (?,?,?)", tenantId, name,
      ({ "Electricity (bijli)": 350000, "Generator fuel": 120000, "Maintenance & repairs": 150000, "Tea & food": 30000 } as Record<string, number>)[name] ?? null);
  const monthly: [string, number, string, string][] = [
    ["Salaries & wages", 820000, "Staff payroll", "bank"], ["Rent", 250000, "Land owner", "bank"], ["Security", 90000, "Guard services", "bank"],
    ["Electricity (bijli)", 310000, "LESCO", "bank"], ["Bank charges", 6500, "Bank", "bank"], ["Taxes & fees", 45000, "Municipal / FBR", "bank"],
  ];
  for (let m = 2; m >= 0; m--) {
    const base = new Date(T0); base.setUTCDate(1); base.setUTCMonth(base.getUTCMonth() - m);
    const day = (n: number) => { const d = new Date(base); d.setUTCDate(n); return d.getTime() <= T0 ? d.toISOString().slice(0, 10) : null; };
    for (const [cat, amt, to, method] of monthly) {
      const date = day(cat === "Salaries & wages" ? 1 : 5 + Math.floor(rnd() * 5));
      if (!date) continue;
      const amount = Math.round(amt * (cat === "Electricity (bijli)" && m === 0 ? 1.32 : 0.95 + rnd() * 0.1));
      run(`INSERT INTO expenses (tenant_id,station_id,category,amount,paid_to,method,status,created_by,approved_by,expense_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
        tenantId, null, cat, amount, to, method, "approved", "Haji Abdul Rehman (CEO)", "Haji Abdul Rehman (CEO)", date, date + "T09:00:00.000Z");
    }
    for (let i = 0; i < 14; i++) {
      const date = day(1 + Math.floor(rnd() * 28));
      if (!date) continue;
      const [cat, lo, hi, to] = pick([
        ["Generator fuel", 8000, 25000, "Own stock"], ["Maintenance & repairs", 3000, 40000, "Dispenser mechanic"], ["Tea & food", 800, 3000, "Hotel"],
        ["Office & stationery", 500, 4000, "Stationery shop"], ["Tanker freight & transport", 6000, 18000, "Tanker contractor"], ["Other", 500, 6000, "Misc"],
      ] as [string, number, number, string][]);
      run(`INSERT INTO expenses (tenant_id,station_id,category,amount,paid_to,method,status,created_by,approved_by,expense_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
        tenantId, pick([st1, st2]), cat, Math.round((lo + rnd() * (hi - lo)) / 100) * 100, to, pick(["cash", "cash", "jazzcash", "easypaisa"]), "approved",
        "Kamran Shah", "Kamran Shah", date, date + "T12:00:00.000Z");
    }
  }
  const today = new Date(T0).toISOString().slice(0, 10);
  run(`INSERT INTO expenses (tenant_id,station_id,category,amount,paid_to,method,note,status,created_by,expense_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
    tenantId, st1, "Maintenance & repairs", 68000, "Gilbarco service engineer", "cash", "Dispenser 2 pulser replacement", "pending", "Kamran Shah", today, iso(T0));
}

/**
 * Replay every sale and wholesale movement per tank in time order, ordering tankers from the
 * supplier whenever stock runs low. Tank levels, deliveries, dips and the supplier ledger
 * therefore reconcile exactly, so stock reports for any period are consistent.
 */
function simulateStock(tenantId: number, st1: number, st2: number, T0: number) {
  const supplierId = run("INSERT INTO suppliers (tenant_id,name,phone,opening_balance,notes,created_at) VALUES (?,?,?,?,?,?)",
    tenantId, "PSO Mehmoodkot Depot", "924299201234", 0, "Main fuel supplier", iso(T0 - 70 * DAY)).id;
  const margin: Record<string, number> = { PMG: 7.87, HOBC: 12.5, HSD: 7.5 }; // dealer margin per litre (demo)
  const retailAt = (p: string, t: string) => get("SELECT price FROM prices WHERE tenant_id=? AND product=? AND effective_from <= ? ORDER BY effective_from DESC LIMIT 1", tenantId, p, t)?.price
    ?? get("SELECT price FROM prices WHERE tenant_id=? AND product=? ORDER BY effective_from LIMIT 1", tenantId, p)!.price;
  const start = T0 - 57 * DAY;

  for (const tank of all("SELECT t.* FROM tanks t JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=?", tenantId)) {
    type Ev = { t: string; dl: number; kind: "move" | "dip" };
    const ev: Ev[] = [
      ...all("SELECT created_at t, -litres dl FROM sales WHERE station_id=? AND product=?", tank.station_id, tank.product).map((r) => ({ ...r, kind: "move" as const })),
      ...all("SELECT created_at t, CASE type WHEN 'supply' THEN -litres ELSE litres END dl FROM wholesale_txns WHERE tank_id=? AND type IN ('supply','return')", tank.id).map((r) => ({ ...r, kind: "move" as const })),
    ];
    for (let d = 14; d >= 1; d--) {
      const day = new Date(T0 - d * DAY); day.setUTCHours(18, 0, 0, 0); // 11pm Pakistan time
      ev.push({ t: day.toISOString(), dl: 0, kind: "dip" });
    }
    ev.sort((a, b) => a.t.localeCompare(b.t));
    let level = tank.capacity_l * 0.75;
    const lowTank = tank.station_id === st2 && tank.product === "PMG"; // left low for the demo reorder alert
    for (const e of ev) {
      const tms = Date.parse(e.t);
      const skipRefill = lowTank && tms > T0 - 3 * DAY;
      if (e.kind === "move" && e.dl < 0 && (level + e.dl < tank.capacity_l * (tank.product === "HOBC" ? 0.3 : 0.28) && !skipRefill || level + e.dl < 200)) {
        const at = new Date(tms - 3600_000).toISOString();
        const invoice = Math.floor((tank.capacity_l * 0.93 - level) / 1000) * 1000;
        const shortPct = lowTank && tms > T0 - 6 * DAY ? 0.011 : rnd() * 0.002;
        const received = Math.round(invoice * (1 - shortPct));
        const rate = Math.round((retailAt(tank.product, at) - margin[tank.product]) * 100) / 100;
        const tanker = `TLR-${2000 + Math.floor(rnd() * 7000)}`;
        const del = run("INSERT INTO deliveries (tank_id,supplier,supplier_id,purchase_rate,tanker_no,invoice_l,received_l,shortage_pct,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
          tank.id, "PSO Mehmoodkot Depot", supplierId, rate, tanker, invoice, received, Math.round(shortPct * 10000) / 100, at).id;
        run(`INSERT INTO supplier_txns (tenant_id,supplier_id,type,delivery_id,product,litres,rate,amount,ref,created_by,txn_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
          tenantId, supplierId, "purchase", del, tank.product, invoice, rate, Math.round(invoice * rate * 100) / 100, tanker, "Kamran Shah", at, at);
        level += received;
      }
      if (e.kind === "dip") {
        const v = tank.station_id === st1 && tank.product === "HSD" && tms > T0 - 2 * DAY ? -1.25 : (rnd() - 0.5) * 0.4;
        const measured = Math.round(level * (1 + v / 100));
        run("INSERT INTO dip_readings (tank_id,measured_l,book_l,variance_pct,created_at) VALUES (?,?,?,?,?)", tank.id, measured, Math.round(level), Math.round(v * 100) / 100, e.t);
        level = measured;
      } else level = Math.max(0, level + e.dl);
    }
    run("UPDATE tanks SET current_l=? WHERE id=?", Math.round(level), tank.id);
  }
  // pay the depot every few days, keeping the last couple of tankers on credit
  let owed = 0;
  const purchases = all("SELECT txn_date, amount FROM supplier_txns WHERE supplier_id=? ORDER BY txn_date", supplierId);
  for (let d = 56; d >= 1; d -= 3) {
    const cutoff = iso(T0 - d * DAY);
    owed = purchases.filter((p) => p.txn_date < cutoff).reduce((a, p) => a + p.amount, 0)
      - (get("SELECT COALESCE(SUM(amount),0) s FROM supplier_txns WHERE supplier_id=? AND type='payment'", supplierId)!.s);
    const pay = Math.floor((owed * 0.85) / 100000) * 100000;
    if (pay > 0)
      run("INSERT INTO supplier_txns (tenant_id,supplier_id,type,amount,method,ref,created_by,txn_date,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        tenantId, supplierId, "payment", pay, pick(["Bank transfer", "Pay order", "Online (1LINK)"]), `PO-${7000 + d}`, "Haji Abdul Rehman (CEO)", cutoff, cutoff);
  }
}

/** Company wallet, a coupon book, monthly fixed costs, bijli bills, customer ratings, TV board offers. */
function seedPrepaidAndMore(tenantId: number, st1: number, st2: number, T0: number) {
  const fleet = get("SELECT * FROM customers WHERE tenant_id=? AND name='Al-Karam Rent a Car'", tenantId);
  if (fleet) {
    const dep = (amount: number, d: number, ref: string) => run("INSERT INTO wallet_ledger (tenant_id,customer_id,type,amount,method,ref,note,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
      tenantId, fleet.id, "deposit", amount, "raast", ref, "Advance for fuel", "Kamran Shah", iso(T0 - d * DAY));
    dep(200000, 15, "RAAST-88213");
    dep(150000, 3, "RAAST-90417");
    let bal = 350000;
    // a few of their recent cash fills were paid from the wallet
    for (const sale of all("SELECT * FROM sales WHERE customer_id=? AND payment_method <> 'khata' AND created_at >= ? ORDER BY id LIMIT 8", fleet.id, iso(T0 - 14 * DAY))) {
      run("UPDATE sales SET payment_method='wallet' WHERE id=?", sale.id);
      run("INSERT INTO wallet_ledger (tenant_id,customer_id,type,amount,note,sale_id,created_at) VALUES (?,?,?,?,?,?,?)", tenantId, fleet.id, "fill", sale.amount, `${sale.litres}L ${sale.product} @ Rs ${sale.rate}`, sale.id, sale.created_at);
      bal -= sale.amount;
    }
    run("UPDATE customers SET wallet_balance=?, wallet_low=50000 WHERE id=?", Math.round(bal * 100) / 100, fleet.id);
  }
  // a book of 20 x Rs 1,000 petrol coupons sold to a company, 6 already used
  const buyer = get("SELECT id FROM customers WHERE tenant_id=? AND name='Punjab Builders (Pvt) Ltd'", tenantId);
  for (let i = 0; i < 20; i++) {
    const code = `C${(0x5a3f00000 + i * 7919 + 1234567).toString(16).toUpperCase().slice(0, 9)}`;
    run("INSERT INTO fuel_coupons (tenant_id,code,batch,value,product,buyer,customer_id,method,status,expires_on,sold_by,sold_at,used_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
      tenantId, code, "B-DEMO-01", 1000, null, "Punjab Builders (Pvt) Ltd", buyer?.id ?? null, "cash", i < 6 ? "used" : "active", iso(T0 + 80 * DAY).slice(0, 10),
      "Kamran Shah", iso(T0 - 10 * DAY), i < 6 ? iso(T0 - (8 - i) * DAY) : null);
  }
  // rent, guard and internet booked by themselves each month
  const month = iso(T0 + 5 * 3600_000).slice(0, 7);
  for (const [cat, amount, to, day, station] of [["Rent", 150000, "Malik Property (land owner)", 1, st1], ["Security", 45000, "Shield Security Services", 5, null], ["Office & stationery", 3500, "PTCL Flash Fiber", 10, st1]] as const)
    run("INSERT INTO recurring_expenses (tenant_id,station_id,category,amount,paid_to,method,day_of_month,note,last_month,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
      tenantId, station, cat, amount, to, "bank", day, cat === "Office & stationery" ? "Internet" : null, month, "Haji Abdul Rehman (CEO)", iso(T0 - 60 * DAY));
  // last two bijli bills of the Lahore pump
  const mAgo = (n: number) => { const d = new Date(T0); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - n); return d.toISOString().slice(0, 7); };
  run("INSERT INTO utility_bills (tenant_id,station_id,kind,month,units,amount,reference,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)", tenantId, st1, "electricity", mAgo(2), 2850, 168400, "24 11215 0412300", "Kamran Shah", iso(T0 - 40 * DAY));
  run("INSERT INTO utility_bills (tenant_id,station_id,kind,month,units,amount,reference,prev_amount,prev_units,change_pct,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", tenantId, st1, "electricity", mAgo(1), 2910, 171200, "24 11215 0412300", 168400, 2850, 1.66, "Kamran Shah", iso(T0 - 10 * DAY));
  // customer ratings after fills (WhatsApp 1-5)
  const imran = get("SELECT id FROM users WHERE email='salesman@pumpai.pk'")!.id;
  const raters = all("SELECT id FROM customers WHERE tenant_id=? ORDER BY id LIMIT 30", tenantId);
  raters.forEach((c, i) => {
    const score = i === 7 ? 2 : i % 6 === 0 ? 3 : i % 3 === 0 ? 4 : 5;
    const at = iso(T0 - (i % 14) * DAY - 3 * 3600_000);
    run("INSERT INTO ratings (tenant_id,customer_id,station_id,salesman_id,score,comment,status,asked_at,rated_at) VALUES (?,?,?,?,?,?,?,?,?)",
      tenantId, c.id, i % 4 === 3 ? st2 : st1, imran, score, score <= 2 ? "Bohat der intezar karna para, salesman phone par baat kar raha tha" : null, score <= 2 ? "low" : "rated", at, at);
  });
  setSetting(tenantId, "board_offers", "Free air & water check with every fill\n1 point on every Rs 100 — pay with points!");
}

/** A second depot to compare with, freight on each tanker, tax numbers. */
function seedMoney(tenantId: number) {
  const first = get("SELECT * FROM suppliers WHERE tenant_id=? ORDER BY id LIMIT 1", tenantId);
  if (!first) return;
  const second = run("INSERT INTO suppliers (tenant_id,name,phone,opening_balance,notes,created_at) VALUES (?,?,?,?,?,?)",
    tenantId, "Shell Machike Depot", "923004445566", 0, "Second depot, used when PSO is short", iso(Date.now() - 70 * DAY)).id;
  // every third tanker came from the second depot: a little cheaper per litre, more freight and more shortage
  const dels = all("SELECT d.* FROM deliveries d JOIN tanks t ON t.id=d.tank_id JOIN stations s ON s.id=t.station_id WHERE s.tenant_id=? ORDER BY d.id", tenantId);
  dels.forEach((d, i) => {
    const alt = i % 3 === 2;
    const freight = Math.round(d.received_l * (alt ? 2.4 : 1.6));
    run("UPDATE deliveries SET freight=? WHERE id=?", freight, d.id);
    if (!alt) return;
    const rate = d.purchase_rate ? Math.round((d.purchase_rate - 0.6) * 100) / 100 : null;
    const received = Math.round(d.invoice_l * 0.9955);
    run("UPDATE tanks SET current_l = current_l - ? WHERE id=?", d.received_l - received, d.tank_id);
    run("UPDATE deliveries SET supplier_id=?, supplier=?, purchase_rate=?, received_l=?, shortage_pct=? WHERE id=?", second, "Shell Machike Depot", rate, received, Math.round(((d.invoice_l - received) / d.invoice_l) * 10000) / 100, d.id);
    if (rate) run("UPDATE supplier_txns SET supplier_id=?, rate=?, amount=? WHERE delivery_id=?", second, rate, Math.round(d.invoice_l * rate * 100) / 100, d.id);
  });
  // pay the second depot what it is owed except the last tanker; move the same amount off the first depot's payments
  const owed2 = get("SELECT COALESCE(SUM(amount),0) v FROM supplier_txns WHERE supplier_id=? AND type='purchase'", second)!.v;
  const last = get("SELECT amount FROM supplier_txns WHERE supplier_id=? AND type='purchase' ORDER BY txn_date DESC LIMIT 1", second)?.amount ?? 0;
  let toMove = owed2 - last;
  for (const p of all("SELECT * FROM supplier_txns WHERE supplier_id=? AND type='payment' ORDER BY txn_date", first.id)) {
    if (toMove <= 0) break;
    if (p.amount <= toMove) { run("UPDATE supplier_txns SET supplier_id=? WHERE id=?", second, p.id); toMove -= p.amount; }
  }
  setSetting(tenantId, "ntn", "4217651-3");
  setSetting(tenantId, "strn", "3277876154321");
}
