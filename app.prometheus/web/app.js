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
function authTab(m) { authMode = m; $("tab-login").classList.toggle("on", m === "login"); $("tab-reg").classList.toggle("on", m === "reg"); $("reg-fields").classList.toggle("hidden", m !== "reg"); }
async function doAuth() {
  $("au-err").textContent = "";
  try {
    const body = { email: $("au-email").value.trim(), password: $("au-pass").value };
    if (authMode === "reg") { body.first_name = $("au-first").value.trim(); body.last_name = $("au-last").value.trim(); }
    const d = await api("/api/" + (authMode === "reg" ? "register" : "login"), "POST", body);
    TOKEN = d.token; localStorage.setItem("prometheus_token", TOKEN); enterApp();
  } catch (e) { $("au-err").textContent = e.message; }
}
function logout() { localStorage.removeItem("prometheus_token"); TOKEN = ""; if (ws) ws.close(); $("app").classList.add("hidden"); $("landing").classList.remove("hidden"); }
function enterApp() {
  $("landing").classList.add("hidden"); $("app").classList.remove("hidden");
  const foll = localStorage.getItem("ch_follow") === "1";
  $("ch-follow").checked = foll; $("ch-sym").disabled = foll; $("ch-tf").disabled = foll;
  refresh(); openWs(); loadChart(); pollPrices();
}

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
  const tf = s.strategy_timeframe || "1h";
  let changed = false;
  if ($("ch-sym").value !== sym) { $("ch-sym").value = sym; changed = true; }
  if ($("ch-tf").value !== tf) { $("ch-tf").value = tf; changed = true; }
  if (forceLoad || changed) loadChart();
}

