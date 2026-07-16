// Prometheus Web — admin panel
let TOKEN = localStorage.getItem("prometheus_token") || "";
const $ = id => document.getElementById(id);

async function api(path, method = "GET", bodyObj) {
  const opt = { method, headers: {} };
  if (TOKEN) opt.headers["Authorization"] = "Bearer " + TOKEN;
  if (bodyObj) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(bodyObj); }
  const r = await fetch(path, opt);
  const t = await r.text(); let d; try { d = JSON.parse(t); } catch { d = { detail: t }; }
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
  return d;
}
function logout() { localStorage.removeItem("prometheus_token"); TOKEN = ""; location.reload(); }

async function adminLogin() {
  $("g-err").textContent = "";
  try {
    const d = await api("/api/login", "POST", { email: $("g-email").value.trim(), password: $("g-pass").value });
    TOKEN = d.token; localStorage.setItem("prometheus_token", TOKEN); boot();
  } catch (e) { $("g-err").textContent = e.message; }
}

async function boot() {
  try {
    const d = await api("/api/admin/users");
    $("gate").classList.add("hidden"); $("panel").classList.remove("hidden");
    render(d.users);
  } catch (e) {
    if (String(e.message).includes("admin only")) { $("ad-me").textContent = "not an admin account"; showGate(); }
    else showGate();
  }
}
function showGate() { $("gate").classList.remove("hidden"); $("panel").classList.add("hidden"); }

async function loadUsers() { try { render((await api("/api/admin/users")).users); } catch (e) { alert(e.message); } }

function render(users) {
  let trial = 0, lic = 0, off = 0;
  $("u-body").innerHTML = users.map(u => {
    const e = u.entitlement || {};
    if (e.status === "trial") trial++; else if (e.status === "licensed") lic++;
    else if (e.status === "suspended" || e.status === "expired") off++;
    const cls = e.ok ? (e.status === "trial" ? "warn" : "pos") : "neg";
    const seen = u.last_seen ? new Date(u.last_seen * 1000).toLocaleString() : "—";
    return `<tr>
      <td>${u.id}</td><td class="mono">${u.email}${u.is_admin ? ' <span class="badge">admin</span>' : ''}</td>
      <td class="${cls}">${e.status}</td><td>${u.plan}</td><td class="mono">${e.days_left ?? '—'}</td>
      <td>${u.has_keys ? '✓' : '—'}</td><td class="k">${seen}</td>
      <td>
        ${u.active ? `<button class="btn ghost sm" onclick="act(${u.id},'suspend')">Suspend</button>`
        : `<button class="btn green sm" onclick="act(${u.id},'activate')">Activate</button>`}
        <button class="btn sm" onclick="grant(${u.id})">+Licence</button>
        <button class="btn ghost sm" onclick="act(${u.id},'revoke')">Revoke</button>
      </td></tr>`;
  }).join("");
  $("m-total").textContent = users.length;
  $("m-trial").textContent = trial; $("m-lic").textContent = lic; $("m-off").textContent = off;
}

async function act(uid, action) {
  if (action === "suspend" && !confirm("Suspend this customer? Their trading stops immediately.")) return;
  try { await api("/api/admin/action", "POST", { user_id: uid, action }); loadUsers(); } catch (e) { alert(e.message); }
}
async function grant(uid) {
  const days = prompt("Grant licence for how many days?", "30");
  if (!days) return;
  try { await api("/api/admin/action", "POST", { user_id: uid, action: "grant", days: parseFloat(days) }); loadUsers(); }
  catch (e) { alert(e.message); }
}

if (TOKEN) boot(); else showGate();
