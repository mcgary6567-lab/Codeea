// Prometheus Web — dashboard client
let TOKEN = localStorage.getItem("prometheus_token") || "";
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
function authTab(m) { authMode = m; $("tab-login").classList.toggle("on", m === "login"); $("tab-reg").classList.toggle("on", m === "reg"); }
async function doAuth() {
  $("au-err").textContent = "";
  try {
    const d = await api("/api/" + (authMode === "reg" ? "register" : "login"), "POST",
      { email: $("au-email").value.trim(), password: $("au-pass").value });
    TOKEN = d.token; localStorage.setItem("prometheus_token", TOKEN); enterApp();
  } catch (e) { $("au-err").textContent = e.message; }
}
function logout() { localStorage.removeItem("prometheus_token"); TOKEN = ""; if (ws) ws.close(); $("app").classList.add("hidden"); $("landing").classList.remove("hidden"); }
function enterApp() { $("landing").classList.add("hidden"); $("app").classList.remove("hidden"); refresh(); openWs(); }

// ---- views ----
const VIEWS = ["home", "chart", "strategy", "backtest", "analytics", "log", "settings"];
function show(v, btn) {
  VIEWS.forEach(x => $("v-" + x).classList.toggle("hidden", x !== v));
  document.querySelectorAll(".side button").forEach(b => b.classList.toggle("on", b === btn));
  if (v === "analytics") loadAnalytics();
  if (v === "chart") loadChart();
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
setInterval(() => { if (TOKEN && !ws) refresh(); }, 3000);

function render(s) {
  lastState = s;
  $("st-dot").classList.toggle("on", s.connected);
  $("st-conn").textContent = s.connected ? "Connected" : "Disconnected";
  $("st-ex").textContent = s.exchange ? `${s.exchange} · ${s.market_type}` : "—";
  $("st-safe").classList.toggle("hidden", !s.safe_mode);
  $("st-ro").classList.toggle("hidden", !s.read_only);
  $("st-halt").classList.toggle("hidden", !s.guard_tripped);
  $("st-email").textContent = s.email || "";
  $("st-admin").classList.toggle("hidden", !s.is_admin);

  // access (fixed: WS now carries it too)
  const ac = s.access || {};
  const lbl = { admin: "ADMIN", licensed: "LICENSED", trial: "TRIAL", suspended: "SUSPENDED", expired: "EXPIRED" }[ac.status] || "—";
  const daySuffix = (ac.days_left != null && (ac.status === "trial" || ac.status === "licensed")) ? ` · ${ac.days_left}d` : "";
  $("st-access").textContent = lbl + daySuffix;
  $("st-access").className = "chip " + (ac.ok ? (ac.status === "trial" ? "warn" : "safe") : "danger");
  const warn = $("access-warn");
  if (ac.status && !ac.ok) {
    warn.classList.remove("hidden");
    warn.textContent = ac.status === "suspended" ? "Your account is suspended. Contact support."
      : "Your trial has ended. A licence is required to connect and trade — contact support to activate.";
  } else warn.classList.add("hidden");

  // tiles
  $("t-bal").textContent = fmt(s.balance);
  const pnl = $("t-pnl"); pnl.textContent = (s.pnl >= 0 ? "+" : "") + fmt(s.pnl); pnl.className = "v mono " + (s.pnl >= 0 ? "pos" : "neg");
  $("t-access").textContent = lbl;
  $("t-accessd").textContent = ac.days_left != null ? `${ac.days_left} days left` : (ac.status || "");
  $("t-strat").textContent = s.strategy_on ? "Running" : "Off";
  $("t-strat").className = "v " + (s.strategy_on ? "pos" : "");
  $("strat-state").textContent = s.strategy_on ? "running" : "off";
  $("tr-mode").textContent = (s.settings || {}).sizing_mode || "—";

  // positions
  const tb = $("pos-body");
  if (!s.positions || !s.positions.length) tb.innerHTML = `<tr><td colspan="7" class="k">No open positions.</td></tr>`;
  else tb.innerHTML = s.positions.map(p => `<tr>
    <td class="mono">${p.pair}</td><td class="${p.side === 'Long' ? 'pos' : 'neg'}">${p.side}</td>
    <td class="mono">${fmt(p.size, 6)}</td><td class="mono">${fmt(p.entry, 4)}</td><td class="mono">${fmt(p.current, 4)}</td>
    <td class="mono ${p.pnl >= 0 ? 'pos' : 'neg'}">${(p.pnl >= 0 ? '+' : '') + fmt(p.pnl, 4)}</td>
    <td><button class="btn ghost sm" onclick="closePos('${p.pair}')">Close</button></td></tr>`).join("");

  // log
  $("log").innerHTML = (s.log || []).map(l => {
    const t = new Date(l.ts * 1000).toLocaleTimeString();
    return `<div class="l"><span class="t">${t}</span> <span class="${l.level}">${esc(l.msg)}</span></div>`;
  }).join("");

  applySettings(s.settings || {}, s.email);
}

// ---- connect / trade ----
async function saveConnect() {
  try {
    await api("/api/keys", "POST", { exchange: $("cx-ex").value, market_type: $("cx-mkt").value, api_key: $("cx-key").value.trim(), api_secret: $("cx-sec").value.trim(), password: $("cx-pass").value.trim() });
    await api("/api/connect", "POST"); refresh();
  } catch (e) { alert("Connect failed: " + e.message); }
}
async function trade(side) {
  const size = parseFloat($("tr-size").value);
  try { const r = await api("/api/trade", "POST", { side, symbol: $("tr-sym").value, size: isNaN(size) ? null : size }); if (!r.ok) alert(r.message); refresh(); }
  catch (e) { alert(e.message); }
}
async function closePos(sym) { if (!confirm("Close " + sym + "?")) return; await api("/api/close", "POST", { symbol: sym }); refresh(); }
async function panic() { if (!confirm("Close ALL positions now?")) return; await api("/api/close_all", "POST"); refresh(); }

// ---- settings ----
function applySettings(s, email) {
  if (document.activeElement && ["INPUT", "SELECT"].includes(document.activeElement.tagName)) return;
  const set = (id, v) => { if ($(id) != null && v !== undefined) $(id).value = v; };
  const chk = (id, v) => { if ($(id) != null) $(id).checked = !!v; };
  set("s-sizing", s.sizing_mode); set("s-fixed", s.fixed_size); set("s-fixedq", s.fixed_quote); set("s-risk", s.risk_percent);
  set("s-otype", s.order_type); set("s-lev", s.leverage); set("s-margin", s.margin_mode); set("s-tp1f", s.tp1_fraction);
  chk("s-bracket", s.auto_bracket); chk("s-safe", s.safe_mode); chk("s-ro", s.read_only);
  set("s-maxopen", s.max_open); set("s-dloss", s.daily_loss); set("s-dprofit", s.daily_profit); set("s-cool", s.cooldown); set("s-dedupe", s.dedupe);
  set("sg-sym", s.strategy_symbols); if (s.strategy_timeframe) set("sg-tf", s.strategy_timeframe);
  set("tg-token", s.telegram_token); set("tg-chat", s.telegram_chat);
  set("ac-email", email);
}
async function saveSettings() {
  const num = id => parseFloat($(id).value) || 0;
  await api("/api/settings", "POST", {
    sizing_mode: $("s-sizing").value, fixed_size: num("s-fixed"), fixed_quote: num("s-fixedq"), risk_percent: num("s-risk"),
    order_type: $("s-otype").value, leverage: num("s-lev"), margin_mode: $("s-margin").value, tp1_fraction: num("s-tp1f"),
    auto_bracket: $("s-bracket").checked, safe_mode: $("s-safe").checked, read_only: $("s-ro").checked,
    max_open: num("s-maxopen"), daily_loss: num("s-dloss"), daily_profit: num("s-dprofit"), cooldown: num("s-cool"), dedupe: num("s-dedupe")
  });
  refresh(); toast("Settings saved");
}
async function saveTelegram() {
  await api("/api/settings", "POST", { telegram_token: $("tg-token").value.trim(), telegram_chat: $("tg-chat").value.trim() });
  toast("Telegram saved");
}
async function saveAccount() {
  $("ac-err").textContent = "";
  const body = { current_password: $("ac-cur").value, new_email: $("ac-email").value.trim(), new_password: $("ac-newpass").value };
  if (!body.current_password) { $("ac-err").textContent = "Enter your current password to save."; return; }
  try {
    const d = await api("/api/account", "POST", body);
    TOKEN = d.token; localStorage.setItem("prometheus_token", TOKEN);
    $("ac-newpass").value = ""; $("ac-cur").value = ""; toast("Account updated"); refresh();
  } catch (e) { $("ac-err").textContent = e.message; }
}
function addSym(sym) {
  const el = $("sg-sym");
  const list = el.value.split(",").map(x => x.trim()).filter(Boolean);
  if (!list.includes(sym)) list.push(sym);
  el.value = list.join(", ");
}

// ---- strategy ----
async function strategy(enable) {
  try { await api("/api/strategy", "POST", { enable, symbols: $("sg-sym").value, timeframe: $("sg-tf").value }); refresh(); }
  catch (e) { alert(e.message); }
}

// ---- backtest (pro) ----
async function runBacktest() {
  $("bt-out").classList.add("hidden");
  try {
    const d = await api("/api/backtest", "POST", {
      exchange: $("bt-ex").value, symbol: $("bt-sym").value, timeframe: $("bt-tf").value, limit: parseInt($("bt-limit").value) || 1000,
      start_equity: parseFloat($("bt-eq").value) || 1000, fee_pct: parseFloat($("bt-fee").value),
      allow_short: $("bt-short").value === "1",
      params: {
        rsi_len: parseInt($("bt-rsi").value) || 14, ma_len: parseInt($("bt-ema").value) || 20,
        ma_tp1_r: parseFloat($("bt-tp1").value), ma_tp2_r: parseFloat($("bt-tp2").value), ma_confirm: parseInt($("bt-conf").value)
      }
    });
    const s = d.summary, ret = s.return_pct, vs = ret - d.buy_hold_pct;
    const tiles = [
      ["Return", (ret >= 0 ? "+" : "") + fmt(ret) + "%", ret >= 0 ? "pos" : "neg"],
      ["vs Buy & Hold", (vs >= 0 ? "+" : "") + fmt(vs) + "%", vs >= 0 ? "pos" : "neg"],
      ["Net PnL", fmt(s.net_pnl), s.net_pnl >= 0 ? "pos" : "neg"],
      ["Win rate", fmt(s.win_rate) + "%", ""],
      ["Profit factor", (s.profit_factor > 999 ? "∞" : fmt(s.profit_factor)), ""],
      ["Max drawdown", "-" + fmt(s.max_drawdown) + "%", "neg"],
      ["Trades", s.trades + ` (${s.longs}L/${s.shorts}S)`, ""],
      ["Avg R", fmt(s.avg_r), s.avg_r >= 0 ? "pos" : "neg"],
    ];
    $("bt-tiles").innerHTML = tiles.map(([k, v, c]) => `<div class="tile"><div class="k">${k}</div><div class="v ${c}" style="font-size:19px">${v}</div></div>`).join("");
    const eq = d.equity.map(e => e[1]);
    drawLine("bt-eqc", eq, "#f97316", true);
    // drawdown %
    let peak = -1e9; const dd = eq.map(v => { peak = Math.max(peak, v); return peak > 0 ? -(peak - v) / peak * 100 : 0; });
    drawLine("bt-ddc", dd, "#ef4444", true);
    $("bt-ntr").textContent = d.trades.length;
    $("bt-trades").innerHTML = d.trades.slice().reverse().map((t, i) => `<tr>
      <td>${d.trades.length - i}</td><td class="${t.side === 'long' ? 'pos' : 'neg'}">${t.side}</td>
      <td class="mono">${fmt(t.entry, 4)}</td><td class="mono">${fmt(t.exit, 4)}</td><td class="mono">${fmt(t.qty, 5)}</td>
      <td class="mono ${t.pnl >= 0 ? 'pos' : 'neg'}">${(t.pnl >= 0 ? '+' : '') + fmt(t.pnl, 2)}</td>
      <td class="mono ${t.r >= 0 ? 'pos' : 'neg'}">${fmt(t.r, 2)}</td><td class="k">${t.reason}</td></tr>`).join("");
    $("bt-out").classList.remove("hidden");
  } catch (e) { alert("Backtest: " + e.message); }
}

// ---- analytics ----
async function loadAnalytics() {
  try {
    const d = await api("/api/analytics"); const s = d.stats;
    $("an-wr").textContent = fmt(s.win_rate) + "%";
    const p = $("an-pnl"); p.textContent = (s.realized_pnl >= 0 ? "+" : "") + fmt(s.realized_pnl); p.className = "v " + (s.realized_pnl >= 0 ? "pos" : "neg");
    $("an-n").textContent = s.trades; $("an-bw").textContent = fmt(s.best) + " / " + fmt(s.worst);
    drawLine("an-chart", (d.equity || []).map(e => e.balance + e.pnl), "#f97316", true);
  } catch (e) { }
}

// ---- charts ----
function drawLine(id, series, color, fill) {
  const cv = $(id); if (!cv) return; const ctx = cv.getContext("2d");
  const w = cv.width = cv.clientWidth, h = cv.height = cv.clientHeight; ctx.clearRect(0, 0, w, h);
  if (!series || series.length < 2) { ctx.fillStyle = "#8a97ab"; ctx.font = "12px sans-serif"; ctx.fillText("No data yet", 12, 22); return; }
  const min = Math.min(...series), max = Math.max(...series), rng = (max - min) || 1;
  const x = i => 8 + i * (w - 16) / (series.length - 1), y = v => h - 8 - (v - min) / rng * (h - 16);
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
  series.forEach((v, i) => i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))); ctx.stroke();
  if (fill) { ctx.fillStyle = color + "22"; ctx.lineTo(x(series.length - 1), h - 8); ctx.lineTo(x(0), h - 8); ctx.closePath(); ctx.fill(); }
}

