/** Demo data: one business, two stations, 8 weeks of realistic sales, customers, khata and WhatsApp chats. */
import bcrypt from "bcryptjs";
import { db, migrate, run, all, get, tx } from "./db.js";
import { scoreCustomers, detectAnomalies } from "./ai/analytics.js";
import { createAlert } from "./services.js";
import { ensureAutomations } from "./automation/scheduler.js";

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
    run("INSERT INTO users (tenant_id,name,email,password_hash,role,station_id) VALUES (?,?,?,?,?,1)", tenantId, "Imran", "salesman@pumpai.pk", hash, "salesman");

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
      const limit = type === "fleet" ? pick([300000, 500000, 800000]) : type === "farmer" ? pick([200000, 400000]) : type === "business" ? 250000 : i % 5 === 0 ? 30000 : 0;
      const id = run("INSERT INTO customers (tenant_id,name,phone,type,city,credit_limit,opt_in,loyalty_points,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        tenantId, name, phone, type, i % 3 === 0 ? "Okara" : "Lahore", limit, i % 11 === 10 ? 0 : 1, 0, iso(T0 - 120 * DAY)).id;
      if (type === "fleet") for (let v = 0; v < 3; v++) run("INSERT INTO vehicles (customer_id,plate_no,fuel,daily_limit_l) VALUES (?,?,?,?)", id, `${pick(["LES", "LEA", "LEB", "LZT"])}-${1000 + Math.floor(rnd() * 8999)}`, pick(["HSD", "PMG"]), 120);
      else if (type === "retail") run("INSERT INTO vehicles (customer_id,plate_no,fuel) VALUES (?,?,?)", id, `${pick(["LEC", "LEH", "LXR", "OKA"])}-${100 + Math.floor(rnd() * 899)}`, "PMG");
      // some regulars churn: last visit set by sales below; mark a few as "stopped coming" 25-40 days ago
      return { id, type, limit, churnFrom: i % 7 === 3 ? 20 + Math.floor(rnd() * 20) : 0, freq: type === "retail" ? 0.25 + rnd() * 0.3 : 0.6 };
    });

    // 8 weeks of sales
    const base: Record<string, number> = { [`${st1}PMG`]: 5200, [`${st1}HOBC`]: 650, [`${st1}HSD`]: 3800, [`${st2}PMG`]: 2600, [`${st2}HSD`]: 6400 };
    const weekday = [1.08, 0.94, 0.96, 0.98, 1.12, 1.15, 0.92]; // Sun..Sat (Friday/Saturday peaks)
    const insertSale = db.prepare(`INSERT INTO sales (station_id,shift_id,customer_id,nozzle_id,product,litres,rate,amount,payment_method,vehicle_no,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)`);
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
          const usable = cust && !(cust.churnFrom && d < cust.churnFrom) && (cust.type !== "retail" || tk.p !== "HSD") ? cust : null;
          const litres = Math.round((tk.p === "HSD" ? (usable && usable.type !== "retail" ? 60 + rnd() * 240 : 15 + rnd() * 60) : 3 + rnd() * 30) * 100) / 100;
          const rate = priceAt(tk.p, t);
          const amount = Math.round(litres * rate * 100) / 100;
          let method = pick(["cash", "cash", "cash", "cash", "jazzcash", "easypaisa", "card", "raast"]);
          if (usable && usable.limit > 0 && rnd() < 0.75) method = "khata";
          insertSale.run(tk.sid, null, usable?.id ?? null, pick(tk.noz), tk.p, litres, rate, amount, method, null, iso(t));
          if (usable) {
            run("UPDATE customers SET last_visit_at = MAX(COALESCE(last_visit_at,''), ?), loyalty_points = loyalty_points + ? WHERE id=?", iso(t), Math.floor(amount / 100), usable.id);
            if (method === "khata") {
              run("INSERT INTO khata_ledger (customer_id,type,amount,ref,note,created_at) VALUES (?,?,?,?,?,?)", usable.id, "debit", amount, "SALE", `${litres}L ${tk.p}`, iso(t));
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
      // deliveries & dips
      if (d % 4 === 1) for (const tk of tanks) {
        const inv = tk.p === "HOBC" ? 5000 : pick([10000, 15000, 20000]);
        const short = tk.sid === st2 && tk.p === "PMG" && d === 1 ? 0.011 : rnd() * 0.002;
        run("INSERT INTO deliveries (tank_id,supplier,tanker_no,invoice_l,received_l,shortage_pct,created_at) VALUES (?,?,?,?,?,?,?)",
          tk.id, "PSO Mehmoodkot Depot", `TLR-${2000 + Math.floor(rnd() * 7000)}`, inv, Math.round(inv * (1 - short)), Math.round(short * 10000) / 100, iso(ds + 10 * 3600_000));
      }
      if (d <= 14) for (const tk of tanks) {
        const book = 5000 + rnd() * 15000;
        const v = tk.sid === st1 && tk.p === "HSD" && d === 1 ? -1.25 : (rnd() - 0.5) * 0.4;
        run("INSERT INTO dip_readings (tank_id,measured_l,book_l,variance_pct,created_at) VALUES (?,?,?,?,?)", tk.id, Math.round(book * (1 + v / 100)), Math.round(book), Math.round(v * 100) / 100, iso(ds + 23 * 3600_000));
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

    ensureAutomations(tenantId);
  });
  const tenantId = get("SELECT id FROM tenants LIMIT 1")!.id;
  scoreCustomers(tenantId);
  for (const f of detectAnomalies(tenantId)) createAlert(tenantId, { station_id: f.station_id, type: f.type, severity: f.severity, title: f.title, body: f.body, dedupe_key: f.key });
  console.log(`[seed] demo data ready: ${all("SELECT COUNT(*) n FROM sales")[0].n} sales, ${all("SELECT COUNT(*) n FROM customers")[0].n} customers`);
}