// ---- views ----
const VIEWS = ["home", "exchange", "strategy", "backtest", "analytics", "log", "settings"];
function show(v, btn) {
  VIEWS.forEach(x => $("v-" + x).classList.toggle("hidden", x !== v));
  document.querySelectorAll(".side button").forEach(b => b.classList.toggle("on", b === btn));
  document.querySelector(".side").classList.remove("open");
  if (v === "analytics") loadAnalytics();
  if (v === "home") loadChart();
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
  $("ex-state").textContent = s.connected ? "connected" : "not connected";
  $("st-ro").classList.toggle("hidden", !s.read_only);
  $("st-halt").classList.toggle("hidden", !s.guard_tripped);
  $("st-paper").classList.toggle("hidden", !s.paper_mode);
  const name = s.name || (s.email || "").split("@")[0];
  $("st-email").textContent = "👋 " + name;
  $("welcome").textContent = "Welcome back, " + name + " 👋";
  $("st-admin").classList.toggle("hidden", !s.is_admin);
  const setIf = (id, v) => { if ($(id) && !(document.activeElement && document.activeElement.id === id)) $(id).value = v || ""; };
  setIf("ac-first", s.first_name); setIf("ac-last", s.last_name);

  const ac = s.access || {};
  const lbl = { admin: "ADMIN", licensed: "LICENSED", trial: "TRIAL", suspended: "SUSPENDED", expired: "EXPIRED" }[ac.status] || "—";
  const suf = (ac.days_left != null && (ac.status === "trial" || ac.status === "licensed")) ? ` · ${ac.days_left}d` : "";
  $("st-access").textContent = lbl + suf;
  $("st-access").className = "chip " + (ac.ok ? (ac.status === "trial" ? "warn" : "safe") : "danger");
  const warn = $("access-warn");
  if (ac.status && !ac.ok) { warn.classList.remove("hidden"); warn.textContent = ac.status === "suspended" ? "Your account is suspended. Contact support." : "Your trial has ended. A licence is required to connect and trade — contact support."; }
  else warn.classList.add("hidden");

  $("t-bal").textContent = fmt(s.balance);
  const pnl = $("t-pnl"); pnl.textContent = (s.pnl >= 0 ? "+" : "") + fmt(s.pnl); pnl.className = "v mono " + (s.pnl >= 0 ? "pos" : "neg");
  $("t-access").textContent = lbl; $("t-accessd").textContent = ac.days_left != null ? `${ac.days_left} days left` : (ac.status || "");
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
    <td><button class="btn ghost sm" onclick="closePos('${p.pair}')">Close</button></td></tr>`).join("");

  $("log").innerHTML = (s.log || []).map(l => `<div class="l"><span class="t">${new Date(l.ts * 1000).toLocaleTimeString()}</span> <span class="${l.level}">${esc(l.msg)}</span></div>`).join("");
  applySettings(s.settings || {}, s.email);
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
    await api("/api/keys", "POST", { exchange: $("cx-ex").value, market_type: $("cx-mkt").value, api_key: $("cx-key").value.trim(), api_secret: $("cx-sec").value.trim(), password: $("cx-pass").value.trim() });
    await api("/api/connect", "POST"); refresh(); show('home', document.querySelector('.side button'));
  } catch (e) { alert("Connect failed: " + e.message); }
}
async function trade(side) {
  const size = parseFloat($("tr-size").value);
  if (!confirm(`${side.toUpperCase()} ${$("tr-sym").value} — LIVE order. Continue?`)) return;
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
  chk("s-bracket", s.auto_bracket); chk("s-ro", s.read_only); chk("s-paper", s.paper_mode);
  set("s-maxopen", s.max_open); set("s-dloss", s.daily_loss); set("s-dprofit", s.daily_profit); set("s-cool", s.cooldown); set("s-dedupe", s.dedupe);
  mselSet(s.strategy_symbols || "BTC/USDT"); if (s.strategy_timeframe) set("sg-tf", s.strategy_timeframe);
  set("tg-token", s.telegram_token); set("tg-chat", s.telegram_chat); set("ac-email", email);
  const p = s.strategy_params || {};
  for (const k in SPARAMS) { const v = p[k] !== undefined ? p[k] : SPARAMS[k]; if (BOOLP.has(k)) set("p-" + k, v ? "1" : "0"); else set("p-" + k, v); }
}
async function saveSettings() {
  const num = id => parseFloat($(id).value) || 0;
  await api("/api/settings", "POST", { sizing_mode: $("s-sizing").value, fixed_size: num("s-fixed"), fixed_quote: num("s-fixedq"), risk_percent: num("s-risk"), order_type: $("s-otype").value, leverage: num("s-lev"), margin_mode: $("s-margin").value, tp1_fraction: num("s-tp1f"), auto_bracket: $("s-bracket").checked, read_only: $("s-ro").checked, paper_mode: $("s-paper").checked, max_open: num("s-maxopen"), daily_loss: num("s-dloss"), daily_profit: num("s-dprofit"), cooldown: num("s-cool"), dedupe: num("s-dedupe") });
  refresh(); toast("Settings saved");
}
async function saveTelegram() { await api("/api/settings", "POST", { telegram_token: $("tg-token").value.trim(), telegram_chat: $("tg-chat").value.trim() }); toast("Telegram saved"); }
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
  try { const d = await api("/api/account", "POST", b); TOKEN = d.token; localStorage.setItem("prometheus_token", TOKEN); $("ac-newpass").value = ""; $("ac-cur").value = ""; toast("Account updated"); refresh(); }
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
const SPARAMS = { fast_ema: 9, slow_ema: 21, trend_ema: 100, use_trend_filter: 1, confirm: 1, min_body: 0.4, sl_ema_buffer_pct: 0.2, swing_lookback: 10, tp_r: 2.0, partial_pct: 0.5, whipsaw_max_crosses: 2, whipsaw_window: 5, whipsaw_suspend_hours: 12, avoid_daily_close: 1 };
const INTP = new Set(["fast_ema", "slow_ema", "trend_ema", "confirm", "swing_lookback", "whipsaw_max_crosses", "whipsaw_window"]);
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
async function strategy(enable) { try { await api("/api/strategy", "POST", { enable, symbols: $("sg-sym").value, timeframe: $("sg-tf").value, params: collectParams() }); refresh(); } catch (e) { alert(e.message); } }
async function saveStrategy() { try { await api("/api/strategy", "POST", { params: collectParams(), symbols: $("sg-sym").value, timeframe: $("sg-tf").value }); toast("Strategy saved"); refresh(); } catch (e) { alert(e.message); } }

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
    $("bt-trades").innerHTML = d.trades.slice().reverse().map((t, i) => `<tr><td>${d.trades.length - i}</td><td class="${t.side === 'long' ? 'pos' : 'neg'}">${t.side}</td><td class="mono">${fmt(t.entry, 4)}</td><td class="mono">${fmt(t.exit, 4)}</td><td class="mono">${fmt(t.qty, 5)}</td><td class="mono ${t.pnl >= 0 ? 'pos' : 'neg'}">${(t.pnl >= 0 ? '+' : '') + fmt(t.pnl, 2)}</td><td class="mono ${t.r >= 0 ? 'pos' : 'neg'}">${fmt(t.r, 2)}</td><td class="k">${t.reason}</td></tr>`).join("");
    const p = d.period || {};
    $("bt-period-info").textContent = `${d.exchange} · ${d.symbol} · ${d.timeframe} — ${d.candles} candles over ~${p.days || "?"} days (${p.from ? new Date(p.from).toLocaleDateString() : "?"} → ${p.to ? new Date(p.to).toLocaleDateString() : "?"})`;
    $("bt-csv").disabled = !d.trades.length;
    // Un-hide the results BEFORE drawing so the canvases have real dimensions.
    $("bt-out").classList.remove("hidden");
    const eq = d.equity.map(e => e[1]);
    requestAnimationFrame(() => {
      drawLine("bt-eqc", eq, "#f97316", true);
      let peak = -1e9;
      drawLine("bt-ddc", eq.map(v => { peak = Math.max(peak, v); return peak > 0 ? -(peak - v) / peak * 100 : 0; }), "#ef4444", true);
    });
  } catch (e) { alert("Backtest: " + e.message); }
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

// ================= ADVANCED CANDLE CHART (zoom / pan / SL-TP) =================
let CH = null;            // {d, i0, i1}
let chDrag = null;        // {x, i0, i1} while dragging
let chPinch = null;
async function loadChart() {
  const info = $("ch-price"); if (!info) return;
  try {
    const d = await api(`/api/candles?symbol=${encodeURIComponent($("ch-sym").value)}&timeframe=${$("ch-tf").value}&limit=400`);
    CH = { d, i0: 0, i1: d.candles.length }; chReset();
    const buys = d.markers.filter(m => m.type === "enter" && m.side === "long").length;
    const sells = d.markers.filter(m => m.type === "enter" && m.side === "short").length;
    $("ch-legend").innerHTML = `<span>${d.symbol} · ${d.timeframe}</span> <span class="lg-fast">EMA${d.fast_ema}</span> <span class="lg-slow">EMA${d.slow_ema}</span> <span class="lg-trend">EMA${d.trend_ema}</span> <span class="pos">▲ ${buys} BUY</span> <span class="neg">▼ ${sells} SELL</span>`;
  } catch (e) { const ctx = $("ch-price").getContext("2d"); ctx.clearRect(0, 0, 2000, 500); ctx.fillStyle = "#8a97ab"; ctx.font = "13px sans-serif"; ctx.fillText("No chart data — check connectivity", 14, 26); }
}
function chReset() { if (!CH) return; const n = CH.d.candles.length; CH.i1 = n; CH.i0 = Math.max(0, n - 140); drawChart(); }
function chZoom(f) { if (!CH) return; const n = CH.d.candles.length; let c = Math.round((CH.i1 - CH.i0) * f); c = Math.max(20, Math.min(n, c)); const mid = (CH.i0 + CH.i1) / 2; CH.i1 = Math.min(n, Math.round(mid + c / 2)); CH.i0 = Math.max(0, CH.i1 - c); drawChart(); }
function chPan(dCandles) { if (!CH) return; const n = CH.d.candles.length, c = CH.i1 - CH.i0; let i0 = Math.max(0, Math.min(n - c, chDrag.i0 + dCandles)); CH.i0 = i0; CH.i1 = i0 + c; drawChart(); }
function drawChart() {
  if (!CH) return;
  const cv = $("ch-price"); if (cv.clientWidth < 20) return; const ctx = cv.getContext("2d");
  const w = cv.width = cv.clientWidth, h = cv.height = cv.clientHeight; ctx.clearRect(0, 0, w, h);
  const d = CH.d, i0 = Math.max(0, Math.floor(CH.i0)), i1 = Math.min(d.candles.length, Math.ceil(CH.i1));
  const view = d.candles.slice(i0, i1); if (view.length < 2) return;
  let lo = Math.min(...view.map(c => c[3])), hi = Math.max(...view.map(c => c[2]));
  // include SL/TP of visible markers in range so lines are on-screen
  d.markers.forEach(m => { if (m.i >= i0 && m.i < i1 && m.type === "enter") {[m.sl, m.tp1, m.tp2].forEach(v => { if (v) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }); } });
  const rng = (hi - lo) || 1, padR = 62, plotW = w - 8 - padR;
  const N = i1 - i0, cw = Math.max(1.5, plotW / N * 0.7);
  const x = i => 8 + (i - i0) * plotW / (N - 1);
  const y = v => 6 + (hi - v) / rng * (h - 12);
  // grid + price axis
  ctx.fillStyle = "#8a97ab"; ctx.font = "10px monospace"; ctx.strokeStyle = "#1c2534";
  for (let g = 0; g <= 4; g++) { const v = lo + rng * g / 4, yy = y(v); ctx.beginPath(); ctx.moveTo(8, yy); ctx.lineTo(8 + plotW, yy); ctx.stroke(); ctx.fillText(v.toFixed(v < 10 ? 4 : 2), w - padR + 5, yy + 3); }
  // candles
  view.forEach((k, idx) => {
    const i = i0 + idx, [, o, hg, l, c] = k, up = c >= o; ctx.strokeStyle = up ? "#22c55e" : "#ef4444"; ctx.fillStyle = ctx.strokeStyle;
    ctx.beginPath(); ctx.moveTo(x(i), y(hg)); ctx.lineTo(x(i), y(l)); ctx.stroke();
    const yt = y(Math.max(o, c)), yb = y(Math.min(o, c)); ctx.fillRect(x(i) - cw / 2, yt, cw, Math.max(1, yb - yt));
  });
  // EMA overlays
  const line = (arr, col) => { ctx.strokeStyle = col; ctx.lineWidth = 1.4; ctx.beginPath(); let st = false; for (let i = i0; i < i1; i++) { const v = arr[i]; if (v == null) continue; const px = x(i), py = y(v); st ? ctx.lineTo(px, py) : ctx.moveTo(px, py); st = true; } ctx.stroke(); ctx.lineWidth = 1; };
  line(d.fast, "#eab308"); line(d.slow, "#3b82f6"); line(d.trend, "#a855f7");
  // markers + SL/TP lines
  ctx.font = "bold 11px sans-serif";
  d.markers.forEach(m => {
    if (m.i < i0 || m.i >= i1) return; const px = x(m.i);
    if (m.type === "enter") {
      const buy = m.side === "long", col = buy ? "#22c55e" : "#ef4444"; const py = y(m.price);
      ctx.fillStyle = col; ctx.beginPath();
      if (buy) { ctx.moveTo(px, py + 14); ctx.lineTo(px - 6, py + 26); ctx.lineTo(px + 6, py + 26); } else { ctx.moveTo(px, py - 14); ctx.lineTo(px - 6, py - 26); ctx.lineTo(px + 6, py - 26); }
      ctx.closePath(); ctx.fill();
      const lab = buy ? "BUY" : "SELL", tw = ctx.measureText(lab).width, ly = buy ? py + 40 : py - 32, lx = Math.min(Math.max(px - tw / 2 - 4, 2), w - padR - tw - 8);
      ctx.fillStyle = col; rr(ctx, lx, ly - 12, tw + 8, 16, 4); ctx.fill(); ctx.fillStyle = "#0a0d13"; ctx.fillText(lab, lx + 4, ly);
      const seg = Math.min(px + 64, w - padR);
      if (m.sl) { hline(ctx, px, seg, y(m.sl), "#ef4444"); chlabel(ctx, seg, y(m.sl), "SL", "#ef4444"); }
      if (m.tp1) { hline(ctx, px, seg, y(m.tp1), "#22c55e"); chlabel(ctx, seg, y(m.tp1), "TP1", "#22c55e"); }
      if (m.tp2) { hline(ctx, px, seg, y(m.tp2), "#16a34a"); chlabel(ctx, seg, y(m.tp2), "TP2", "#16a34a"); }
    } else { ctx.fillStyle = "#8a97ab"; ctx.fillRect(px - 2, y(m.price) - 2, 4, 4); }
  });
}
function rr(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
function hline(ctx, x1, x2, yy, col) { ctx.strokeStyle = col; ctx.setLineDash([4, 3]); ctx.beginPath(); ctx.moveTo(x1, yy); ctx.lineTo(x2, yy); ctx.stroke(); ctx.setLineDash([]); }
function chlabel(ctx, x, yy, text, col) { ctx.font = "bold 9px sans-serif"; const tw = ctx.measureText(text).width; ctx.fillStyle = col; rr(ctx, x + 2, yy - 7, tw + 6, 13, 3); ctx.fill(); ctx.fillStyle = "#0a0d13"; ctx.fillText(text, x + 5, yy + 3); }

// order-panel mark price for the selected symbol
async function markTick() {
  const el = $("tr-sym"); if (!el) return; const sym = el.value;
  try { const d = await api("/api/prices?symbols=" + encodeURIComponent(sym)); const t = d[sym]; if ($("tr-mark")) $("tr-mark").value = t && t.price != null ? fmt(t.price, t.price < 10 ? 5 : 2) : "—"; } catch (e) { }
}

// chart interaction
function chSetup() {
  const cv = $("ch-price"); if (!cv || cv._wired) return; cv._wired = true;
  const candleW = () => { const N = (CH ? CH.i1 - CH.i0 : 1); return (cv.clientWidth - 70) / Math.max(1, N); };
  cv.addEventListener("wheel", e => { e.preventDefault(); chZoom(e.deltaY > 0 ? 1.15 : 0.87); }, { passive: false });
  cv.addEventListener("mousedown", e => { if (CH) chDrag = { x: e.clientX, i0: CH.i0 }; });
  window.addEventListener("mousemove", e => { if (chDrag) chPan(Math.round((chDrag.x - e.clientX) / candleW())); });
  window.addEventListener("mouseup", () => chDrag = null);
  cv.addEventListener("touchstart", e => { if (e.touches.length === 1 && CH) chDrag = { x: e.touches[0].clientX, i0: CH.i0 }; else if (e.touches.length === 2 && CH) chPinch = { dist: tdist(e), c: CH.i1 - CH.i0, mid: (CH.i0 + CH.i1) / 2 }; }, { passive: true });
  cv.addEventListener("touchmove", e => {
    if (e.touches.length === 2 && chPinch) { const nd = tdist(e); let c = Math.round(chPinch.c * chPinch.dist / (nd || 1)); c = Math.max(20, Math.min(CH.d.candles.length, c)); CH.i1 = Math.min(CH.d.candles.length, Math.round(chPinch.mid + c / 2)); CH.i0 = Math.max(0, CH.i1 - c); drawChart(); }
    else if (e.touches.length === 1 && chDrag) { chPan(Math.round((chDrag.x - e.touches[0].clientX) / candleW())); }
  }, { passive: true });
  cv.addEventListener("touchend", () => { chDrag = null; chPinch = null; });
}
function tdist(e) { const a = e.touches[0], b = e.touches[1]; return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY); }

function btRedraw() {
  if (!BT || $("bt-out").classList.contains("hidden")) return;
  const eq = BT.equity.map(e => e[1]); drawLine("bt-eqc", eq, "#f97316", true);
  let peak = -1e9; drawLine("bt-ddc", eq.map(v => { peak = Math.max(peak, v); return peak > 0 ? -(peak - v) / peak * 100 : 0; }), "#ef4444", true);
}
// Redraw canvases when the tab becomes visible again (Chrome memory-saver can
// discard canvas contents while a tab is frozen).
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  requestAnimationFrame(() => { if (CH) drawChart(); btRedraw(); });
});

function toast(msg) { const e = $("st-email"); e.textContent = "✓ " + msg; setTimeout(() => { if (lastState) e.textContent = "👋 " + (lastState.name || (lastState.email || "").split("@")[0]); }, 1500); }
window.addEventListener("load", chSetup);
if (TOKEN) enterApp();
