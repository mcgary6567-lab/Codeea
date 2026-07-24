// Trevolto Web — dashboard client
const CHECKOUT_URL = "https://trevolto.com/checkout";
let TOKEN = localStorage.getItem("trevolto_token") || "";
let authMode = "login";
let ws = null, lastState = null;

const $ = id => document.getElementById(id);
const fmt = (n, d = 2) => (n === null || n === undefined || isNaN(n)) ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d });
const esc = x => String(x).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

async function api(path, method = "GET", bodyObj) {
  const opt = { method, headers: {} };
  if (TOKEN) opt.headers["Authorization"] = "Bearer " + TOKEN;
  if (bodyObj) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(bodyObj); }
  const r = await fetch(path, opt);
  const txt = await r.text();
  let data; try { data = JSON.parse(txt); } catch { data = { detail: txt }; }
  if (!r.ok) throw new Error(data.detail || data.message || ("HTTP " + r.status));
  return data;
}

// ---- auth ----
function authTab(m) {
  authMode = m;
  $("tab-login").classList.toggle("on", m === "login");
  $("tab-reg").classList.toggle("on", m === "reg");
  $("reg-fields").classList.toggle("hidden", m !== "reg");
  if ($("auth-title")) {
    $("auth-title").textContent = m === "reg" ? "Start your free trial" : "Welcome back";
    $("auth-sub").textContent = m === "reg" ? "10 days free — no card required" : "Sign in to your trading dashboard";
  }
}
async function doAuth() {
  $("au-err").textContent = "";
  try {
    const body = { email: $("au-email").value.trim(), password: $("au-pass").value };
    if (authMode === "reg") { body.first_name = $("au-first").value.trim(); body.last_name = $("au-last").value.trim(); }
    const totp = $("au-totp").value.trim(); if (totp) body.totp = totp;
    const d = await api("/api/" + (authMode === "reg" ? "register" : "login"), "POST", body);
    if (d.need_2fa) { $("tfa-field").classList.remove("hidden"); $("au-err").textContent = "Enter your 6-digit 2FA code."; $("au-totp").focus(); return; }
    TOKEN = d.token; localStorage.setItem("trevolto_token", TOKEN); enterApp();
  } catch (e) { $("au-err").textContent = e.message; }
}
async function forgotPassword() {
  const email = ($("au-email").value || "").trim() || prompt("Enter your account email:");
  if (!email) return;
  try { await api("/api/forgot", "POST", { email }); notify("If that email is registered, a reset link has been sent.", "ok"); }
  catch (e) { notify(e.message, "error"); }
}
let RESET_TOKEN = null;
function checkResetUrl() {
  const t = new URLSearchParams(location.search).get("reset");
  if (t) { RESET_TOKEN = t; $("login-panel").classList.add("hidden"); $("reset-panel").classList.remove("hidden"); const tw = document.querySelector(".tabsw"); if (tw) tw.classList.add("hidden"); }
}
async function doReset() {
  $("rs-err").textContent = "";
  const pw = $("rs-pass").value; if (pw.length < 6) { $("rs-err").textContent = "6+ characters."; return; }
  try {
    const d = await api("/api/reset", "POST", { token: RESET_TOKEN, password: pw });
    TOKEN = d.token; localStorage.setItem("trevolto_token", TOKEN);
    history.replaceState({}, "", location.pathname);
    notify("Password reset ✓", "ok"); enterApp();
  } catch (e) { $("rs-err").textContent = e.message; }
}
// ---- 2FA settings ----
async function twofaSetup() { try { const d = await api("/api/2fa/setup", "POST"); $("tfa-secret").textContent = d.secret; $("tfa-setup").classList.remove("hidden"); } catch (e) { notify(e.message, "error"); } }
async function twofaEnable() { try { await api("/api/2fa/enable", "POST", { code: $("tfa-code").value.trim() }); notify("2FA enabled ✓", "ok"); refresh(); } catch (e) { notify(e.message, "error"); } }
async function twofaDisable() { try { await api("/api/2fa/disable", "POST", { password: $("tfa-pass").value }); notify("2FA disabled", "warn"); $("tfa-pass").value = ""; refresh(); } catch (e) { notify(e.message, "error"); } }
function logout() { localStorage.removeItem("trevolto_token"); TOKEN = ""; if (ws) ws.close(); stopSocialProof(); spDismissed = false; spLoaded = false; $("app").classList.add("hidden"); $("landing").classList.remove("hidden"); }
function enterApp() {
  $("landing").classList.add("hidden"); $("app").classList.remove("hidden");
  const foll = localStorage.getItem("ch_follow") !== "0";   // ON by default
  $("ch-follow").checked = foll; $("ch-sym").disabled = foll; $("ch-tf").disabled = foll;
  refresh(); openWs(); loadChart(); pollPrices(); updateNotifBtn(); registerSW(); if (browserNotifyOn()) subscribePush();
}

// ---- browser / system notifications ----
let lastAlertTs = 0, alertInit = false, unread = 0, seenTs = parseFloat(localStorage.getItem("bell_seen_ts") || "0"), newestAlertTs = 0;
function notifIcon() { const i = document.querySelector('.brand img'); return i ? i.src : ""; }
function notifTitle() { const i = document.querySelector('.brand img'); return (i && i.alt) || "Alert"; }
function browserNotifyOn() { return localStorage.getItem("notif") === "1" && "Notification" in window && Notification.permission === "granted"; }
function updateNotifBtn() { const b = $("notif-btn"); if (b) b.textContent = browserNotifyOn() ? "\uD83D\uDD14 Notifications ON \u2014 click to turn off" : "\uD83D\uDD14 Enable browser notifications"; }
function toggleNotif() {
  if (!("Notification" in window)) { notify("This browser doesn't support notifications", "warn"); return; }
  if (browserNotifyOn()) { localStorage.setItem("notif", "0"); updateNotifBtn(); unsubscribePush(); notify("Browser notifications off", "warn"); return; }
  Notification.requestPermission().then(p => {
    if (p === "granted") { localStorage.setItem("notif", "1"); updateNotifBtn(); subscribePush(); try { new Notification(notifTitle(), { body: "Notifications enabled \u2014 you'll be alerted on trades.", icon: notifIcon() }); } catch (e) { } }
    else notify("Notifications are blocked in your browser settings", "warn");
  });
}
function processAlerts(s) {
  const a = s.alerts || [];
  renderBell(a);
  if (a.length) newestAlertTs = a[0].ts;
  unread = a.filter(x => x.ts > seenTs).length;   // persistent unseen count (survives reloads)
  updateBadge();
  if (!a.length) return;
  const newestTs = a[0].ts;
  if (!alertInit) { lastAlertTs = newestTs; alertInit = true; return; }   // don't toast history on first load
  if (newestTs <= lastAlertTs) return;
  const fresh = a.filter(x => x.ts > lastAlertTs).sort((x, y) => x.ts - y.ts);
  lastAlertTs = newestTs;
  fresh.forEach(x => {
    const t = x.msg.indexOf("\u274C") >= 0 ? "error" : (x.msg.indexOf("\u23ED") >= 0 ? "warn" : "ok");
    notify(x.msg.split("\n").join(" \u00b7 "), t);                              // in-app toast
    if (browserNotifyOn() && !pushActive) { try { new Notification(notifTitle(), { body: x.msg, icon: notifIcon() }); } catch (e) { } }
  });
}
function bellTime(ts) { const d = Date.now() / 1000 - ts; if (d < 60) return "just now"; if (d < 3600) return Math.floor(d / 60) + "m ago"; if (d < 86400) return Math.floor(d / 3600) + "h ago"; return Math.floor(d / 86400) + "d ago"; }
function renderBell(a) {
  const list = $("bell-list"); if (!list) return;
  if (!a || !a.length) { list.innerHTML = '<div class="bell-empty">No alerts yet.</div>'; return; }
  list.innerHTML = a.map(x => `<div class="bell-item">${esc(x.msg).split("\n").join("<br>")}<div class="bell-time">${bellTime(x.ts)}</div></div>`).join("");
}
function updateBadge() { const b = $("bell-badge"); if (!b) return; b.textContent = unread > 9 ? "9+" : unread; b.classList.toggle("hidden", unread <= 0); const btn = $("bell-btn"); if (btn) btn.classList.toggle("has-unread", unread > 0); }
function toggleBell() { const p = $("bell-panel"); if (!p) return; p.classList.toggle("hidden"); if (!p.classList.contains("hidden")) { seenTs = newestAlertTs || (Date.now() / 1000); localStorage.setItem("bell_seen_ts", String(seenTs)); unread = 0; updateBadge(); } }
document.addEventListener("click", e => { const p = $("bell-panel"); if (p && !p.classList.contains("hidden") && !e.target.closest(".bell-wrap")) p.classList.add("hidden"); });