async function loadChart() {
  $("ch-info").textContent = "loading…";
  try {
    const d = await api(`/api/candles?symbol=${encodeURIComponent($("ch-sym").value)}&timeframe=${$("ch-tf").value}&limit=300`);
    $("ch-info").textContent = `${d.candles.length} candles · ${d.markers.length} signals`;
    drawCandles(d); drawRsi(d);
  } catch (e) { $("ch-info").textContent = "no data — connect an exchange"; }
}
function drawCandles(d) {
  const cv = $("ch-price"), ctx = cv.getContext("2d");
  const w = cv.width = cv.clientWidth, h = cv.height = cv.clientHeight; ctx.clearRect(0, 0, w, h);
  const c = d.candles; if (!c.length) return;
  const lo = Math.min(...c.map(x => x[3])), hi = Math.max(...c.map(x => x[2])), rng = (hi - lo) || 1;
  const padL = 8, padR = 58, plotW = w - padL - padR;
  const x = i => padL + i * plotW / (c.length - 1), y = v => 6 + (hi - v) / rng * (h - 12);
  const cw = Math.max(1, plotW / c.length * 0.6);
  c.forEach((k, i) => {
    const [, o, hg, l, cl] = k, up = cl >= o; ctx.strokeStyle = up ? "#22c55e" : "#ef4444"; ctx.fillStyle = ctx.strokeStyle;
    ctx.beginPath(); ctx.moveTo(x(i), y(hg)); ctx.lineTo(x(i), y(l)); ctx.stroke();
    const yt = y(Math.max(o, cl)), yb = y(Math.min(o, cl)); ctx.fillRect(x(i) - cw / 2, yt, cw, Math.max(1, yb - yt));
  });
  ctx.strokeStyle = "#f59e0b"; ctx.lineWidth = 1.5; ctx.beginPath(); let started = false;
  d.ema.forEach((v, i) => { if (v == null) return; const px = x(i), py = y(v); started ? ctx.lineTo(px, py) : ctx.moveTo(px, py); started = true; });
  ctx.stroke(); ctx.lineWidth = 1;
  ctx.fillStyle = "#8a97ab"; ctx.font = "11px monospace";
  for (let g = 0; g <= 4; g++) { const v = lo + rng * g / 4; ctx.fillText(v.toFixed(2), w - padR + 5, y(v) + 3); }
  // BUY/SELL arrows + labels
  ctx.font = "bold 11px sans-serif";
  d.markers.forEach(m => {
    const px = x(m.i);
    if (m.type === "enter") {
      const buy = m.side === "long"; const col = buy ? "#22c55e" : "#ef4444"; ctx.fillStyle = col; ctx.strokeStyle = col;
      const py = y(m.price);
      ctx.beginPath();
      if (buy) { ctx.moveTo(px, py + 14); ctx.lineTo(px - 6, py + 26); ctx.lineTo(px + 6, py + 26); }
      else { ctx.moveTo(px, py - 14); ctx.lineTo(px - 6, py - 26); ctx.lineTo(px + 6, py - 26); }
      ctx.closePath(); ctx.fill();
      const label = buy ? "BUY" : "SELL"; const tw = ctx.measureText(label).width;
      const ly = buy ? py + 40 : py - 32, lx = Math.min(Math.max(px - tw / 2 - 4, 2), w - padR - tw - 8);
      ctx.fillStyle = col; roundRect(ctx, lx, ly - 12, tw + 8, 16, 4); ctx.fill();
      ctx.fillStyle = "#0a0d13"; ctx.fillText(label, lx + 4, ly);
      if (m.sl) { ctx.strokeStyle = "rgba(239,68,68,.5)"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(px, y(m.sl)); ctx.lineTo(Math.min(px + 45, w - padR), y(m.sl)); ctx.stroke(); ctx.setLineDash([]); }
    } else { ctx.fillStyle = "#8a97ab"; ctx.fillRect(px - 2, y(m.price) - 2, 4, 4); }
  });
}
function roundRect(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
function drawRsi(d) {
  const cv = $("ch-rsi"), ctx = cv.getContext("2d");
  const w = cv.width = cv.clientWidth, h = cv.height = cv.clientHeight; ctx.clearRect(0, 0, w, h);
  const r = d.rsi; if (!r.length) return;
  const padR = 58, plotW = w - 8 - padR, x = i => 8 + i * plotW / (r.length - 1), y = v => 4 + (100 - v) / 100 * (h - 8);
  ctx.fillStyle = "rgba(59,130,246,.08)"; ctx.fillRect(8, y(70), plotW, y(30) - y(70));
  ctx.strokeStyle = "#242d3e";[30, 50, 70].forEach(v => { ctx.beginPath(); ctx.moveTo(8, y(v)); ctx.lineTo(8 + plotW, y(v)); ctx.stroke(); });
  ctx.strokeStyle = "#3b82f6"; ctx.beginPath(); let st = false;
  r.forEach((v, i) => { if (v == null) return; const px = x(i), py = y(v); st ? ctx.lineTo(px, py) : ctx.moveTo(px, py); st = true; });
  ctx.stroke(); ctx.fillStyle = "#8a97ab"; ctx.font = "10px monospace";[30, 50, 70].forEach(v => ctx.fillText(v, w - padR + 5, y(v) + 3));
}

function toast(msg) {
  const e = $("st-email"); const old = e.textContent; e.textContent = "✓ " + msg;
  setTimeout(() => { if (lastState) e.textContent = lastState.email; }, 1500);
}

if (TOKEN) enterApp();
