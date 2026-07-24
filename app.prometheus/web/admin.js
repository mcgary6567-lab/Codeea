// Prometheus Web — admin console
let TOKEN = localStorage.getItem("prometheus_token") || "";
const $ = id => document.getElementById(id);
let ALL_USERS = [];          // raw list from server
let STATS = null;            // platform stats
let SORT = { key: "id", dir: 1 };
let VIEW_ROWS = [], PAGE = 1; const PER_PAGE = 5;   // customers pagination (5 per page)
let autoTimer = null;

const esc = x => String(x ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtMoney = n => (n == null || isNaN(n)) ? "—" : (n < 0 ? "-$" : "$") + Math.abs(Number(n)).toLocaleString(undefined, { maximumFractionDigits: 2 });
const fmtNum = n => (n == null || isNaN(n)) ? "—" : Number(n).toLocaleString();
const dshort = s => s ? new Date(s * 1000).toLocaleDateString() : "—";
const ago = s => {
  if (!s) return "never";
  const d = Date.now() / 1000 - s;
  if (d < 60) return "just now";
  if (d < 3600) return Math.floor(d / 60) + "m ago";
  if (d < 86400) return Math.floor(d / 3600) + "h ago";
  return Math.floor(d / 86400) + "d ago";
};

async function api(path, method = "GET", bodyObj) {
  const opt = { method, headers: {} };
  if (TOKEN) opt.headers["Authorization"] = "Bearer " + TOKEN;
  if (bodyObj) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(bodyObj); }
  const r = await fetch(path, opt);
  const t = await r.text(); let d; try { d = JSON.parse(t); } catch { d = { detail: t }; }
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
  return d;
}
function logout() { localStorage.removeItem("prometheus_token"); TOKEN = ""; if (autoTimer) clearInterval(autoTimer); location.reload(); }

// ---- auth ----
async function adminLogin() {
  $("g-err").textContent = "";
  try {
    const d = await api("/api/login", "POST", { email: $("g-email").value.trim(), password: $("g-pass").value });
    TOKEN = d.token; localStorage.setItem("prometheus_token", TOKEN); boot();
  } catch (e) { $("g-err").textContent = e.message; }
}
function showGate() { $("gate").classList.remove("hidden"); $("panel").classList.add("hidden"); }