// Chart "Follow strategy": lock the chart symbol + timeframe to the strategy's.
function chFollowToggle() {
  const on = $("ch-follow").checked;
  localStorage.setItem("ch_follow", on ? "1" : "0");
  $("ch-sym").disabled = on; $("ch-tf").disabled = on;
  if (on) syncChartToStrategy(true);
}
function syncChartToStrategy(forceLoad) {
  if (!lastState) return;
  const s = lastState.settings || {};
  const sym = (s.strategy_symbols || "BTC/USDT").split(",")[0].trim();
  const tf = s.strategy_timeframe || "15m";
  let changed = false;
  if ($("ch-sym").value !== sym) { $("ch-sym").value = sym; changed = true; }
  if ($("ch-tf").value !== tf) { $("ch-tf").value = tf; changed = true; }
  if (forceLoad || changed) loadChart();
}

// ---- views ----
const VIEWS = ["home", "exchange", "strategy", "backtest", "analytics", "log", "settings", "guide"];
let accessBuyable = false;
function goCheckout() { if (!accessBuyable) return; window.open(CHECKOUT_URL, "_blank", "noopener"); }
function dismissAnnounce(id) { localStorage.setItem("bc_seen", String(id)); const ab = $("announce"); if (ab) ab.classList.add("hidden"); }
function qNav(v) { return document.querySelector('.side button[data-v="' + v + '"]'); }
function show(v, btn) {
  VIEWS.forEach(x => $("v-" + x).classList.toggle("hidden", x !== v));
  document.querySelectorAll(".side button").forEach(b => b.classList.toggle("on", b === btn));
  document.querySelector(".side").classList.remove("open");
  if (v === "analytics") loadAnalytics();
  if (v === "home") loadChart();
  if (v === "settings") loadLogins();
  if (v === "exchange") loadServerIP();
}

