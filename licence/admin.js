// Lead Agent licence admin: talks to api.php (api/admin/*) with a login session.
(() => {
  'use strict';
  if (!location.pathname.endsWith('/')) { location.replace(location.pathname + '/' + location.search + location.hash); return; }

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const state = { csrf: null, admin: null, licences: [], settings: {}, users: [], filter: 'all', view: 'dashboard' };

  // ---------- api ----------
  async function api(path, { method = 'GET', body } = {}) {
    const res = await fetch(`api/admin/${path}`, {
      method, credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...(state.csrf ? { 'X-CSRF': state.csrf } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    let data; try { data = await res.json(); } catch { data = { ok: false, error: `The server answered ${res.status}.` }; }
    if (data.code === 'unauthorized' && path !== 'status') { showAuth(true); throw new Error(data.error); }
    if (!data.ok) throw new Error(data.error || 'Something went wrong.');
    return data;
  }

  // ---------- formatting ----------
  const fmtDate = (t) => (t ? new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '–');
  const fmtDateTime = (t) => (t ? new Date(t).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–');
  function ago(t) {
    if (!t) return 'never';
    const s = Math.round((Date.now() - t) / 1000);
    if (s < 60) return 'just now';
    const m = Math.round(s / 60); if (m < 60) return `${m} min ago`;
    const h = Math.round(m / 60); if (h < 24) return `${h} h ago`;
    const d = Math.round(h / 24); if (d < 31) return `${d} day${d > 1 ? 's' : ''} ago`;
    return fmtDate(t);
  }
  const STATUS = { active: 'Active', unused: 'Not used yet', revoked: 'Switched off', expired: 'Expired' };
  const badge = (s) => `<span class="badge ${s}">${STATUS[s] || s}</span>`;
  const ICON = {
    key: '<svg viewBox="0 0 24 24"><path d="M7 14a2 2 0 1 1 0-4 2 2 0 0 1 0 4zm5.65-4A6 6 0 1 0 12.65 14H17v4h4v-4h2v-4z"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>',
    user: '<svg viewBox="0 0 24 24"><path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>',
    pc: '<svg viewBox="0 0 24 24"><path d="M20 18a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2H0v2h24v-2zM4 6h16v10H4z"/></svg>',
    off: '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM4 12a8 8 0 0 1 12.9-6.32L5.68 16.9A7.95 7.95 0 0 1 4 12zm8 8a7.95 7.95 0 0 1-4.9-1.68L18.32 7.1A8 8 0 0 1 12 20z"/></svg>',
    clock: '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 11h-5v-2h3V6h2z"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><path d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h11z"/></svg>',
    gear: '<svg viewBox="0 0 24 24"><path d="M12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7zm7.43-2.53a7.8 7.8 0 0 0 0-1.94l2.11-1.65-2-3.46-2.49 1a7.3 7.3 0 0 0-1.68-.98L15 3.29h-4l-.38 2.65a7.3 7.3 0 0 0-1.68.98l-2.49-1-2 3.46 2.11 1.65a7.8 7.8 0 0 0 0 1.94l-2.11 1.65 2 3.46 2.49-1c.52.4 1.08.73 1.68.98L11 20.71h4l.38-2.65c.6-.25 1.16-.58 1.68-.98l2.49 1 2-3.46z"/></svg>',
  };
  const EVENT = {
    activated: ['ok', 'user', (e) => `<b>${esc(e.name || 'Someone')}</b> (${esc(e.email || '')}) activated <span class="mono">${esc(e.key)}</span> on ${esc(e.computer || 'a computer')}`],
    created: ['', 'key', (e) => `Key <span class="mono">${esc(e.key)}</span> created${e.name || e.email ? ` for <b>${esc(e.name || e.email)}</b>` : ''}`],
    revoke: ['bad', 'off', (e) => `Key <span class="mono">${esc(e.key)}</span> switched off`],
    restore: ['ok', 'check', (e) => `Key <span class="mono">${esc(e.key)}</span> switched back on`],
    release: ['warn', 'pc', (e) => `A computer was released from <span class="mono">${esc(e.key)}</span>`],
    'release-all': ['warn', 'pc', (e) => `All computers released from <span class="mono">${esc(e.key)}</span>`],
    released_by_user: ['warn', 'pc', (e) => `A user released their computer from <span class="mono">${esc(e.key)}</span>`],
    edit: ['', 'key', (e) => `Key <span class="mono">${esc(e.key)}</span> edited`],
    deleted: ['bad', 'off', (e) => `Key <span class="mono">${esc(e.key)}</span> deleted`],
    login: ['', 'user', (e) => `Admin logged in (${esc(e.by || '')})`],
    login_failed: ['bad', 'off', () => 'A wrong admin password was entered'],
    setup: ['ok', 'gear', () => 'Licence server set up'],
    settings: ['', 'gear', () => 'Settings changed'],
    password_changed: ['', 'gear', () => 'Admin password changed'],
  };
  const feedItem = (e) => {
    const [tone, icon, text] = EVENT[e.type] || ['', 'clock', () => esc(e.type)];
    return `<li><span class="dot ${tone}">${ICON[icon]}</span><p>${text(e)}</p><small title="${esc(fmtDateTime(e.t))}">${ago(e.t)}</small></li>`;
  };

  // ---------- toasts, copy, modal ----------
  function toast(msg, bad = false) {
    const t = document.createElement('div'); t.className = `toast${bad ? ' bad' : ''}`; t.textContent = msg;
    $('#toasts').append(t); setTimeout(() => t.remove(), 3200);
  }
  async function copy(text, label = 'Copied') {
    try { await navigator.clipboard.writeText(text); toast(label); } catch {
      const ta = document.createElement('textarea'); ta.value = text; document.body.append(ta); ta.select();
      try { document.execCommand('copy'); toast(label); } catch { toast('Copy failed: select the text and copy it', true); }
      ta.remove();
    }
  }
  const copyBtn = (text) => `<button type="button" class="copy" data-copy="${esc(text)}">${esc(text)}${ICON.copy}</button>`;
  document.addEventListener('click', (e) => {
    const c = e.target.closest('[data-copy]');
    if (c) { e.stopPropagation(); copy(c.dataset.copy, c.dataset.copyLabel || 'Copied'); }
  });

  let modalResolve = null;
  function openModal(html) {
    $('#modal-card').innerHTML = html; $('#modal').hidden = false;
    const f = $('#modal-card input, #modal-card textarea, #modal-card button'); f?.focus();
    return new Promise((r) => { modalResolve = r; });
  }
  function closeModal(v) { $('#modal').hidden = true; $('#modal-card').innerHTML = ''; modalResolve?.(v); modalResolve = null; }
  $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal' || e.target.closest('[data-close]')) closeModal(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { if (!$('#modal').hidden) closeModal(false); else if (!$('#drawer').hidden) closeDrawer(); } });
  async function confirmBox(title, text, okLabel = 'Confirm', danger = false) {
    const p = openModal(`<h2>${esc(title)}</h2><p class="muted">${esc(text)}</p><div class="modal-foot"><button class="btn" data-close>Cancel</button><button class="btn ${danger ? 'danger' : 'primary'}" id="m-ok">${esc(okLabel)}</button></div>`);
    $('#m-ok').addEventListener('click', () => closeModal(true));
    return p;
  }

  function fillTemplate(lic) {
    const st = state.settings;
    return String(st.messageTemplate || '').replace(/\{(\w+)\}/g, (m, k) => ({
      name: lic.name || 'there', email: lic.email || '', key: lic.key, devices: lic.maxDevices, product: st.productName || 'Lead Agent',
      seller: st.sellerName || '', support: st.supportEmail || '',
    })[k] ?? m);
  }

  // ---------- auth ----------
  async function boot() {
    let st;
    try { st = await api('status'); } catch (e) { $('#boot').innerHTML = `<p class="muted">Could not reach the licence server: ${esc(e.message)}</p>`; return; }
    $('#boot').hidden = true;
    if (!st.setupDone) { $('#auth').hidden = false; $('#setup-form').hidden = false; $('#setup-form [name=name]').focus(); return; }
    if (!st.loggedIn) { showAuth(false); return; }
    state.csrf = st.csrf; state.admin = st.admin;
    startApp();
  }
  function showAuth(expired) {
    $('#app').hidden = true; $('#auth').hidden = false; $('#setup-form').hidden = true; $('#login-form').hidden = false;
    $('#login-form .form-msg').textContent = expired ? 'Your session ended. Please log in again.' : '';
    $('#login-form [name=email]').focus();
  }
  $('#setup-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target), msg = $('.form-msg', e.target);
    if (f.get('password') !== f.get('password2')) { msg.textContent = 'The two passwords are different.'; return; }
    e.submitter && (e.submitter.disabled = true); msg.textContent = '';
    try {
      const r = await api('setup', { method: 'POST', body: { name: f.get('name'), email: f.get('email'), password: f.get('password') } });
      state.csrf = r.csrf; state.admin = { name: f.get('name'), email: String(f.get('email')).toLowerCase() };
      $('#auth').hidden = true; startApp(); toast('Your licence server is ready');
    } catch (err) { msg.textContent = err.message; } finally { e.submitter && (e.submitter.disabled = false); }
  });
  $('#login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target), msg = $('.form-msg', e.target);
    e.submitter && (e.submitter.disabled = true); msg.textContent = '';
    try {
      const r = await api('login', { method: 'POST', body: { email: f.get('email'), password: f.get('password') } });
      state.csrf = r.csrf; e.target.reset();
      const st = await api('status'); state.admin = st.admin;
      $('#auth').hidden = true; startApp();
    } catch (err) { msg.textContent = err.message; } finally { e.submitter && (e.submitter.disabled = false); }
  });
  $('#logout').addEventListener('click', async () => { try { await api('logout', { method: 'POST' }); } catch { /* */ } location.reload(); });

  // ---------- app shell ----------
  let started = false;
  function startApp() {
    $('#app').hidden = false;
    $('#me-name').textContent = state.admin?.name || '';
    $('#me-email').textContent = state.admin?.email || '';
    $('#me-avatar').textContent = (state.admin?.name || 'A').trim()[0].toUpperCase();
    if (!started) {
      started = true;
      window.addEventListener('hashchange', route);
      $('#menu').addEventListener('click', () => $('#app').classList.toggle('nav-open'));
      $('#app').addEventListener('click', (e) => { if (e.target === $('#app') ) $('#app').classList.remove('nav-open'); });
      $$('.side nav a').forEach((a) => a.addEventListener('click', () => $('#app').classList.remove('nav-open')));
      $('#new-key-top').addEventListener('click', newKey);
      setInterval(() => { if (!document.hidden && ['dashboard', 'users', 'activity'].includes(state.view)) route(); }, 60000);
    }
    loadSettings().then(route);
  }
  const TITLES = { dashboard: 'Dashboard', keys: 'Licence keys', users: 'Users', activity: 'Activity', settings: 'Settings' };
  function route() {
    const v = (location.hash.slice(1) || 'dashboard');
    state.view = TITLES[v] ? v : 'dashboard';
    $$('.view').forEach((s) => { s.hidden = s.dataset.view !== state.view; });
    $$('.side nav a').forEach((a) => a.classList.toggle('on', a.dataset.view === state.view));
    $('#view-title').textContent = TITLES[state.view];
    document.title = `${TITLES[state.view]} · Licence Admin`;
    ({ dashboard: renderDashboard, keys: renderKeys, users: renderUsers, activity: renderActivity, settings: renderSettings })[state.view]().catch((e) => toast(e.message, true));
  }
  async function loadSettings() {
    const r = await api('settings');
    state.settings = r.settings; state.publicKey = r.publicKey; state.serverInfo = { dataDir: r.dataDir, php: r.php };
    $('#product-name').textContent = r.settings.productName;
    const pill = $('#open-pill');
    pill.textContent = r.settings.activationsOpen ? 'Activations open' : 'Activations paused';
    pill.classList.toggle('off', !r.settings.activationsOpen);
  }

  // ---------- dashboard ----------
  async function renderDashboard() {
    const r = await api('overview');
    const c = r.counts;
    const k = (cls, icon, label, value, sub) => `<div class="kpi ${cls}"><i>${ICON[icon]}</i><small>${label}</small><b>${value.toLocaleString()}</b><span>${sub}</span></div>`;
    $('#kpis').innerHTML = [
      k('', 'key', 'Licence keys', c.total, `${c.unused} not used yet`),
      k('ok', 'check', 'Active keys', c.active, `${c.total ? Math.round((c.active / c.total) * 100) : 0}% of all keys`),
      k('', 'user', 'Users', c.users, `${c.computers} computer${c.computers === 1 ? '' : 's'} activated`),
      k('ok', 'pc', 'Online this week', c.online7, `${c.online1} in the last 24 h`),
      k(c.revoked + c.expired ? 'bad' : '', 'off', 'Switched off / expired', c.revoked + c.expired, `${c.revoked} off · ${c.expired} expired`),
    ].join('');
    drawChart(r.series);
    const b = Object.entries(r.browsers || {}); const max = Math.max(1, ...b.map(([, n]) => n));
    $('#browsers').innerHTML = b.length ? `<div class="bars-h">${b.map(([name, n]) => `<div><b>${esc(name)}</b><em style="width:${Math.max(6, (n / max) * 100)}%"></em><span>${n}</span></div>`).join('')}</div>`
      : '<p class="empty" style="padding:20px 0">No computers yet.</p>';
    $('#dash-feed').innerHTML = r.events.length ? r.events.map(feedItem).join('') : '<li class="empty" style="justify-content:center">Nothing has happened yet.</li>';
  }
  function drawChart(series) {
    const W = Math.max(280, Math.round($('#chart').clientWidth) || 720), H = 240, P = { l: 30, r: 8, t: 10, b: 26 };
    state.lastSeries = series;
    const max = Math.max(4, ...series.map((d) => Math.max(d.activations, d.created)));
    const step = Math.ceil(max / 4); const top = step * 4;
    const bw = (W - P.l - P.r) / series.length;
    const y = (v) => P.t + (H - P.t - P.b) * (1 - v / top);
    let g = '';
    for (let i = 0; i <= 4; i++) { const v = step * i; g += `<line x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}" stroke="#EEF0F6"/><text x="${P.l - 6}" y="${y(v) + 4}" font-size="10.5" text-anchor="end" fill="#94A3B8">${v}</text>`; }
    series.forEach((d, i) => {
      const x = P.l + i * bw;
      const w = Math.max(2, bw / 2 - 2);
      g += `<rect class="bar1" x="${x + 2}" y="${y(d.activations)}" width="${w}" height="${Math.max(0, y(0) - y(d.activations))}" rx="2"/>`;
      g += `<rect class="bar2" x="${x + 2 + w + 1}" y="${y(d.created)}" width="${w}" height="${Math.max(0, y(0) - y(d.created))}" rx="2"/>`;
      g += `<rect x="${x}" y="${P.t}" width="${bw}" height="${H - P.t - P.b}" fill="transparent" data-i="${i}"/>`;
      if (i % (W < 500 ? 10 : 5) === 0 || i === series.length - 1) g += `<text x="${x + bw / 2}" y="${H - 8}" font-size="10.5" text-anchor="middle" fill="#94A3B8">${new Date(`${d.day}T12:00:00Z`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}</text>`;
    });
    $('#chart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Activations and keys created per day">${g}</svg><div class="tip" hidden></div>`;
    const tip = $('#chart .tip');
    $('#chart svg').addEventListener('mousemove', (e) => {
      const i = e.target.dataset?.i; if (i == null) { tip.hidden = true; return; }
      const d = series[i]; const box = $('#chart').getBoundingClientRect();
      tip.hidden = false; tip.style.left = `${e.clientX - box.left}px`; tip.style.top = `${e.clientY - box.top}px`;
      tip.innerHTML = `<b>${new Date(`${d.day}T12:00:00Z`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}</b><br>${d.activations} activation${d.activations === 1 ? '' : 's'} · ${d.created} key${d.created === 1 ? '' : 's'} created`;
    });
    $('#chart svg').addEventListener('mouseleave', () => { tip.hidden = true; });
  }
  let resizeT; window.addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (state.view === 'dashboard' && state.lastSeries) drawChart(state.lastSeries); }, 150); });

  // ---------- keys ----------
  async function renderKeys() {
    const r = await api('licences');
    state.licences = r.licences; state.settings = r.settings;
    drawKeys();
  }
  function drawKeys() {
    const q = $('#key-search').value.trim().toLowerCase();
    const rows = state.licences.filter((l) => (state.filter === 'all' || l.status === state.filter)
      && (!q || [l.key, l.name, l.email, l.note, ...l.devices.map((d) => `${d.userName} ${d.userEmail}`)].join(' ').toLowerCase().includes(q)));
    $('#key-table tbody').innerHTML = rows.map((l) => `<tr data-key="${esc(l.key)}">
      <td>${copyBtn(l.key)}</td>
      <td class="who"><b>${esc(l.name || '–')}</b><small>${esc(l.email || l.note || '')}</small></td>
      <td>${badge(l.status)}</td>
      <td><div class="meter"><em><i style="width:${Math.min(100, (l.deviceCount / l.maxDevices) * 100)}%"></i></em>${l.deviceCount} / ${l.maxDevices}</div></td>
      <td>${fmtDate(l.createdAt)}</td>
      <td>${l.lastSeen ? ago(l.lastSeen) : '<span class="muted">–</span>'}</td>
      <td><button class="btn small ghost" data-open="${esc(l.key)}">Open</button></td></tr>`).join('');
    $('#key-empty').hidden = rows.length > 0;
    if (!state.licences.length) $('#key-empty').innerHTML = 'No licence keys yet. Press <b>New key</b> to make one.';
    else if (!rows.length) $('#key-empty').textContent = 'No keys match this search.';
  }
  $('#key-search').addEventListener('input', drawKeys);
  $('#key-filter').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    state.filter = b.dataset.f; $$('#key-filter button').forEach((x) => x.classList.toggle('on', x === b)); drawKeys();
  });
  $('#key-table').addEventListener('click', (e) => { const tr = e.target.closest('tr[data-key]'); if (tr && !e.target.closest('[data-copy]')) openDrawer(tr.dataset.key); });

  async function newKey() {
    const st = state.settings;
    const p = openModal(`<h2>New licence key</h2><p class="muted">Make a key for a buyer, then send it with the ready-made message.</p>
      <form id="new-form">
        <div class="row"><label>Buyer name<input name="name" placeholder="optional"></label><label>Buyer email<input name="email" type="email" placeholder="optional"></label></div>
        <label>Note<input name="note" placeholder="e.g. paid via PayPal, order #1042"></label>
        <div class="row"><label>Computers<input name="maxDevices" type="number" min="1" max="50" value="${esc(st.defaultMaxDevices || 2)}"></label><label>Expires<input name="expires" type="date"><span class="hint">Leave empty for lifetime</span></label></div>
        <label>How many keys<input name="count" type="number" min="1" max="200" value="1"><span class="hint">More than 1 makes blank keys for resellers or bundles.</span></label>
        <div class="modal-foot"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary" type="submit">Create key</button></div>
      </form>`);
    $('#new-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const exp = f.get('expires') ? new Date(`${f.get('expires')}T23:59:59`).getTime() : null;
      e.submitter.disabled = true;
      try {
        const r = await api('licences', { method: 'POST', body: { name: f.get('name'), email: f.get('email'), note: f.get('note'), maxDevices: Number(f.get('maxDevices')), expiresAt: exp, count: Number(f.get('count')) } });
        closeModal(true); showCreated(r.licences);
        if (state.view === 'keys') renderKeys(); else if (state.view === 'dashboard') renderDashboard();
      } catch (err) { toast(err.message, true); e.submitter.disabled = false; }
    });
    return p;
  }
  function showCreated(list) {
    if (list.length === 1) {
      const l = list[0]; const msg = fillTemplate(l);
      openModal(`<h2>Key created</h2><p class="muted">Send this to ${esc(l.name || l.email || 'the buyer')} with the Lead Agent zip.</p>
        <div style="margin:14px 0">${copyBtn(l.key)}</div>
        <div class="msg-box" id="msg-text">${esc(msg)}</div>
        <div class="modal-foot"><button class="btn" data-close>Done</button><button class="btn primary" data-copy="${esc(msg)}" data-copy-label="Message copied">Copy message</button></div>`);
    } else {
      const keys = list.map((l) => l.key).join('\n');
      openModal(`<h2>${list.length} keys created</h2><p class="muted">Each works on ${list[0].maxDevices} computers.</p>
        <div class="keylist">${list.map((l) => copyBtn(l.key)).join('')}</div>
        <div class="modal-foot"><button class="btn" data-close>Done</button><button class="btn primary" data-copy="${esc(keys)}" data-copy-label="All keys copied">Copy all</button></div>`);
    }
  }

  // ---------- drawer ----------
  function closeDrawer() { $('#drawer').hidden = true; $('#scrim').hidden = true; }
  $('#d-close').addEventListener('click', closeDrawer);
  $('#scrim').addEventListener('click', closeDrawer);
  async function openDrawer(key) {
    $('#drawer').hidden = false; $('#scrim').hidden = false;
    $('#d-key').textContent = key;
    $('#d-body').innerHTML = '<div class="spinner" style="margin:40px auto"></div>';
    let r; try { r = await api(`licences/${encodeURIComponent(key)}`); } catch (e) { $('#d-body').innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    const l = r.licence;
    $('#d-body').innerHTML = `
      <div class="actions">${badge(l.status)}<button class="copy" data-copy="${esc(l.key)}">${ICON.copy}Copy key</button><button class="copy" data-copy="${esc(fillTemplate(l))}" data-copy-label="Message copied">${ICON.copy}Copy message</button></div>
      <section><h3>Buyer</h3>
        <form id="d-edit" class="form-grid" style="grid-template-columns:1fr 1fr">
          <label>Name<input name="name" value="${esc(l.name)}"></label><label>Email<input name="email" type="email" value="${esc(l.email)}"></label>
          <label class="full">Note<input name="note" value="${esc(l.note)}"></label>
          <label>Computers allowed<input name="maxDevices" type="number" min="1" max="50" value="${l.maxDevices}"></label>
          <label>Expires<input name="expires" type="date" value="${l.expiresAt ? new Date(l.expiresAt).toISOString().slice(0, 10) : ''}"></label>
          <div class="full"><button class="btn small" type="submit">Save changes</button></div>
        </form></section>
      <section><h3>Computers (${l.deviceCount} of ${l.maxDevices})</h3>
        ${l.devices.length ? l.devices.map((d) => `<div class="dev"><span class="dot" style="width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:var(--pri-soft);color:var(--pri)">${ICON.pc}</span>
          <div><b>${esc(d.userName || '–')} · ${esc(d.userEmail || '')}</b><small>${esc(d.label)} · activated ${fmtDate(d.firstSeen)} · seen ${ago(d.lastSeen)}</small></div>
          <button class="btn small danger" data-release="${esc(d.id)}">Release</button></div>`).join('') : '<p class="muted">Not activated on any computer yet.</p>'}
      </section>
      <section><h3>Details</h3><dl class="kv"><dt>Created</dt><dd>${fmtDateTime(l.createdAt)}</dd><dt>First activated</dt><dd>${fmtDateTime(l.activatedAt)}</dd><dt>Expires</dt><dd>${l.expiresAt ? fmtDate(l.expiresAt) : 'Never (lifetime)'}</dd><dt>Last seen</dt><dd>${l.lastSeen ? ago(l.lastSeen) : '–'}</dd></dl></section>
      <section><h3>History</h3><ul class="feed">${r.events.length ? r.events.map(feedItem).join('') : '<li class="muted">No events.</li>'}</ul></section>
      <section class="actions">
        ${l.revoked ? '<button class="btn" data-act="restore">Switch back on</button>' : '<button class="btn danger" data-act="revoke">Switch off key</button>'}
        ${l.devices.length ? '<button class="btn" data-act="release-all">Release all computers</button>' : ''}
        <button class="btn danger ghost" data-act="delete">Delete key</button>
      </section>`;
    $('#d-edit').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      try {
        await api(`licences/${encodeURIComponent(key)}/edit`, { method: 'POST', body: { name: f.get('name'), email: f.get('email'), note: f.get('note'), maxDevices: Number(f.get('maxDevices')), expiresAt: f.get('expires') ? new Date(`${f.get('expires')}T23:59:59`).getTime() : null } });
        toast('Saved'); refreshAfter(key);
      } catch (err) { toast(err.message, true); }
    });
    $$('[data-release]', $('#d-body')).forEach((b) => b.addEventListener('click', async () => {
      if (!(await confirmBox('Release this computer?', 'Lead Agent stops working on it until the key is entered again there. The slot can then be used on another computer.', 'Release'))) return;
      try { await api(`licences/${encodeURIComponent(key)}/release`, { method: 'POST', body: { device: b.dataset.release } }); toast('Computer released'); refreshAfter(key); } catch (err) { toast(err.message, true); }
    }));
    $$('[data-act]', $('#d-body')).forEach((b) => b.addEventListener('click', async () => {
      const act = b.dataset.act;
      const ask = {
        revoke: ['Switch off this key?', `Lead Agent stops working for this buyer within ${state.settings.passDays || 7} days (within 12 hours on computers that are online). You can switch it back on later.`, 'Switch off', true],
        'release-all': ['Release all computers?', 'Every computer using this key has to enter it again.', 'Release all', true],
        delete: ['Delete this key for good?', 'The key stops working and cannot be restored. Switching it off is usually better.', 'Delete', true],
      }[act];
      if (ask && !(await confirmBox(...ask))) return;
      try {
        await api(`licences/${encodeURIComponent(key)}/${act}`, { method: 'POST', body: {} });
        toast({ revoke: 'Key switched off', restore: 'Key switched back on', 'release-all': 'All computers released', delete: 'Key deleted' }[act]);
        if (act === 'delete') { closeDrawer(); renderKeys(); } else refreshAfter(key);
      } catch (err) { toast(err.message, true); }
    }));
  }
  function refreshAfter(key) { openDrawer(key); if (state.view === 'keys') renderKeys(); }

  // ---------- users ----------
  async function renderUsers() { const r = await api('users'); state.users = r.users; drawUsers(); }
  function drawUsers() {
    const q = $('#user-search').value.trim().toLowerCase();
    const rows = state.users.filter((u) => !q || [u.userName, u.userEmail, u.key, u.computer, u.buyer, u.buyerEmail].join(' ').toLowerCase().includes(q));
    $('#user-table tbody').innerHTML = rows.map((u) => `<tr data-key="${esc(u.key)}">
      <td class="who"><b>${esc(u.userName || '–')}</b><small>${esc(u.userEmail)}</small></td>
      <td>${copyBtn(u.key)}</td><td>${esc(u.computer)}</td><td>${fmtDate(u.firstSeen)}</td><td>${ago(u.lastSeen)}</td><td>${badge(u.status)}</td></tr>`).join('');
    $('#user-empty').hidden = rows.length > 0;
  }
  $('#user-search').addEventListener('input', drawUsers);
  $('#user-table').addEventListener('click', (e) => { const tr = e.target.closest('tr[data-key]'); if (tr && !e.target.closest('[data-copy]')) openDrawer(tr.dataset.key); });

  // ---------- activity ----------
  async function renderActivity() {
    const r = await api('events');
    $('#act-feed').innerHTML = r.events.length ? r.events.map(feedItem).join('') : '<li class="empty" style="justify-content:center">Nothing has happened yet.</li>';
  }

  // ---------- settings ----------
  async function renderSettings() {
    await loadSettings();
    const f = $('#settings-form'), st = state.settings;
    for (const [k, v] of Object.entries(st)) { const el = f.elements[k]; if (!el) continue; if (el.type === 'checkbox') el.checked = !!v; else el.value = v; }
    $('#pubkey').textContent = state.publicKey ? JSON.stringify(state.publicKey) : 'Not created yet.';
    $('#server-info').textContent = `PHP ${state.serverInfo.php} · data saved privately outside the website folder`;
  }
  $('#settings-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target, body = {};
    for (const el of f.elements) { if (!el.name) continue; body[el.name] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value; }
    try { await api('settings', { method: 'POST', body }); await loadSettings(); toast('Settings saved'); } catch (err) { toast(err.message, true); }
  });
  $('#password-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target), msg = $('.form-msg', e.target);
    try { await api('password', { method: 'POST', body: { current: f.get('current'), password: f.get('password') } }); e.target.reset(); msg.className = 'form-msg ok'; msg.textContent = 'Password changed.'; } catch (err) { msg.className = 'form-msg'; msg.textContent = err.message; }
  });

  boot();
})();