function tokenEmail() { try { return JSON.parse(atob(TOKEN.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).email || ""; } catch (e) { return ""; } }
async function boot() {
  try {
    await reloadAll();
    const me = tokenEmail(); if (me && $("ad-me")) $("ad-me").textContent = me;
    $("gate").classList.add("hidden"); $("panel").classList.remove("hidden");
  } catch (e) {
    if (String(e.message).toLowerCase().includes("admin")) $("ad-me").textContent = "not an admin account";
    showGate();
  }
}

async function reloadAll() {
  const [uRes, sRes] = await Promise.all([api("/api/admin/users"), api("/api/admin/stats")]);
  ALL_USERS = uRes.users || [];
  STATS = sRes;
  renderStats();
  applyFilters();
  loadBroadcasts();
  loadAudit();
  loadStrategy();
  loadRequests();
  loadSocialProofState();
}
async function loadSocialProofState() {
  try {
    const d = await api("/api/admin/social_proof");
    if ($("sp-on")) $("sp-on").checked = !!d.enabled;
    if ($("sp-state")) $("sp-state").textContent = d.enabled
      ? `On · ${d.count} recent sale${d.count === 1 ? "" : "s"} in rotation.`
      : "Off — customers won't see the sale popup.";
  } catch (e) { }
}
async function toggleSocialProof() {
  const on = !!($("sp-on") && $("sp-on").checked);
  try { await api("/api/admin/social_proof", "POST", { enabled: on }); notify(on ? "Sale popup ON ✓" : "Sale popup off", on ? "ok" : "warn"); loadSocialProofState(); loadAudit(); }
  catch (e) { notify(e.message, "error"); loadSocialProofState(); }
}

// ---- KPI + charts ----
function kpi(label, value, sub, cls) {
  return `<div class="kpi"><div class="kpi-l">${label}</div><div class="kpi-v ${cls || ""}">${value}</div><div class="kpi-s">${sub || ""}</div></div>`;
}
function renderStats() {
  if (!STATS) return;
  const u = STATS.users, g = STATS.growth, rev = STATS.revenue, tr = STATS.trading, live = STATS.live || {};
  $("kpi-biz").innerHTML =
    kpi("Customers", fmtNum(u.customers), `+${g.new_today} today · +${g.new_7d} this week`) +
    kpi("Licensed", fmtNum(u.licensed), `~${fmtMoney(rev.est_mrr)} est. MRR`, "pos") +
    kpi("Active trials", fmtNum(u.trial), `${g.new_30d} new in 30d`, "warn") +
    kpi("Trial → Paid", g.conversion + "%", "conversion rate") +
    kpi("Expired / Susp.", fmtNum(u.expired + u.suspended), `${u.suspended} suspended`, (u.expired + u.suspended) ? "neg" : "") +
    kpi("Online now", fmtNum(live.online || 0), `${live.trading || 0} actively trading`, (live.online ? "pos" : ""));
  $("kpi-trade").innerHTML =
    kpi("Platform PnL (live)", fmtMoney(tr.live_pnl), "realized, all customers", tr.live_pnl >= 0 ? "pos" : "neg") +
    kpi("Live trades", fmtNum(tr.live_trades), `${tr.open_trades} open now`) +
    kpi("Paper trades", fmtNum(tr.paper_trades), fmtMoney(tr.paper_pnl) + " simulated", "warn") +
    kpi("Win rate (live)", tr.live_win_rate + "%", "closed live trades") +
    kpi("With API keys", fmtNum(u.with_keys), "exchange connected") +
    kpi("Total accounts", fmtNum(u.total), `${u.admins} admin`);
  const rr = STATS.revenue_real || { total: 0, last_30d: 0, last_7d: 0, sales: 0 };
  $("kpi-rev").innerHTML = rr.sales > 0
    ? kpi("Revenue (all-time)", fmtMoney(rr.total), `${rr.sales} sales logged`, "pos") +
      kpi("Revenue (30d)", fmtMoney(rr.last_30d), "last 30 days", "pos") +
      kpi("Revenue (7d)", fmtMoney(rr.last_7d), "last 7 days") +
      kpi("Avg sale", fmtMoney(rr.sales ? rr.total / rr.sales : 0), "per licence")
    : kpi("Revenue", "—", "Log a $ amount when granting a licence to track real revenue");

  // online chip
  const oc = $("ad-online");
  oc.classList.toggle("hidden", !(live.online));
  oc.textContent = `● ${live.online || 0} online`;

  drawSignups(g.signup_series || []);
  const tot30 = (g.signup_series || []).reduce((a, b) => a + b, 0);
  $("ad-signups-cap").textContent = `${tot30} new sign-up${tot30 === 1 ? "" : "s"} in the last 30 days.`;

  // mix bar
  const parts = [
    { k: "Licensed", n: u.licensed, c: "var(--green)" },
    { k: "Trial", n: u.trial, c: "var(--amber)" },
    { k: "Expired", n: u.expired, c: "var(--mut)" },
    { k: "Suspended", n: u.suspended, c: "var(--red)" },
  ];
  const sum = parts.reduce((a, p) => a + p.n, 0) || 1;
  $("ad-mix").innerHTML =
    `<div class="mixbar">${parts.map(p => p.n ? `<span style="width:${100 * p.n / sum}%;background:${p.c}" title="${p.k}: ${p.n}"></span>` : "").join("")}</div>` +
    `<div class="mixlegend">${parts.map(p => `<span><i style="background:${p.c}"></i>${p.k} <b>${p.n}</b></span>`).join("")}</div>`;
}

function drawSignups(series) {
  const cv = $("ad-signups"); if (!cv) return;
  const ratio = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 400, h = cv.clientHeight || 120;
  if (w < 20) return;
  cv.width = w * ratio; cv.height = h * ratio;
  const g = cv.getContext("2d"); g.scale(ratio, ratio);
  g.clearRect(0, 0, w, h);
  const max = Math.max(1, ...series);
  const n = series.length, gap = 3, bw = (w - gap * (n - 1)) / n;
  const grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "#f97316"); grad.addColorStop(1, "#7c3a10");
  series.forEach((v, i) => {
    const bh = Math.max(v ? 3 : 0, (h - 18) * v / max);
    const x = i * (bw + gap), y = h - 16 - bh;
    g.fillStyle = v ? grad : "rgba(255,255,255,.05)";
    g.fillRect(x, y, bw, bh || 2);
  });
  g.fillStyle = "#8a97ab"; g.font = "10px -apple-system,sans-serif";
  g.fillText("30d ago", 0, h - 3); g.textAlign = "right"; g.fillText("today", w, h - 3);
}
window.addEventListener("resize", () => STATS && drawSignups(STATS.growth.signup_series || []));

// ---- users table ----
function statusOf(u) { return (u.entitlement || {}).status || "—"; }
function daysOf(u) { const e = u.entitlement || {}; return e.days_left == null ? -1 : e.days_left; }