// ---- live state ----
function openWs() {
  try {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${TOKEN}`);
    ws.onmessage = ev => render(JSON.parse(ev.data));
    ws.onclose = () => { ws = null; setTimeout(() => { if (TOKEN) openWs(); }, 3000); };
  } catch (e) { }
}
async function refresh() { try { render(await api("/api/state")); } catch (e) { if (String(e).includes("401")) logout(); } }
async function clearLog() {
  if (!confirm("Clear the activity log? This can't be undone.")) return;
  try { await api("/api/log/clear", "POST"); if ($("log")) $("log").innerHTML = '<div class="empty">Log cleared.</div>'; notify("Activity log cleared 🗑", "ok"); }
  catch (e) { notify(e.message, "error"); }
}
setInterval(() => { if (TOKEN && !ws) refresh(); }, 3000);

function render(s) {
  lastState = s;
  processAlerts(s);
  if (s.signal) renderSignal(s.signal);   // live signal-strength meter (when the bot is running)
  renderNews(s.news);                     // high-impact news filter toggle + blackout flag
  $("st-dot").classList.toggle("on", s.connected);
  $("st-conn").textContent = s.connected ? "Connected" : "Disconnected";
  $("st-ex").textContent = s.exchange ? `${s.exchange} · ${s.market_type}` : "—";
  $("ex-state").textContent = s.connected ? "connected" : "not connected";
  if ($("btn-clear-keys")) $("btn-clear-keys").classList.toggle("hidden", !(s.has_keys || s.connected));
  // keys stay blank for security, but show they're saved + restore the exchange picker
  { const kk = $("cx-key"), ks = $("cx-sec"); const ph = (s.has_keys || s.connected) ? "•••••••• saved — leave blank to keep" : null;
    if (kk && !kk.value) kk.placeholder = ph || "key";
    if (ks && !ks.value) ks.placeholder = ph || "secret";
    const setSel = (id, v) => { const el = $(id); if (el && v && document.activeElement !== el) el.value = v; };
    if (s.connected) { setSel("cx-ex", s.exchange); setSel("cx-mkt", s.market_type); } }
  $("st-ro").classList.toggle("hidden", !s.read_only);
  $("st-halt").classList.toggle("hidden", !s.guard_tripped);
  $("st-paper").classList.toggle("hidden", !s.paper_mode);
  { const an = s.announcement, ab = $("announce"); if (ab) { if (an && an.message && localStorage.getItem("bc_seen") !== String(an.id)) { ab.classList.remove("hidden"); ab.innerHTML = "📢 " + esc(an.message) + ` <a onclick="dismissAnnounce(${an.id})" style="cursor:pointer;text-decoration:underline">dismiss</a>`; } else ab.classList.add("hidden"); } }
  { const ww = $("withdraw-warn"); if (ww) { ww.classList.toggle("hidden", !s.key_withdraw_warn); if (s.key_withdraw_warn) ww.innerHTML = "\u26a0\ufe0f <b>Your API key has withdrawals enabled.</b> For safety, replace it with a trade-only key (withdrawals disabled) on your exchange."; } }
  const name = s.name || (s.email || "").split("@")[0];
  $("st-email").textContent = "👋 " + name;
  $("welcome").textContent = "Welcome back, " + name + " 👋";
  $("st-admin").classList.toggle("hidden", !s.is_admin);
  const setIf = (id, v) => { if ($(id) && !(document.activeElement && document.activeElement.id === id)) $(id).value = v || ""; };
  setIf("ac-first", s.first_name); setIf("ac-last", s.last_name);
  if ($("tfa-on")) { $("tfa-on").classList.toggle("hidden", !s.totp_enabled); $("tfa-off").classList.toggle("hidden", !!s.totp_enabled); }

  const ac = s.access || {};
  const lbl = { admin: "ADMIN", licensed: "LICENSED", trial: "TRIAL", suspended: "SUSPENDED", expired: "EXPIRED" }[ac.status] || "—";
  const suf = ac.lifetime ? " · ∞" : ((ac.days_left != null && (ac.status === "trial" || ac.status === "licensed")) ? ` · ${ac.days_left}d` : "");
  $("st-access").textContent = lbl + suf;
  $("st-access").className = "chip " + (ac.ok ? (ac.status === "trial" ? "warn" : "safe") : "danger");
  const buyable = ac.status === "trial" || ac.status === "expired";
  accessBuyable = buyable;
  maybeSocialProof(buyable);          // FOMO purchase popup for trial/expired users
  $("st-access").style.cursor = buyable ? "pointer" : "default";
  $("st-access").title = buyable ? "Buy / renew licence" : "";
  if ($("st-buy")) { $("st-buy").classList.toggle("hidden", !buyable); $("st-buy").href = CHECKOUT_URL; }
  const warn = $("access-warn");
  const buyBtn = `<a class="btn" href="${CHECKOUT_URL}" target="_blank" rel="noopener" style="margin-left:12px">🔓 Buy licence</a>`;
  const NUDGE_DAYS = 2;
  if (ac.status && !ac.ok) {
    warn.classList.remove("hidden");
    warn.innerHTML = ac.status === "suspended"
      ? "⛔ Your account is suspended — trading is stopped. Please contact support."
      : `⏳ <b>Your free trial has ended</b> — the bot has stopped trading. Buy a licence to reactivate.${buyBtn}`;
  } else if (ac.status === "trial" && ac.days_left != null && ac.days_left <= NUDGE_DAYS) {
    // pre-expiry nudge — still trading, but remind them to upgrade
    warn.classList.remove("hidden");
    warn.innerHTML = `⏳ Your free trial ends in <b>${ac.days_left} day${ac.days_left === 1 ? "" : "s"}</b> — buy a licence now to keep the bot trading without interruption.${buyBtn}`;
  } else warn.classList.add("hidden");

  $("t-bal").textContent = fmt(s.balance);
  const pnl = $("t-pnl"); pnl.textContent = (s.pnl >= 0 ? "+" : "") + fmt(s.pnl); pnl.className = "v mono " + (s.pnl >= 0 ? "pos" : "neg");
  $("t-access").textContent = lbl; $("t-accessd").textContent = ac.lifetime ? "Lifetime access" : (ac.days_left != null ? `${ac.days_left} days left` : (ac.status || ""));
  $("t-strat").textContent = s.strategy_on ? "Running" : (s.strategy_enabled ? "On (idle)" : "Off");
  $("t-strat").className = "v " + (s.strategy_on ? "pos" : (s.strategy_enabled ? "" : "neg"));
  $("strat-state").textContent = s.strategy_on ? "running" : (s.strategy_enabled ? "on — waiting for connection" : "off");
  $("tr-mode").textContent = (s.settings || {}).sizing_mode || "—";

  const tb = $("pos-body");
  if (!s.positions || !s.positions.length) tb.innerHTML = `<tr><td colspan="6" class="k">No open positions.</td></tr>`;
  else tb.innerHTML = s.positions.map(p => `<tr>
    <td class="mono">${p.pair}</td><td class="${p.side === 'Long' ? 'pos' : 'neg'}">${p.side}</td>
    <td class="mono">${fmt(p.size, 5)}</td><td class="mono">${fmt(p.entry, 4)}</td>
    <td class="mono ${p.pnl >= 0 ? 'pos' : 'neg'}">${(p.pnl >= 0 ? '+' : '') + fmt(p.pnl, 4)}</td>
    <td><button class="btn ghost sm" onclick="closePos('${p.pair}')">✖ Close</button></td></tr>`).join("");

  $("log").innerHTML = (s.log && s.log.length)
    ? s.log.map(l => `<div class="l"><span class="t">${new Date(l.ts * 1000).toLocaleTimeString()}</span> <span class="${l.level}">${esc(l.msg)}</span></div>`).join("")
    : `<div class="empty">No activity yet — connect an exchange and your bot's actions will show up here.</div>`;
  applySettings(s.settings || {}, s.email);
  lockStrategy(s);
  if ($("ch-follow") && $("ch-follow").checked) syncChartToStrategy(false);
}

// ---- live price strip ----
async function pollPrices() {
  if (!TOKEN) return;
  try {
    const d = await api("/api/prices");
    $("pricestrip").innerHTML = Object.entries(d).map(([sym, t]) => {
      const pct = t && t.pct != null ? t.pct : 0;
      return `<div class="pcard"><div class="psym">${sym.replace('/USDT', '')}<span class="k">/USDT</span></div>
        <div class="pprice mono">${t && t.price != null ? fmt(t.price, t.price < 10 ? 5 : 2) : '—'}</div>
        <div class="pchg ${pct >= 0 ? 'pos' : 'neg'}">${pct >= 0 ? '▲' : '▼'} ${fmt(Math.abs(pct), 2)}%</div></div>`;
    }).join("");
  } catch (e) { }
  markTick(); loadPnlModes();
  setTimeout(pollPrices, 10000);
}
async function loadPnlModes() {
  if (!TOKEN) return;
  try {
    const d = await api("/api/pnl_modes");
    const any = (d.live.trades + d.paper.trades) > 0;
    $("pnl-compare-card").style.display = any ? "" : "none";
    const fill = (m, o) => {
      const el = $("pc-" + m + "-pnl"); el.textContent = (o.pnl >= 0 ? "+" : "") + fmt(o.pnl);
      el.className = "v mono " + (o.pnl >= 0 ? "pos" : "neg");
      $("pc-" + m + "-tr").textContent = o.trades; $("pc-" + m + "-wr").textContent = fmt(o.win_rate);
    };
    fill("live", d.live); fill("paper", d.paper);
  } catch (e) { }
}

// ---- connect / trade ----
async function saveConnect() {
  try {
    const key = $("cx-key").value.trim(), sec = $("cx-sec").value.trim();
    if (key && sec) {
      await api("/api/keys", "POST", { exchange: $("cx-ex").value, market_type: $("cx-mkt").value, api_key: key, api_secret: sec, password: $("cx-pass").value.trim() });
    } else if (!(lastState && lastState.has_keys)) {
      notify("Enter your API key and secret.", "warn"); return;
    }
    await api("/api/connect", "POST");           // reconnects with saved keys when fields are blank
    notify("Connected to " + $("cx-ex").value + " ✓", "ok");
    refresh(); show('home', document.querySelector('.side button'));
  } catch (e) { notify("Connect failed: " + e.message, "error"); }
}
async function disconnect() {
  try { await api("/api/disconnect", "POST"); notify("Exchange disconnected", "warn"); refresh(); }
  catch (e) { notify(e.message, "error"); }
}
async function clearKeys() {
  if (!confirm("Remove your saved exchange API keys?\nThe bot stops trading and disconnects, and you'll need to paste keys again to reconnect.")) return;
  try { await api("/api/keys/clear", "POST"); notify("Saved API keys removed 🗑", "ok"); refresh(); }
  catch (e) { notify(e.message, "error"); }
}
async function trade(side) {
  const size = parseFloat($("tr-size").value);
  if (!confirm(`${side.toUpperCase()} ${$("tr-sym").value} — LIVE order. Continue?`)) return;
  try {
    const r = await api("/api/trade", "POST", { side, symbol: $("tr-sym").value, size: isNaN(size) ? null : size });
    if (r.ok) notify(`${side.toUpperCase()} ${$("tr-sym").value} — ${r.message || "order placed"}`, "ok");
    else notify(r.message || "Order rejected", "error");
    refresh();
  } catch (e) { notify(e.message, "error"); }
}
async function closePos(sym) {
  if (!confirm("Close " + sym + "?")) return;
  try { const r = await api("/api/close", "POST", { symbol: sym }); notify(r.ok ? `Closed ${sym}` : (r.message || "Close failed"), r.ok ? "ok" : "error"); refresh(); }
  catch (e) { notify(e.message, "error"); }
}
async function panic() {
  if (!confirm("Close ALL positions now?")) return;
  try { const r = await api("/api/close_all", "POST"); notify(r.message || "All positions closed", "warn"); refresh(); }
  catch (e) { notify(e.message, "error"); }
}

// ---- settings ----
function lockStrategy(st) {
  const setDis = (dis) => {
    for (const k in SPARAMS) { const el = $("p-" + k); if (el) el.disabled = dis; }
    if ($("sg-tf")) $("sg-tf").disabled = dis;
    const mh = document.querySelector("#v-strategy .msel-head"); if (mh) mh.style.pointerEvents = dis ? "none" : "";
    ["btn-save-strat", "btn-reset-strat"].forEach(id => { const b = $(id); if (b) b.classList.toggle("hidden", dis); });
  };
  if (!st || !st.strategy_managed) {                 // unlocked — customer controls it
    if ($("strat-managed-banner")) $("strat-managed-banner").classList.add("hidden");
    if ($("strat-upgrade")) $("strat-upgrade").classList.add("hidden");
    setDis(false); return;
  }
  const m = st.managed_strategy || {}, p = m.params || {};
  for (const k in SPARAMS) { const v = p[k] !== undefined ? p[k] : SPARAMS[k]; const el = $("p-" + k); if (el) el.value = BOOLP.has(k) ? (v ? "1" : "0") : v; }
  if ($("sg-tf")) $("sg-tf").value = m.timeframe || "15m";
  if (typeof mselSet === "function") mselSet(m.symbols || "BTC/USDT");
  setDis(true);
  if ($("strat-managed-banner")) $("strat-managed-banner").classList.remove("hidden");
  if ($("strat-upgrade")) $("strat-upgrade").classList.remove("hidden");
  const btn = $("strat-req-btn"), status = $("strat-req-status");
  if (st.custom_requested) { if (btn) btn.classList.add("hidden"); if (status) status.innerHTML = "⏳ <b>Request pending</b> — we'll review it shortly."; }
  else { if (btn) btn.classList.remove("hidden"); if (status) status.textContent = ""; }
}
async function requestCustom() {
  const reason = prompt("Tell us briefly why you'd like to run your own strategy (optional):", "");
  if (reason === null) return;
  try { await api("/api/strategy/request", "POST", { reason }); notify("Request sent ✓ — we'll review it shortly.", "ok"); refresh(); }
  catch (e) { notify(e.message, "error"); }
}
function applySettings(s, email) {
  if (document.activeElement && ["INPUT", "SELECT"].includes(document.activeElement.tagName)) return;
  const set = (id, v) => { if ($(id) != null && v !== undefined) $(id).value = v; };
  const chk = (id, v) => { if ($(id) != null) $(id).checked = !!v; };
  set("s-sizing", s.sizing_mode); set("s-fixed", s.fixed_size); set("s-fixedq", s.fixed_quote); set("s-risk", s.risk_percent);
  set("s-otype", s.order_type); set("s-lev", s.leverage); set("s-margin", s.margin_mode); set("s-tp1f", s.tp1_fraction);
  chk("s-bracket", s.auto_bracket); chk("s-ro", s.read_only); chk("s-paper", s.paper_mode);
  set("s-maxopen", s.max_open); set("s-dloss", s.daily_loss); set("s-dprofit", s.daily_profit); set("s-cool", s.cooldown); set("s-dedupe", s.dedupe);
  mselSet(s.strategy_symbols || "BTC/USDT"); if (s.strategy_timeframe) set("sg-tf", s.strategy_timeframe);
  set("tg-token", s.telegram_token); set("tg-chat", s.telegram_chat); set("ac-email", email);
  chk("s-summary", s.daily_summary); chk("s-alert-skips", s.alert_skips);
  const p = s.strategy_params || {};
  for (const k in SPARAMS) { const v = p[k] !== undefined ? p[k] : SPARAMS[k]; if (BOOLP.has(k)) set("p-" + k, v ? "1" : "0"); else set("p-" + k, v); }
}
async function saveSettings() {
  const num = id => parseFloat($(id).value) || 0;
  try {
    await api("/api/settings", "POST", { sizing_mode: $("s-sizing").value, fixed_size: num("s-fixed"), fixed_quote: num("s-fixedq"), risk_percent: num("s-risk"), order_type: $("s-otype").value, leverage: num("s-lev"), margin_mode: $("s-margin").value, tp1_fraction: num("s-tp1f"), auto_bracket: $("s-bracket").checked, read_only: $("s-ro").checked, paper_mode: $("s-paper").checked, max_open: num("s-maxopen"), daily_loss: num("s-dloss"), daily_profit: num("s-dprofit"), cooldown: num("s-cool"), dedupe: num("s-dedupe") });
    refresh(); notify("Settings saved ✓", "ok");
  } catch (e) { notify(e.message, "error"); }
}
async function saveTelegram() {
  try { await api("/api/settings", "POST", { telegram_token: $("tg-token").value.trim(), telegram_chat: $("tg-chat").value.trim(), daily_summary: $("s-summary").checked, alert_skips: $("s-alert-skips").checked }); notify("Telegram settings saved ✓", "ok"); }
  catch (e) { notify(e.message, "error"); }
}
async function tgTest() {
  const btn = $("tg-test"), res = $("tg-result");
  btn.disabled = true; btn.textContent = "Sending…"; res.textContent = ""; res.style.color = "";
  try {
    const d = await api("/api/telegram/test", "POST", { token: $("tg-token").value.trim(), chat: $("tg-chat").value.trim() });
    res.style.color = d.ok ? "var(--green)" : "var(--red)";
    res.textContent = (d.ok ? "✅ " : "⚠️ ") + d.message;
  } catch (e) { res.style.color = "var(--red)"; res.textContent = "⚠️ " + e.message; }
  finally { btn.disabled = false; btn.textContent = "✈️ Send test"; }
}
async function saveAccount() {
  $("ac-err").textContent = "";
  const b = { current_password: $("ac-cur").value, new_email: $("ac-email").value.trim(), new_password: $("ac-newpass").value, first_name: $("ac-first").value.trim(), last_name: $("ac-last").value.trim() };
  if (!b.current_password) { $("ac-err").textContent = "Enter your current password."; return; }
  try { const d = await api("/api/account", "POST", b); TOKEN = d.token; localStorage.setItem("trevolto_token", TOKEN); $("ac-newpass").value = ""; $("ac-cur").value = ""; toast("Account updated"); refresh(); }
  catch (e) { $("ac-err").textContent = e.message; }
}
// multi-select symbol dropdown (checkbox panel) -> hidden #sg-sym comma list
const POP = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT", "TON/USDT", "TRX/USDT", "PEPE/USDT"];
function mselBuild() {
  const panel = $("sg-panel"); if (!panel || panel._built) return; panel._built = true;
  panel.innerHTML = POP.map(s => `<label class="msel-opt"><input type="checkbox" value="${s}" onchange="mselSync()"/> ${s}</label>`).join("");
}
function mselSync() {
  const sel = [...$("sg-panel").querySelectorAll("input:checked")].map(i => i.value);
  $("sg-sym").value = sel.join(", ");
  $("sg-label").textContent = sel.length ? (sel.length <= 2 ? sel.join(", ") : `${sel.length} pairs selected`) : "Select…";
}
function mselSet(csv) {
  mselBuild();
  const set = new Set((csv || "").split(",").map(x => x.trim()).filter(Boolean));
  $("sg-panel").querySelectorAll("input").forEach(i => i.checked = set.has(i.value));
  mselSync();
}
function mselToggle() { mselBuild(); $("sg-panel").classList.toggle("hidden"); }
document.addEventListener("click", e => { const m = document.querySelector(".msel"); if (m && !m.contains(e.target) && $("sg-panel")) $("sg-panel").classList.add("hidden"); });

// ---- strategy params (EMA 9/21 model) ----
const SPARAMS = { fast_ema: 9, slow_ema: 21, trend_ema: 100, use_trend_filter: 1, confirm: 2, min_body: 0.4, sl_ema_buffer_pct: 0.2, swing_lookback: 10, tp_r: 1.0, partial_pct: 0.5, whipsaw_max_crosses: 2, whipsaw_window: 5, whipsaw_suspend_hours: 12, post_sl_cooldown_bars: 0, avoid_daily_close: 1 };
const INTP = new Set(["fast_ema", "slow_ema", "trend_ema", "confirm", "swing_lookback", "whipsaw_max_crosses", "whipsaw_window", "post_sl_cooldown_bars"]);
const BOOLP = new Set(["use_trend_filter", "avoid_daily_close"]);
function collectParams() {
  const p = {};
  for (const k in SPARAMS) {
    const el = $("p-" + k); if (!el) continue;
    if (BOOLP.has(k)) { p[k] = el.value === "1"; continue; }
    let v = parseFloat(el.value); if (isNaN(v)) v = SPARAMS[k];
    p[k] = INTP.has(k) ? Math.round(v) : v;
  }
  return p;
}
async function strategy(enable) { try { const r = await api("/api/strategy", "POST", { enable, symbols: $("sg-sym").value, timeframe: $("sg-tf").value, params: collectParams() }); notify((r && r.message) ? r.message : (enable ? "Strategy enabled ✓" : "Strategy disabled"), enable ? "ok" : "warn"); refresh(); } catch (e) { notify(e.message, "error"); } }
async function saveStrategy() { try { const d = await api("/api/strategy", "POST", { params: collectParams(), symbols: $("sg-sym").value, timeframe: $("sg-tf").value }); notify(d.message || "Strategy saved ✓", d.message ? "warn" : "ok"); refresh(); } catch (e) { notify(e.message, "error"); } }
async function resetStrategy() {
  if (!confirm("Reset all strategy parameters to their defaults?")) return;
  for (const k in SPARAMS) { const el = $("p-" + k); if (!el) continue; el.value = BOOLP.has(k) ? (SPARAMS[k] ? "1" : "0") : SPARAMS[k]; }
  $("sg-tf").value = "15m";
  try { await api("/api/strategy", "POST", { params: collectParams(), symbols: $("sg-sym").value, timeframe: "15m" }); notify("Strategy reset to defaults ✓", "ok"); refresh(); }
  catch (e) { notify(e.message, "error"); }
}

// ---- backtest (pro) ----
let BT = null;   // last result for CSV export
function btPeriodChange() { $("bt-limit-wrap").style.display = $("bt-period").value === "0" ? "" : "none"; }
function streaks(trades) {
  let win = 0, loss = 0, cw = 0, cl = 0;
  for (const t of trades) { if (t.pnl > 0) { cw++; cl = 0; } else if (t.pnl < 0) { cl++; cw = 0; } win = Math.max(win, cw); loss = Math.max(loss, cl); }
  return { win, loss };
}
async function runBacktest() {
  const btn = $("bt-run"); btn.disabled = true; btn.textContent = "Running…"; $("bt-out").classList.add("hidden");
  try {
    const days = parseFloat($("bt-period").value);
    const req = { exchange: $("bt-ex").value, symbol: $("bt-sym").value, timeframe: $("bt-tf").value, start_equity: parseFloat($("bt-eq").value) || 1000, risk_pct: parseFloat($("bt-risk").value) || 0, fee_pct: parseFloat($("bt-fee").value), allow_short: $("bt-short").value === "1", params: collectParams() };
    if (days > 0) req.days = days; else req.limit = parseInt($("bt-limit").value) || 1000;
    const nf = $("bt-news") ? $("bt-news").value : "";   // "" = follow the dashboard News-trading setting
    if (nf === "1") req.news_filter = true; else if (nf === "0") req.news_filter = false;
    const d = await api("/api/backtest", "POST", req); BT = d;
    const s = d.summary, ret = s.return_pct, vs = ret - d.buy_hold_pct, st = streaks(d.trades);
    const wins = d.trades.filter(t => t.pnl > 0), losses = d.trades.filter(t => t.pnl < 0);
    const avgWin = wins.length ? wins.reduce((a, t) => a + t.pnl, 0) / wins.length : 0;
    const avgLoss = losses.length ? losses.reduce((a, t) => a + t.pnl, 0) / losses.length : 0;
    const payoff = avgLoss ? Math.abs(avgWin / avgLoss) : 0;
    const expectancy = d.trades.length ? s.net_pnl / d.trades.length : 0;
    const tiles = [
      ["Return", (ret >= 0 ? "+" : "") + fmt(ret) + "%", ret >= 0 ? "pos" : "neg"],
      ["vs Buy & Hold", (vs >= 0 ? "+" : "") + fmt(vs) + "%", vs >= 0 ? "pos" : "neg"],
      ["Net PnL $", fmt(s.net_pnl), s.net_pnl >= 0 ? "pos" : "neg"],
      ["Final equity", fmt(s.end_equity), ""],
      ["Win rate", fmt(s.win_rate) + "%", ""],
      ["Profit factor", (s.profit_factor > 999 ? "∞" : fmt(s.profit_factor)), ""],
      ["Max drawdown", "-" + fmt(s.max_drawdown) + "%", "neg"],
      ["Trades", `${s.trades} (${s.longs}L/${s.shorts}S)`, ""],
      ["Avg R", fmt(s.avg_r), s.avg_r >= 0 ? "pos" : "neg"],
      ["Expectancy $/trade", fmt(expectancy), expectancy >= 0 ? "pos" : "neg"],
      ["Payoff (win/loss)", fmt(payoff), ""],
      ["Win/Loss streak", `${st.win} / ${st.loss}`, ""],
      ["Best / Worst $", `${fmt(s.best)} / ${fmt(s.worst)}`, ""],
      ["Fees paid $", fmt(s.fees), "neg"],
    ];
    $("bt-tiles").innerHTML = tiles.map(([k, v, c]) => `<div class="tile"><div class="k">${k}</div><div class="v ${c}" style="font-size:18px">${v}</div></div>`).join("");
    $("bt-ntr").textContent = d.trades.length;
    $("bt-trades").innerHTML = d.trades.length
      ? d.trades.slice().reverse().map((t, i) => `<tr><td>${d.trades.length - i}</td><td class="${t.side === 'long' ? 'pos' : 'neg'}">${t.side}</td><td class="mono">${fmt(t.entry, 4)}</td><td class="mono">${fmt(t.exit, 4)}</td><td class="mono">${fmt(t.qty, 5)}</td><td class="mono ${t.pnl >= 0 ? 'pos' : 'neg'}">${(t.pnl >= 0 ? '+' : '') + fmt(t.pnl, 2)}</td><td class="mono ${t.r >= 0 ? 'pos' : 'neg'}">${fmt(t.r, 2)}</td><td class="k">${t.reason}</td></tr>`).join("")
      : `<tr><td colspan="8" class="k" style="text-align:center;padding:16px">No trades were triggered in this period — try a longer range, a different timeframe, or looser settings.</td></tr>`;
    const p = d.period || {};
    let info = `${d.exchange} · ${d.symbol} · ${d.timeframe} — ${d.candles} candles over ~${p.days || "?"} days (${p.from ? new Date(p.from).toLocaleDateString() : "?"} → ${p.to ? new Date(p.to).toLocaleDateString() : "?"})`;
    if (d.news_filter) info += ` · 📰 News filter ON — ${(d.summary.news_skipped || 0)} entr${(d.summary.news_skipped === 1) ? "y" : "ies"} skipped near high-impact news (backtest coverage: current week only; the live bot applies it in real time).`;
    $("bt-period-info").textContent = info;
    $("bt-csv").disabled = !d.trades.length;
    // Un-hide the results BEFORE drawing so the canvases have real dimensions.
    $("bt-out").classList.remove("hidden");
    notify(`Backtest done — ${d.summary.trades} trades, ${fmt(d.summary.return_pct)}% return`, "ok");
    const eq = d.equity.map(e => e[1]);
    requestAnimationFrame(() => {
      drawLine("bt-eqc", eq, "#f97316", true);
      let peak = -1e9;
      drawLine("bt-ddc", eq.map(v => { peak = Math.max(peak, v); return peak > 0 ? -(peak - v) / peak * 100 : 0; }), "#ef4444", true);
    });
  } catch (e) { notify("Backtest: " + e.message, "error"); }
  finally { btn.disabled = false; btn.textContent = "▶ Run backtest"; }
}
function btCsv() {
  if (!BT || !BT.trades.length) return;
  const head = ["#", "side", "entry_ts", "exit_ts", "entry", "exit", "qty", "pnl", "R", "reason"];
  const rows = BT.trades.map((t, i) => [i + 1, t.side, t.entry_ts, t.exit_ts, t.entry, t.exit, t.qty, t.pnl, t.r, t.reason]);
  const csv = [head, ...rows].map(r => r.join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `backtest_${BT.symbol.replace('/', '')}_${BT.timeframe}.csv`; a.click();
}

// ---- analytics ----
function anRange(days) {
  if (!days) { $("an-from").value = ""; $("an-to").value = ""; }
  else { const to = new Date(), from = new Date(Date.now() - days * 864e5); $("an-to").value = to.toISOString().slice(0, 10); $("an-from").value = from.toISOString().slice(0, 10); }
  loadAnalytics();
}
async function loadAnalytics() {
  const since = $("an-from").value ? Math.floor(new Date($("an-from").value).getTime() / 1000) : 0;
  const until = $("an-to").value ? Math.floor(new Date($("an-to").value).getTime() / 1000) + 86400 : 0;
  try {
    const d = await api(`/api/analytics?since=${since}&until=${until}`); const s = d.stats;
    $("an-wr").textContent = fmt(s.win_rate) + "%";
    const p = $("an-pnl"); p.textContent = (s.realized_pnl >= 0 ? "+" : "") + fmt(s.realized_pnl); p.className = "v " + (s.realized_pnl >= 0 ? "pos" : "neg");
    $("an-n").textContent = s.trades; $("an-bw").textContent = fmt(s.best) + " / " + fmt(s.worst);
    drawLine("an-chart", (d.equity || []).map(e => e.balance + e.pnl), "#f97316", true);
    const cs = s.by_symbol || [];
    $("an-coins").innerHTML = cs.length ? cs.map(c => `<tr><td class="mono">${c.symbol}</td><td>${c.trades}</td><td>${fmt(c.win_rate)}%</td><td class="mono ${c.pnl >= 0 ? 'pos' : 'neg'}">${(c.pnl >= 0 ? '+' : '') + fmt(c.pnl, 2)}</td></tr>`).join("") : `<tr><td colspan="4" class="k">No closed trades in range.</td></tr>`;
  } catch (e) { }
}

// ---- line chart ----
function drawLine(id, series, color, fill) {
  const cv = $(id); if (!cv || cv.clientWidth < 20) return; const ctx = cv.getContext("2d");
  const w = cv.width = cv.clientWidth, h = cv.height = cv.clientHeight; ctx.clearRect(0, 0, w, h);
  if (!series || series.length < 2) { ctx.fillStyle = "#8a97ab"; ctx.font = "12px sans-serif"; ctx.fillText("No data yet", 12, 22); return; }
  const min = Math.min(...series), max = Math.max(...series), rng = (max - min) || 1;
  const x = i => 8 + i * (w - 16) / (series.length - 1), y = v => h - 8 - (v - min) / rng * (h - 16);
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath(); series.forEach((v, i) => i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))); ctx.stroke();
  if (fill) { ctx.fillStyle = color + "22"; ctx.lineTo(x(series.length - 1), h - 8); ctx.lineTo(x(0), h - 8); ctx.closePath(); ctx.fill(); }
}

// ============ TradingView Lightweight Charts — live candlestick chart ============
let CH = null;            // { d } last fetched chart data
let LW = null;            // { chart, candle, fast, slow, trend, priceLines:[], bs }
function lwInit() {
  const el = $("ch-price"); if (!el || LW || typeof LightweightCharts === "undefined") return;
  const chart = LightweightCharts.createChart(el, {
    autoSize: true,
    layout: { background: { color: "#0e1420" }, textColor: "#8a97ab", fontFamily: "inherit", fontSize: 13 },
    grid: { vertLines: { color: "#161d2b" }, horzLines: { color: "#161d2b" } },
    rightPriceScale: { borderColor: "#242d3e", scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: "#242d3e", timeVisible: true, secondsVisible: false, rightOffset: 4, barSpacing: 7 },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "#3b4a63", width: 1, style: 2, labelBackgroundColor: "#1a2130" },
      horzLine: { color: "#3b4a63", width: 1, style: 2, labelBackgroundColor: "#1a2130" },
    },
    localization: { priceFormatter: p => p >= 100 ? p.toFixed(2) : p.toFixed(4) },
  });
  const candle = chart.addCandlestickSeries({
    upColor: "#22c55e", downColor: "#ef4444", borderUpColor: "#22c55e",
    borderDownColor: "#ef4444", wickUpColor: "#22c55e", wickDownColor: "#ef4444",
  });
  const mk = c => chart.addLineSeries({ color: c, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  LW = { chart, candle, fast: mk("#eab308"), slow: mk("#3b82f6"), trend: mk("#a855f7"), priceLines: [], bs: 7 };
  chart.subscribeCrosshairMove(p => {
    const o = $("ch-ohlc"); if (!o) return;
    const c = p && p.seriesData && p.seriesData.get(candle);
    if (!c) { o.innerHTML = ""; return; }
    const cls = c.close >= c.open ? "pos" : "neg", ch = ((c.close - c.open) / c.open * 100);
    o.innerHTML = `O <b>${fmt(c.open)}</b> H <b>${fmt(c.high)}</b> L <b>${fmt(c.low)}</b> C <b class="${cls}">${fmt(c.close)}</b> <b class="${cls}">${ch >= 0 ? "+" : ""}${ch.toFixed(2)}%</b>`;
  });
}
function lwLine(d, arr) { const out = []; for (let i = 0; i < d.candles.length; i++) { if (arr[i] == null) continue; out.push({ time: Math.floor(d.candles[i][0] / 1000), value: arr[i] }); } return out; }
function lwRender(d) {
  lwInit(); if (!LW) return;
  LW.candle.setData(d.candles.map(c => ({ time: Math.floor(c[0] / 1000), open: c[1], high: c[2], low: c[3], close: c[4] })));
  LW.fast.setData(lwLine(d, d.fast)); LW.slow.setData(lwLine(d, d.slow)); LW.trend.setData(lwLine(d, d.trend));
  const mks = [];
  (d.markers || []).forEach(m => {
    const c = d.candles[m.i]; if (!c) return; const time = Math.floor(c[0] / 1000);
    if (m.type === "enter") { const buy = m.side === "long"; mks.push({ time, position: buy ? "belowBar" : "aboveBar", color: buy ? "#22c55e" : "#ef4444", shape: buy ? "arrowUp" : "arrowDown", text: buy ? "BUY" : "SELL" }); }
    else if (m.type === "exit") mks.push({ time, position: "aboveBar", color: "#8a97ab", shape: "circle", text: m.reason === "sl" ? "SL hit" : (m.reason === "counter_cross" ? "exit" : "trail") });
  });
  mks.sort((a, b) => a.time - b.time);
  LW.candle.setMarkers(mks);
  LW.priceLines.forEach(pl => LW.candle.removePriceLine(pl)); LW.priceLines = [];
  const le = [...(d.markers || [])].reverse().find(m => m.type === "enter");
  if (le) {
    if (le.sl) LW.priceLines.push(LW.candle.createPriceLine({ price: le.sl, color: "#ef4444", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "SL" }));
    if (le.tp1) LW.priceLines.push(LW.candle.createPriceLine({ price: le.tp1, color: "#22c55e", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "TP" }));
  }
}
async function loadChart() {
  if (!$("ch-price")) return;
  try {
    const d = await api(`/api/candles?symbol=${encodeURIComponent($("ch-sym").value)}&timeframe=${$("ch-tf").value}&limit=1000`);
    const first = !CH; CH = { d }; lwRender(d);
    if (first && LW) LW.chart.timeScale().fitContent();
    const buys = d.markers.filter(m => m.type === "enter" && m.side === "long").length;
    const sells = d.markers.filter(m => m.type === "enter" && m.side === "short").length;
    $("ch-legend").innerHTML = `<span>📊 <b>${d.symbol}</b> · ${d.timeframe}</span> <span class="lg-fast">EMA${d.fast_ema}</span> <span class="lg-slow">EMA${d.slow_ema}</span> <span class="lg-trend">EMA${d.trend_ema}</span> <span class="pos"><b>▲ ${buys} BUY</b></span> <span class="neg"><b>▼ ${sells} SELL</b></span>`;
    // last signal badge: most recent entry (BUY/SELL) + price, for the selected pair
    const ls = $("ch-lastsig"), le = [...d.markers].reverse().find(m => m.type === "enter");
    if (ls) {
      if (le) { const buy = le.side === "long", px = fmt(le.price, le.price < 10 ? 5 : (le.price < 1000 ? 3 : 2)); ls.classList.remove("hidden"); ls.innerHTML = `<span class="k">🎯 Last signal</span> <b class="${buy ? "pos" : "neg"}">${buy ? "▲ BUY" : "▼ SELL"} ${px}</b> <span class="k">${d.symbol}</span>`; }
      else { ls.classList.add("hidden"); ls.innerHTML = ""; }
    }
    renderSignal(d.signal);
  } catch (e) { if ($("ch-legend")) $("ch-legend").innerHTML = '<span class="neg">No chart data — connect an exchange or check connectivity.</span>'; }
}
function renderSignal(sig) {
  const box = $("ch-sig"); if (!box) return;
  if (!sig || sig.pct == null) { box.classList.add("hidden"); return; }
  const pct = Math.max(0, Math.min(100, sig.pct | 0));
  const sell = sig.side === "short";
  const col = sell ? "#ef4444" : "#22c55e";   // BUY side green, SELL side red
  box.classList.remove("hidden"); box.classList.toggle("ready", pct >= 85);
  box.classList.toggle("sell", sell);
  const fill = $("ch-sig-fill"); if (fill) { fill.style.width = pct + "%"; fill.style.background = col; }
  const p = $("ch-sig-pct"); if (p) { p.textContent = pct + "%"; p.style.color = "#fff"; }
  const st = $("ch-sig-state"); if (st) st.textContent = sig.state || "";
}
let newsBusy = false;
function renderNews(nw) {
  const btn = $("ch-news-btn"), lbl = $("ch-news-lbl"), flag = $("ch-news-flag"), box = $("ch-news");
  if (!btn || !box) return;
  box.classList.remove("hidden");
  // trading ON = protection OFF; protection ON = user turned news trading OFF
  const on = !(nw && nw.protect);
  btn.classList.toggle("off", !on);
  btn.setAttribute("aria-checked", on ? "true" : "false");
  if (lbl) lbl.textContent = on ? "ON" : "OFF";
  if (!flag) return;
  if (nw && nw.protect && nw.event) {                 // protection on + inside a news window
    const e = nw.event, cc = e.country ? ` ${e.country}` : "";
    const when = e.phase === "pre" ? `in ${e.mins}m` : `${Math.abs(e.mins)}m ago`;
    flag.className = "ch-news-flag paused";
    flag.textContent = `⛔ Paused — ${e.title}${cc} ${when}`;
  } else if (nw && nw.protect && nw.next) {            // protection on, next event ahead
    const e = nw.next, cc = e.country ? ` ${e.country}` : "";
    const h = Math.floor(e.mins / 60), m = e.mins % 60, eta = h ? `${h}h ${m}m` : `${m}m`;
    flag.className = "ch-news-flag armed";
    flag.textContent = nw.stale ? "⚠ calendar offline" : `🛡 Next: ${e.title}${cc} in ${eta}`;
  } else {
    flag.className = "ch-news-flag hidden";
    flag.textContent = "";
  }
}
async function toggleNews() {
  if (newsBusy) return; newsBusy = true;
  const cur = !(lastState && lastState.news && lastState.news.protect);  // currently ON?
  const next = !cur;                                                     // flip
  try {
    await api("/api/settings", "POST", { news_trading: next });
    if (lastState) { lastState.news = Object.assign({}, lastState.news, { protect: !next }); renderNews(lastState.news); }
    notify(next ? "News trading ON — bot trades through all events." :
                  "News trading OFF — bot pauses ±1h around high-impact news. 🛡", "ok");
  } catch (e) { notify("Could not update news filter.", "err"); }
  finally { newsBusy = false; }
}
function chReset() { if (LW) { LW.bs = 7; LW.chart.timeScale().applyOptions({ barSpacing: 7 }); LW.chart.timeScale().fitContent(); } }
function chZoom(f) { if (!LW) return; LW.bs = Math.max(2, Math.min(50, (LW.bs || 7) / f)); LW.chart.timeScale().applyOptions({ barSpacing: LW.bs }); }

// order-panel mark price for the selected symbol
async function markTick() {
  const el = $("tr-sym"); if (!el) return; const sym = el.value;
  try { const d = await api("/api/prices?symbols=" + encodeURIComponent(sym)); const t = d[sym]; if ($("tr-mark")) $("tr-mark").value = t && t.price != null ? fmt(t.price, t.price < 10 ? 5 : 2) : "—"; } catch (e) { }
}

// Lightweight Charts manages its own pan/zoom/crosshair — just init the chart.
function chSetup() { lwInit(); }

function btRedraw() {
  if (!BT || $("bt-out").classList.contains("hidden")) return;
  const eq = BT.equity.map(e => e[1]); drawLine("bt-eqc", eq, "#f97316", true);
  let peak = -1e9; drawLine("bt-ddc", eq.map(v => { peak = Math.max(peak, v); return peak > 0 ? -(peak - v) / peak * 100 : 0; }), "#ef4444", true);
}
// Redraw canvases when the tab becomes visible again (Chrome memory-saver can
// discard canvas contents while a tab is frozen).
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  requestAnimationFrame(() => { btRedraw(); });
});