function applyFilters() {
  const q = ($("ad-search").value || "").trim().toLowerCase();
  const f = $("ad-filter").value;
  let rows = ALL_USERS.filter(u => {
    if (q) {
      const hay = (u.email + " " + (u.first_name || "") + " " + (u.last_name || "")).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (f === "admin") return u.is_admin;
    if (f === "online") return u.live && u.live.connected;
    if (f) return statusOf(u) === f;
    return true;
  });
  const k = SORT.key, dir = SORT.dir;
  rows.sort((a, b) => {
    let va, vb;
    if (k === "status") { va = statusOf(a); vb = statusOf(b); }
    else if (k === "days") { va = daysOf(a); vb = daysOf(b); }
    else if (k === "email") { va = a.email; vb = b.email; }
    else { va = a[k] || 0; vb = b[k] || 0; }
    return (va > vb ? 1 : va < vb ? -1 : 0) * dir;
  });
  VIEW_ROWS = rows; PAGE = 1; renderRows();
}
function sortBy(k) { SORT = { key: k, dir: SORT.key === k ? -SORT.dir : 1 }; applyFilters(); }
function gotoPage(p) { PAGE = p; renderRows(); }

const PILL = { trial: "warn", licensed: "safe", admin: "safe", expired: "danger", suspended: "danger" };
function renderRows() {
  const rows = VIEW_ROWS;
  const pages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
  if (PAGE > pages) PAGE = pages; if (PAGE < 1) PAGE = 1;
  const start = (PAGE - 1) * PER_PAGE;
  const pageRows = rows.slice(start, start + PER_PAGE);
  $("ad-count").textContent = rows.length;
  $("ad-empty").style.display = rows.length ? "none" : "block";
  const pg = $("ad-pager");
  if (pg) {
    if (rows.length <= PER_PAGE) pg.innerHTML = "";
    else pg.innerHTML = `<span class="k">${start + 1}–${Math.min(start + PER_PAGE, rows.length)} of ${rows.length}</span>`
      + `<button class="btn ghost sm" ${PAGE <= 1 ? "disabled" : ""} onclick="gotoPage(${PAGE - 1})">‹ Prev</button>`
      + `<span class="k">Page ${PAGE} / ${pages}</span>`
      + `<button class="btn ghost sm" ${PAGE >= pages ? "disabled" : ""} onclick="gotoPage(${PAGE + 1})">Next ›</button>`;
  }
  $("u-body").innerHTML = pageRows.map(u => {
    const st = statusOf(u), e = u.entitlement || {};
    const nm = [u.first_name, u.last_name].filter(Boolean).join(" ");
    const lv = u.live || {};
    const dot = lv.connected ? `<span class="dot on" title="connected${lv.strategy_on ? " · trading" : ""}"></span>` : `<span class="dot" title="offline"></span>`;
    return `<tr onclick="openDrawer(${u.id})" style="cursor:pointer">
      <td>${u.id}</td>
      <td><div>${nm ? esc(nm) : "<span class='k'>—</span>"}</div><div class="k mono">${esc(u.email)}</div></td>
      <td><span class="chip ${PILL[st] || ""}">${st}</span>${u.is_admin ? ' <span class="chip safe">admin</span>' : ""}</td>
      <td class="mono">${e.lifetime ? "♾ <span class='pos'>Lifetime</span>" : (e.days_left == null ? "—" : e.days_left + "d")}</td>
      <td>${u.has_keys ? "✓" : "—"}</td>
      <td>${dot}${lv.strategy_on ? ' <span class="pos" title="strategy running">▶</span>' : ""}</td>
      <td class="k">${ago(u.last_seen)}</td>
      <td class="k">${dshort(u.created)}</td>
      <td onclick="event.stopPropagation()">${actionBtns(u)}</td>
    </tr>`;
  }).join("");
}
function actionBtns(u) {
  return `${u.active
    ? `<button class="btn ghost sm" onclick="act(${u.id},'suspend')">⛔ Suspend</button>`
    : `<button class="btn green sm" onclick="act(${u.id},'activate')">✅ Activate</button>`}
    <button class="btn sm" onclick="grantLifetime(${u.id})">🎟️ Licence</button>
    <button class="btn ghost sm" onclick="openDrawer(${u.id})">🔍 Details</button>`;
}

// ---- detail drawer ----
async function openDrawer(uid) {
  $("drawer").classList.remove("hidden"); $("drawer-scrim").classList.remove("hidden");
  $("dw-body").innerHTML = `<div class="hint">Loading…</div>`;
  try {
    const d = await api("/api/admin/user/" + uid);
    renderDrawer(d);
  } catch (e) { $("dw-body").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
function closeDrawer() { $("drawer").classList.add("hidden"); $("drawer-scrim").classList.add("hidden"); }

function renderDrawer(d) {
  const nm = [d.first_name, d.last_name].filter(Boolean).join(" ") || "(no name)";
  $("dw-name").textContent = nm;
  $("dw-email").textContent = d.email;
  const e = d.entitlement || {}, an = d.analytics || {}, pm = d.pnl_modes || {}, live = d.live;
  const lv = pm.live || {}, pp = pm.paper || {};
  const st = e.status;
  const rows = (d.recent_trades || []).slice(0, 12);
  $("dw-body").innerHTML = `
    <div class="dw-badges">
      <span class="chip ${PILL[st] || ""}">${st}</span>
      ${d.is_admin ? '<span class="chip safe">admin</span>' : ""}
      ${d.has_keys ? '<span class="chip">🔑 keys</span>' : '<span class="chip">no keys</span>'}
      ${d.totp_enabled ? '<span class="chip safe">🔐 2FA</span>' : ""}
      ${live && live.connected ? '<span class="chip safe">● connected</span>' : '<span class="chip">offline</span>'}
      ${live && live.paper_mode ? '<span class="chip warn">paper</span>' : ""}
      ${d.allow_custom ? '<span class="chip safe">custom strategy</span>' : '<span class="chip">managed strategy</span>'}
      ${d.custom_requested && !d.allow_custom ? '<span class="chip warn">⏳ requested custom</span>' : ""}
    </div>
    <div class="dw-grid">
      <div class="dw-stat"><span>Access</span><b>${st}${e.lifetime ? " · ♾ lifetime" : (e.days_left != null ? " · " + e.days_left + "d" : "")}</b></div>
      <div class="dw-stat"><span>Plan</span><b>${esc(d.plan)}</b></div>
      <div class="dw-stat"><span>Joined</span><b>${dshort(d.created)}</b></div>
      <div class="dw-stat"><span>Last seen</span><b>${ago(d.last_seen)}</b></div>
    </div>
    <h4 class="dw-h">Performance</h4>
    <div class="dw-grid">
      <div class="dw-stat"><span>Realized PnL</span><b class="${(an.realized_pnl || 0) >= 0 ? "pos" : "neg"}">${fmtMoney(an.realized_pnl)}</b></div>
      <div class="dw-stat"><span>Win rate</span><b>${an.win_rate ?? 0}%</b></div>
      <div class="dw-stat"><span>Trades</span><b>${an.trades ?? 0}</b></div>
      <div class="dw-stat"><span>Best / Worst</span><b class="mono">${fmtMoney(an.best)} / ${fmtMoney(an.worst)}</b></div>
      <div class="dw-stat"><span>Live PnL</span><b class="${(lv.pnl || 0) >= 0 ? "pos" : "neg"}">${fmtMoney(lv.pnl)}</b><em>${lv.trades || 0} trades</em></div>
      <div class="dw-stat"><span>Paper PnL</span><b class="warn">${fmtMoney(pp.pnl)}</b><em>${pp.trades || 0} trades</em></div>
    </div>
    ${live && live.positions && live.positions.length ? `
      <h4 class="dw-h">Open positions (live)</h4>
      <div class="tablewrap"><table><thead><tr><th>Pair</th><th>Side</th><th>Size</th><th>Entry</th><th>PnL</th></tr></thead>
      <tbody>${live.positions.map(p => `<tr><td>${esc(p.pair)}</td><td>${esc(p.side)}</td><td class="mono">${p.size}</td><td class="mono">${p.entry}</td><td class="mono ${p.pnl >= 0 ? "pos" : "neg"}">${fmtMoney(p.pnl)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    <h4 class="dw-h">Recent activity</h4>
    <div class="tablewrap"><table><thead><tr><th>When</th><th>Symbol</th><th>Kind</th><th>PnL</th><th>Mode</th></tr></thead>
    <tbody>${rows.length ? rows.map(t => `<tr><td class="k">${ago(t.ts)}</td><td>${esc(t.symbol)}</td><td>${esc(t.kind)}</td><td class="mono ${(t.pnl || 0) >= 0 ? "pos" : "neg"}">${t.kind === "close" ? fmtMoney(t.pnl) : "—"}</td><td class="k">${esc(t.mode)}</td></tr>`).join("") : `<tr><td colspan="5" class="k">No activity yet.</td></tr>`}</tbody></table></div>
    <h4 class="dw-h">Internal note</h4>
    <textarea id="dw-note" rows="2" style="width:100%;background:var(--chip);border:1px solid var(--line);color:var(--txt);border-radius:9px;padding:8px 10px;font-family:inherit;font-size:12.5px">${esc(d.admin_note || "")}</textarea>
    <button class="btn ghost sm" style="margin-top:6px" onclick="saveNote(${d.id})">📝 Save note</button>
    <h4 class="dw-h">Actions</h4>
    <div class="dw-actions">
      ${d.active ? `<button class="btn ghost sm" onclick="act(${d.id},'suspend',1)">⛔ Suspend</button>` : `<button class="btn green sm" onclick="act(${d.id},'activate',1)">✅ Activate</button>`}
      <button class="btn sm" onclick="grantLifetime(${d.id},1)">♾ Lifetime licence</button>
      <button class="btn ghost sm" onclick="grantDays(${d.id})">🎟️ Grant N days…</button>
      <button class="btn ghost sm" onclick="extendTrial(${d.id})">⏳ Extend trial</button>
      <button class="btn ghost sm" onclick="act(${d.id},'revoke',1)">🚫 Revoke licence</button>
      <button class="btn ghost sm" onclick="resetLink(${d.id})">🔗 Password-reset link</button>
      <button class="btn ghost sm" onclick="editUser(${d.id})">✏️ Edit details</button>
      <button class="btn ghost sm" onclick="setExpiry(${d.id})">📅 Set expiry date</button>
      ${d.allow_custom ? `<button class="btn ghost sm" onclick="act(${d.id},'lock_strategy',1)">🔒 Lock to managed strategy</button>` : `<button class="btn ghost sm" onclick="act(${d.id},'unlock_strategy',1)">🔓 Allow custom strategy</button>`}
      ${d.is_admin ? `<button class="btn ghost sm" onclick="act(${d.id},'remove_admin',1)">🙅 Remove admin</button>` : `<button class="btn ghost sm" onclick="act(${d.id},'make_admin',1)">🛡️ Make admin</button>`}
      <button class="btn red sm" onclick="deleteUser(${d.id})">🗑 Delete</button>
    </div>
    <div id="dw-msg" class="hint"></div>`;
}

// ---- actions ----
const ACTMSG = { suspend: "Customer suspended", activate: "Customer activated", revoke: "Licence revoked", make_admin: "Now an admin", remove_admin: "Admin removed" };
async function act(uid, action, fromDrawer) {
  if (action === "suspend" && !confirm("Suspend this customer? Their trading stops immediately.")) return;
  if (action === "remove_admin" && !confirm("Remove admin rights from this account?")) return;
  if (action === "revoke" && !confirm("Revoke this customer's licence? They lose live-trading access immediately.")) return;
  if (action === "make_admin" && !confirm("Make this account a full admin? They will get complete access to the admin console.")) return;
  try {
    await api("/api/admin/action", "POST", { user_id: uid, action });
    notify(ACTMSG[action] || "Done", action === "suspend" || action === "revoke" ? "warn" : "ok");
    await reloadAll();
    if (fromDrawer) openDrawer(uid);
  } catch (e) { notify(e.message, "error"); }
}
async function grantLifetime(uid, fromDrawer) {
  if (!confirm("Grant a LIFETIME (never-expiring) licence to this customer?")) return;
  const amt = prompt("Sale amount in $ (blank = don't log revenue):", "");
  try {
    const body = { user_id: uid, action: "grant", lifetime: true };
    if (amt && parseFloat(amt) > 0) { body.amount = parseFloat(amt); body.method = "manual"; }
    await api("/api/admin/action", "POST", body);
    notify("♾ Lifetime licence granted ✓", "ok"); await reloadAll(); if (fromDrawer) openDrawer(uid);
  } catch (e) { notify(e.message, "error"); }
}
// ---- new admin ops: notes, edit, expiry, delete, broadcasts, audit ----
async function saveNote(uid) { try { await api("/api/admin/action", "POST", { user_id: uid, action: "note", note: $("dw-note").value }); notify("Note saved ✓", "ok"); } catch (e) { notify(e.message, "error"); } }
async function editUser(uid) {
  const u = ALL_USERS.find(x => x.id === uid) || {};
  const email = prompt("Email:", u.email || ""); if (email === null) return;
  const first = prompt("First name:", u.first_name || ""); if (first === null) return;
  const last = prompt("Last name:", u.last_name || ""); if (last === null) return;
  try { await api("/api/admin/action", "POST", { user_id: uid, action: "edit", email, first_name: first, last_name: last }); notify("Customer updated ✓", "ok"); await reloadAll(); openDrawer(uid); }
  catch (e) { notify(e.message, "error"); }
}
async function setExpiry(uid) {
  const s = prompt("Licence expiry date (YYYY-MM-DD):"); if (!s) return;
  const t = Date.parse(s + "T23:59:59Z"); if (isNaN(t)) { notify("Invalid date — use YYYY-MM-DD", "error"); return; }
  try { await api("/api/admin/action", "POST", { user_id: uid, action: "set_expiry", ts: t / 1000 }); notify("Expiry date set ✓", "ok"); await reloadAll(); openDrawer(uid); }
  catch (e) { notify(e.message, "error"); }
}
async function deleteUser(uid) {
  if (!confirm("Permanently DELETE this customer and ALL their data (trades, keys, logins)? This cannot be undone.")) return;
  try { await api("/api/admin/action", "POST", { user_id: uid, action: "delete" }); notify("Customer deleted", "warn"); closeDrawer(); await reloadAll(); }
  catch (e) { notify(e.message, "error"); }
}
async function sendBroadcast() {
  const msg = $("bc-msg").value.trim(); if (!msg) { notify("Enter a message first", "warn"); return; }
  try { await api("/api/admin/broadcast", "POST", { message: msg, notify: $("bc-notify").checked }); notify("Announcement posted ✓", "ok"); $("bc-msg").value = ""; loadBroadcasts(); loadAudit(); }
  catch (e) { notify(e.message, "error"); }
}
async function broadcastOff() { try { await api("/api/admin/broadcast_off", "POST"); notify("Announcement cleared", "warn"); loadBroadcasts(); loadAudit(); } catch (e) { notify(e.message, "error"); } }
async function loadBroadcasts() {
  try { const d = await api("/api/admin/broadcasts"); const active = (d.broadcasts || []).find(b => b.active);
    $("bc-list").innerHTML = active ? `<b>Active:</b> ${esc(active.message)} <span class="k">(${new Date(active.ts * 1000).toLocaleString()})</span>` : "No active announcement."; } catch (e) { }
}
async function loadAudit() {
  try { const d = await api("/api/admin/audit"); const rows = d.audit || [];
    $("audit-rows").innerHTML = rows.length ? rows.map(r => `<tr><td class="k">${new Date(r.ts * 1000).toLocaleString()}</td><td class="k">${esc(r.admin_email)}</td><td>${esc(r.action)}</td><td class="k">${esc((r.detail || "") + (r.target_id ? " #" + r.target_id : ""))}</td></tr>`).join("") : `<tr><td colspan="4" class="k">No admin actions yet.</td></tr>`; } catch (e) { }
}
async function grantDays(uid) {
  const days = prompt("Grant a time-limited licence for how many days?\n(Leave blank to cancel — use “+Licence” for a lifetime licence.)", "30");
  if (!days) return;
  const nDays = parseFloat(days);
  if (isNaN(nDays) || nDays <= 0) { notify("Enter a valid number of days", "error"); return; }
  try {
    const body = { user_id: uid, action: "grant", days: nDays };
    const amt = prompt("Sale amount in $ (blank = don't log revenue):", "");
    if (amt && parseFloat(amt) > 0) { body.amount = parseFloat(amt); body.method = "manual"; }
    await api("/api/admin/action", "POST", body);
    notify(`Licence granted (${days} days) ✓`, "ok"); await reloadAll(); openDrawer(uid);
  } catch (e) { notify(e.message, "error"); }
}
async function extendTrial(uid) {
  const days = prompt("Extend the free trial by how many days?", "7");
  if (!days) return;
  const nDays = parseFloat(days);
  if (isNaN(nDays) || nDays <= 0) { notify("Enter a valid number of days", "error"); return; }
  try {
    await api("/api/admin/action", "POST", { user_id: uid, action: "extend_trial", days: nDays });
    notify(`Trial extended (${days} days) ✓`, "ok"); await reloadAll(); openDrawer(uid);
  } catch (e) { notify(e.message, "error"); }
}
async function resetLink(uid) {
  try {
    const d = await api("/api/admin/action", "POST", { user_id: uid, action: "reset_link" });
    const url = location.origin + (d.reset_url.startsWith("http") ? new URL(d.reset_url).pathname + new URL(d.reset_url).search : d.reset_url);
    const box = $("dw-msg");
    if (box) box.innerHTML = `One-time reset link (valid ${d.expires_minutes || 60} min):<br><input class="mono" style="margin-top:6px" readonly value="${esc(url)}" onclick="this.select()"/>`;
    try { await navigator.clipboard.writeText(url); notify("Reset link copied to clipboard", "ok"); }
    catch { notify("Reset link generated", "ok"); }
  } catch (e) { notify(e.message, "error"); }
}

// ---- CSV ----
function exportCsv() {
  const head = ["id", "email", "first_name", "last_name", "status", "days_left", "plan", "has_keys", "is_admin", "created", "last_seen"];
  const lines = [head.join(",")];
  ALL_USERS.forEach(u => {
    const e = u.entitlement || {};
    const row = [u.id, u.email, u.first_name || "", u.last_name || "", e.status || "", e.days_left ?? "", u.plan, u.has_keys ? 1 : 0, u.is_admin ? 1 : 0,
      u.created ? new Date(u.created * 1000).toISOString() : "", u.last_seen ? new Date(u.last_seen * 1000).toISOString() : ""];
    lines.push(row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "prometheus-customers.csv"; a.click();
  URL.revokeObjectURL(a.href);
}

// ---- auto-refresh ----
function toggleAuto() {
  if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
  if ($("ad-auto").checked) autoTimer = setInterval(() => reloadAll().catch(() => { }), 15000);
}

const GSPARAMS = { fast_ema: 9, slow_ema: 21, trend_ema: 100, use_trend_filter: 1, confirm: 2, min_body: 0.4, sl_ema_buffer_pct: 0.2, swing_lookback: 10, tp_r: 1.0, partial_pct: 0.5, whipsaw_max_crosses: 2, whipsaw_window: 5, whipsaw_suspend_hours: 12, post_sl_cooldown_bars: 0, avoid_daily_close: 1, aggressive_entries: 0 };
const GBOOL = new Set(["use_trend_filter", "avoid_daily_close", "aggressive_entries"]);
// global execution/risk config (pushed to managed customers)
const GEXEC = { sizing_mode: "risk_stop", risk_percent: 1, fixed_size: 0.003, fixed_quote: 25, order_type: "market", leverage: 0, margin_mode: "", auto_bracket: 1, tp1_fraction: 0.5, max_open: 3, daily_loss_pct: 5, daily_profit_pct: 0, cooldown: 0, dedupe: 5, scale_in: 0, scale_in_trigger: 40, scale_in_size: 50, scale_in_be: 1 };
const GEXSEL = new Set(["sizing_mode", "order_type", "margin_mode"]);   // string selects
const GEXBOOL = new Set(["auto_bracket", "scale_in", "scale_in_be"]);
async function loadStrategy() {
  try {
    const g = await api("/api/admin/strategy"); const p = g.params || {};
    for (const k in GSPARAMS) { const el = $("gp-" + k); if (!el) continue; const v = p[k] !== undefined ? p[k] : GSPARAMS[k]; el.value = GBOOL.has(k) ? (v ? "1" : "0") : v; }
    const ex = g.execution || {};
    for (const k in GEXEC) { const el = $("gx-" + k); if (!el) continue; const v = ex[k] !== undefined ? ex[k] : GEXEC[k]; el.value = GEXBOOL.has(k) ? (v ? "1" : "0") : v; }
    if ($("gs-tf")) $("gs-tf").value = g.timeframe || "15m";
    setSyms(g.symbols || "BTC/USDT");
    if ($("gs-info")) $("gs-info").innerHTML = `Version <b>${g.version}</b>${g.updated ? " \u00b7 updated " + new Date(g.updated * 1000).toLocaleString() : ""} \u00b7 live for all managed customers.`;
  } catch (e) { }
}
async function saveGlobalStrategy() {
  const params = {}; for (const k in GSPARAMS) { const el = $("gp-" + k); if (!el) continue; let v = parseFloat(el.value); if (isNaN(v)) v = GSPARAMS[k]; params[k] = v; }
  const execution = {}; for (const k in GEXEC) { const el = $("gx-" + k); if (!el) continue;
    if (GEXSEL.has(k)) execution[k] = el.value;
    else if (GEXBOOL.has(k)) execution[k] = el.value === "1";
    else { let v = parseFloat(el.value); execution[k] = isNaN(v) ? GEXEC[k] : v; } }
  if (!confirm("Save this strategy + execution/risk and push it live to ALL managed customers now?")) return;
  try { await api("/api/admin/strategy", "POST", { params, execution, timeframe: $("gs-tf").value, symbols: $("gs-sym").value.trim() }); notify("Strategy saved \u2014 pushing to all customers \u2713", "ok"); loadStrategy(); loadAudit(); }
  catch (e) { notify(e.message, "error"); }
}

/* ---- Symbols multi-select dropdown ---- */
const POPULAR_PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "TRX/USDT", "DOT/USDT", "MATIC/USDT", "LTC/USDT", "TON/USDT", "SHIB/USDT", "NEAR/USDT", "UNI/USDT", "APT/USDT", "ATOM/USDT", "SUI/USDT", "ARB/USDT", "OP/USDT", "INJ/USDT", "PEPE/USDT", "FIL/USDT"];
let SYMS = [];
function normPair(p) { p = String(p || "").toUpperCase().trim().replace(/\s+/g, ""); if (!p) return ""; if (p.indexOf("/") < 0) { if (p.endsWith("USDT")) p = p.slice(0, -4) + "/USDT"; else if (p.endsWith("USDC")) p = p.slice(0, -4) + "/USDC"; else p = p + "/USDT"; } return p; }
function setSyms(csv) { const seen = new Set(); SYMS = []; String(csv || "").split(",").forEach(s => { const n = normPair(s); if (n && !seen.has(n)) { seen.add(n); SYMS.push(n); } }); syncSyms(); renderSymChips(); renderSymOptions(); }
function syncSyms() { if ($("gs-sym")) $("gs-sym").value = SYMS.join(","); }
function toggleSym(p) { p = normPair(p); if (!p) return; const i = SYMS.indexOf(p); if (i >= 0) SYMS.splice(i, 1); else SYMS.push(p); syncSyms(); renderSymChips(); renderSymOptions(); }
function renderSymChips() {
  const box = $("sym-chips"); if (!box) return;
  if (!SYMS.length) { box.innerHTML = '<span class="psel-ph">Select trading pairs\u2026</span>'; return; }
  box.innerHTML = SYMS.map(p => `<span class="psel-chip">${esc(p)}<i data-p="${esc(p)}">\u00d7</i></span>`).join("");
  box.querySelectorAll("i[data-p]").forEach(i => i.onclick = e => { e.stopPropagation(); toggleSym(i.getAttribute("data-p")); });
}
function renderSymOptions() {
  const box = $("sym-options"); if (!box) return;
  const q = ($("sym-search").value || "").toUpperCase().trim();
  const list = POPULAR_PAIRS.filter(p => !q || p.indexOf(q) >= 0);
  const extra = SYMS.filter(p => POPULAR_PAIRS.indexOf(p) < 0 && (!q || p.indexOf(q) >= 0));
  let html = extra.concat(list).map(p => `<label class="psel-opt"><input type="checkbox" data-p="${esc(p)}" ${SYMS.indexOf(p) >= 0 ? "checked" : ""}/> ${esc(p)}</label>`).join("");
  const qn = normPair(q);
  if (qn && POPULAR_PAIRS.indexOf(qn) < 0 && SYMS.indexOf(qn) < 0) html += `<div class="psel-add" data-add="${esc(qn)}">\u2795 Add "${esc(qn)}"</div>`;
  box.innerHTML = html || '<div class="psel-empty">No match \u2014 type a pair and press Enter</div>';
  box.querySelectorAll("input[data-p]").forEach(i => i.onchange = () => toggleSym(i.getAttribute("data-p")));
  box.querySelectorAll("[data-add]").forEach(d => d.onclick = () => { toggleSym(d.getAttribute("data-add")); $("sym-search").value = ""; renderSymOptions(); });
}
function symMenu(open) { const m = $("sym-menu"), f = $("sym-field"); if (!m) return; const willOpen = open === undefined ? m.classList.contains("hidden") : open; m.classList.toggle("hidden", !willOpen); if (f) f.classList.toggle("open", willOpen); if (willOpen) { renderSymOptions(); const s = $("sym-search"); if (s) { s.value = ""; setTimeout(() => s.focus(), 0); } } }
document.addEventListener("click", e => { const w = $("sym-msel"); if (!w) return; if (w.contains(e.target)) { if ($("sym-field").contains(e.target)) symMenu(); } else symMenu(false); });
document.addEventListener("input", e => { if (e.target && e.target.id === "sym-search") renderSymOptions(); });
document.addEventListener("keydown", e => { if (e.target && e.target.id === "sym-search") { if (e.key === "Enter") { e.preventDefault(); const n = normPair(e.target.value); if (n) { if (SYMS.indexOf(n) < 0) toggleSym(n); e.target.value = ""; renderSymOptions(); } } else if (e.key === "Escape") { symMenu(false); } } });
async function loadRequests() {
  try { const d = await api("/api/admin/strategy_requests"); renderRequests(d.requests || []); } catch (e) { }
}
function renderRequests(list) {
  if ($("req-count")) $("req-count").textContent = list.length;
  const el = $("req-list"); if (!el) return;
  el.innerHTML = list.length ? list.map(r => {
    const nm = [r.first_name, r.last_name].filter(Boolean).join(" ") || r.email;
    return `<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--line)"><div><b>${esc(nm)}</b> <span class="k mono">${esc(r.email)}</span><div class="k" style="margin-top:2px">${esc(r.custom_reason || "(no reason given)")} \u00b7 ${ago(r.custom_requested)}</div></div><div style="display:flex;gap:8px;flex-shrink:0"><button class="btn green sm" onclick="approveCustom(${r.id})">✅ Approve</button><button class="btn ghost sm" onclick="denyCustom(${r.id})">🚫 Deny</button></div></div>`;
  }).join("") : '<div class="k">No pending requests.</div>';
}
async function approveCustom(uid) { try { await api("/api/admin/action", "POST", { user_id: uid, action: "unlock_strategy" }); notify("Approved \u2014 customer can now customize \u2713", "ok"); loadRequests(); reloadAll(); } catch (e) { notify(e.message, "error"); } }
async function denyCustom(uid) { try { await api("/api/admin/action", "POST", { user_id: uid, action: "deny_custom" }); notify("Request dismissed", "warn"); loadRequests(); } catch (e) { notify(e.message, "error"); } }
function notify(msg, type = "ok") {
  const wrap = $("toasts"); if (!wrap) { alert(msg); return; }
  const icon = type === "error" ? "⚠️" : type === "warn" ? "⚠️" : "✅";
  const el = document.createElement("div"); el.className = "toast " + type;
  el.innerHTML = `<span>${icon}</span><span class="tx">${esc(msg)}</span>`;
  el.onclick = () => el.remove(); wrap.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 320); }, 3400);
}
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

if (TOKEN) boot(); else showGate();