// unified toast notifications for every action
// ---- PWA + web push ----
let pushActive = false;
function urlB64ToUint8(base64) {
  const pad = "=".repeat((4 - base64.length % 4) % 4);
  const b = (base64 + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b); const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}
function registerSW() { if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { }); }
async function subscribePush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
  try {
    const reg = await navigator.serviceWorker.ready;
    const v = await api("/api/push/vapid");
    if (!v.key) return;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8(v.key) });
    await api("/api/push/subscribe", "POST", { subscription: sub.toJSON() });
    pushActive = true;
  } catch (e) { }
}
async function unsubscribePush() {
  pushActive = false;
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (!sub) return;
    await api("/api/push/unsubscribe", "POST", { endpoint: sub.endpoint }).catch(() => { });
    await sub.unsubscribe().catch(() => { });
  } catch (e) { }
}
// ---- security: login history + sign out everywhere ----
async function loadServerIP() {
  const el = $("srv-ip"); if (!el) return;
  try { const d = await api("/api/server_ip"); el.value = d.ip || "Not detected \u2014 IP whitelist is optional"; }
  catch (e) { el.value = "Unavailable"; }
}
function copyServerIP() {
  const el = $("srv-ip"); if (!el || !el.value) return;
  el.select();
  if (navigator.clipboard) navigator.clipboard.writeText(el.value).then(() => notify("Server IP copied \u2713", "ok"), () => { });
  else { try { document.execCommand("copy"); notify("Copied", "ok"); } catch (e) { } }
}
async function loadLogins() {
  try {
    const d = await api("/api/security/logins"); const rows = d.logins || [];
    $("login-rows").innerHTML = rows.length ? rows.map(r =>
      `<tr><td class="k">${new Date(r.ts * 1000).toLocaleString()}</td><td class="mono">${esc(r.ip || "\u2014")}</td><td class="k">${esc((r.ua || "").slice(0, 42))}</td></tr>`).join("")
      : `<tr><td colspan="3" class="k">No sign-ins recorded yet.</td></tr>`;
  } catch (e) { }
}
async function logoutAll() {
  if (!confirm("Sign out of all other devices? You'll stay signed in here.")) return;
  try {
    const d = await api("/api/security/logout_all", "POST");
    if (d.token) { TOKEN = d.token; localStorage.setItem("trevolto_token", TOKEN); }
    notify("Signed out all other devices \u2713", "ok");
  } catch (e) { notify(e.message, "error"); }
}
function notify(msg, type = "ok") {
  const wrap = $("toasts"); if (!wrap) { console.log(type + ": " + msg); return; }
  const icon = type === "error" ? "⚠️" : type === "warn" ? "⚠️" : "✅";
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.innerHTML = `<span>${icon}</span><span class="tx">${esc(msg)}</span>`;
  el.onclick = () => el.remove();
  wrap.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateX(24px)"; setTimeout(() => el.remove(), 320); }, 3400);
}
function toast(msg) { notify(msg, "ok"); }

// ---- social-proof purchase popup (shown to trial/expired users only) ----
let spSales = [], spIdx = 0, spTimer = null, spOn = false, spDismissed = false, spLoaded = false;
function spAgo(sec) {
  sec = Math.max(0, sec | 0);
  if (sec < 3600) return Math.max(1, Math.floor(sec / 60)) + " min ago";
  if (sec < 86400) { const h = Math.floor(sec / 3600); return h + (h === 1 ? " hour ago" : " hours ago"); }
  const d = Math.floor(sec / 86400); return d + (d === 1 ? " day ago" : " days ago");
}
const SP_SEED = [
  { name: "Michael", country: "United States", ago: 7200 }, { name: "Oliver", country: "United Kingdom", ago: 1500 },
  { name: "Liam", country: "Canada", ago: 432000 }, { name: "Jason", country: "United States", ago: 2400 },
  { name: "Charlotte", country: "United Kingdom", ago: 10800 }, { name: "Ethan", country: "Canada", ago: 90000 },
  { name: "Emily", country: "United States", ago: 3300 }, { name: "Lukas", country: "Germany", ago: 14400 },
  { name: "Mateo", country: "Spain", ago: 1800 }, { name: "Emma", country: "France", ago: 259200 },
  { name: "Arjun", country: "India", ago: 900 }, { name: "Kenji", country: "Japan", ago: 25200 },
  { name: "Wei", country: "Singapore", ago: 100800 }, { name: "Aisha", country: "United Arab Emirates", ago: 18000 }
];
async function loadSocialProof() {
  try { const d = await api("/api/social_proof"); spSales = (d && d.enabled && Array.isArray(d.sales)) ? d.sales : []; }
  catch (e) { spSales = []; }
  if (!spSales.length) spSales = SP_SEED;   // starter list until real sales exist, so it's never empty
}
async function maybeSocialProof(eligible) {
  const force = /[?&]sp=1/.test(location.search);   // add ?sp=1 to the URL to preview on any account
  if ((!eligible && !force) || spDismissed) { stopSocialProof(); return; }
  if (spOn) return;                          // already running for this session
  spOn = true;
  if (!spLoaded) { await loadSocialProof(); spLoaded = true; }
  spCycle();
}
function stopSocialProof() {
  spOn = false;
  if (spTimer) { clearTimeout(spTimer); spTimer = null; }
  const box = $("social-proof"); if (box) box.classList.add("hidden");
}
function spCycle() {
  if (!spOn || spDismissed) return;
  const box = $("social-proof"); if (!box) return;
  if (!spSales.length) { box.classList.add("hidden"); return; }
  const s = spSales[spIdx % spSales.length];
  spIdx++;
  if (spIdx % spSales.length === 0) loadSocialProof();     // refresh list on wrap (picks up new sales)
  const loc = s.country ? ` from ${esc(s.country)}` : "";
  box.innerHTML = `<img class="sp-logo" src="/static/icon-192.png" alt=""/>` +
    `<div class="sp-body"><div class="sp-line"><b>${esc(s.name)}</b>${loc}</div>` +
    `<div class="sp-sub">just purchased! · ${esc(spAgo(s.ago))}</div></div>` +
    `<button class="sp-x" onclick="dismissSocialProof()" aria-label="Dismiss">×</button>`;
  box.classList.remove("hidden");
  requestAnimationFrame(() => box.classList.add("show"));
  spTimer = setTimeout(() => {                                // visible ~6.5s, then slide out
    box.classList.remove("show");
    setTimeout(() => box.classList.add("hidden"), 400);      // finish slide-out then hide
    spTimer = setTimeout(spCycle, 20000 + Math.floor(Math.random() * 8000));  // 20–28s until next
  }, 6500);
}
function dismissSocialProof() { spDismissed = true; stopSocialProof(); }
window.addEventListener("load", chSetup);
checkResetUrl();
if (TOKEN && !RESET_TOKEN) enterApp();
