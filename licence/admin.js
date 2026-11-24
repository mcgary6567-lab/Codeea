// Lead Agent licence admin: talks to api.php (api/admin/*) with a login session.
(() => {
  'use strict';
  if (!location.pathname.endsWith('/')) { location.replace(location.pathname + '/' + location.search + location.hash); return; }

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const state = { csrf: null, admin: null, licences: [], settings: {}, users: [], filter: 'all', userFilter: 'all', userSort: 'recent', view: 'dashboard', selKeys: new Set(), selUsers: new Set() };

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
  const STATUS = { active: 'Active', unused: 'Not activated', revoked: 'Paused', expired: 'Expired', blocked: 'Blocked' };
  const badge = (s) => `<span class="badge ${s}">${STATUS[s] || s}</span>`;
  const HUES = [235, 262, 290, 322, 200, 172, 18, 340];
  const avatar = (u, big = false) => {
    const t = (u.name || u.email || '?').trim();
    const ini = t.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((x) => x[0]).join('').toUpperCase() || '?';
    let h = 0; for (const ch of String(u.email || t)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    const hue = HUES[h % HUES.length];
    return `<span class="av${big ? ' big' : ''}" style="background:hsl(${hue} 70% 94%);color:hsl(${hue} 55% 38%)">${esc(ini)}</span>`;
  };
  const ICON = {
    key: '<svg viewBox="0 0 24 24"><path d="M7 14a2 2 0 1 1 0-4 2 2 0 0 1 0 4zm5.65-4A6 6 0 1 0 12.65 14H17v4h4v-4h2v-4z"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>',
    user: '<svg viewBox="0 0 24 24"><path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>',
    pc: '<svg viewBox="0 0 24 24"><path d="M20 18a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2H0v2h24v-2zM4 6h16v10H4z"/></svg>',
    off: '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM4 12a8 8 0 0 1 12.9-6.32L5.68 16.9A7.95 7.95 0 0 1 4 12zm8 8a7.95 7.95 0 0 1-4.9-1.68L18.32 7.1A8 8 0 0 1 12 20z"/></svg>',
    clock: '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 11h-5v-2h3V6h2z"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><path d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h11z"/></svg>',
    gear: '<svg viewBox="0 0 24 24"><path d="M12 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7zm7.43-2.53a7.8 7.8 0 0 0 0-1.94l2.11-1.65-2-3.46-2.49 1a7.3 7.3 0 0 0-1.68-.98L15 3.29h-4l-.38 2.65a7.3 7.3 0 0 0-1.68.98l-2.49-1-2 3.46 2.11 1.65a7.8 7.8 0 0 0 0 1.94l-2.11 1.65 2 3.46 2.49-1c.52.4 1.08.73 1.68.98L11 20.71h4l.38-2.65c.6-.25 1.16-.58 1.68-.98l2.49 1 2-3.46z"/></svg>',
    pause: '<svg viewBox="0 0 24 24"><path d="M6 19h4V5H6zm8-14v14h4V5z"/></svg>',
    play: '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6zM19 4h-3.5l-1-1h-5l-1 1H5v2h14z"/></svg>',
    ban: '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 0 1-6.32-12.9L16.9 18.32A7.95 7.95 0 0 1 12 20zm6.32-3.1L7.1 5.68A8 8 0 0 1 18.32 16.9z"/></svg>',
    more: '<svg viewBox="0 0 24 24"><path d="M12 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4zm0 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm0 6a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"/></svg>',
    mail: '<svg viewBox="0 0 24 24"><path d="M20 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zm0 4-8 5-8-5V6l8 5 8-5z"/></svg>',
    bolt: '<svg viewBox="0 0 24 24"><path d="M7 2v11h3v9l7-12h-4l4-8z"/></svg>',
    download: '<svg viewBox="0 0 24 24"><path d="M5 20h14v-2H5zm7-18-5.5 5.5 1.41 1.41L11 5.83V16h2V5.83l3.09 3.08 1.41-1.41z" transform="rotate(180 12 11)"/></svg>',
  };
  const who = (e) => `<b>${esc(e.email || '')}</b>`;
  const EVENT = {
    activated: ['ok', 'user', (e) => `<b>${esc(e.name || 'Someone')}</b> (${esc(e.email || '')}) activated ${e.how === 'email' ? 'with their email' : `<span class="mono">${esc(e.key)}</span>`} on ${esc(e.computer || 'a computer')}`],
    created: ['', 'key', (e) => (e.auto ? `New licence <span class="mono">${esc(e.key)}</span> created automatically for <b>${esc(e.name || '')}</b> (${esc(e.email || '')})`
      : `Key <span class="mono">${esc(e.key)}</span> created${e.name || e.email ? ` for <b>${esc(e.name || e.email)}</b>` : ''}`)],
    revoke: ['warn', 'pause', (e) => `Key <span class="mono">${esc(e.key)}</span> paused`],
    restore: ['ok', 'play', (e) => `Key <span class="mono">${esc(e.key)}</span> resumed`],
    user_pause: ['warn', 'pause', (e) => `User ${who(e)} paused`],
    user_resume: ['ok', 'play', (e) => `User ${who(e)} resumed`],
    user_release: ['warn', 'pc', (e) => `All computers of ${who(e)} released`],
    user_delete: ['bad', 'trash', (e) => `User ${who(e)} deleted`],
    user_block: ['bad', 'ban', (e) => `User ${who(e)} blocked`],
    user_unblock: ['ok', 'check', (e) => `User ${who(e)} unblocked`],
    user_edit: ['', 'user', (e) => `User ${who(e)} edited`],
    blocked: ['bad', 'ban', (e) => `<b>${esc(e.email)}</b> added to the block list${e.paused ? ` (${e.paused} licence${e.paused > 1 ? 's' : ''} paused)` : ''}`],
    unblocked: ['ok', 'check', (e) => `<b>${esc(e.email)}</b> removed from the block list`],
    bulk_pause: ['warn', 'pause', (e) => `${e.count} ${esc(e.what)} paused at once`],
    bulk_resume: ['ok', 'play', (e) => `${e.count} ${esc(e.what)} resumed at once`],
    bulk_release: ['warn', 'pc', (e) => `Computers released for ${e.count} ${esc(e.what)}`],
    bulk_delete: ['bad', 'trash', (e) => `${e.count} ${esc(e.what)} deleted at once`],
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
      k('', 'user', 'Users', c.users, `${c.computers} computer${c.computers === 1 ? '' : 's'} activated`),
      k('ok', 'bolt', 'New users this week', c.newUsers7, `${c.newUsers1} in the last 24 h`),
      k('ok', 'check', 'Active licences', c.active, `of ${c.total} licence${c.total === 1 ? '' : 's'}`),
      k('ok', 'pc', 'Online this week', c.online7, `${c.online1} in the last 24 h`),
      k(c.revoked + c.blocked + c.expired ? 'warn' : '', 'pause', 'Paused / blocked', c.revoked + c.blocked, `${c.revoked} paused · ${c.blocked} blocked${c.expired ? ` · ${c.expired} expired` : ''}`),
    ].join('');
    $('#newest').innerHTML = r.newest?.length ? r.newest.map((u) => `<li data-email="${esc(u.email)}">${avatar(u)}<div class="who"><b>${esc(u.name || u.email)}</b><small>${esc(u.email)}</small></div>${badge(u.status)}<small class="muted">${ago(u.createdAt)}</small></li>`).join('')
      : '<li class="empty" style="justify-content:center">No users yet.</li>';
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
    state.keyRows = rows;
    $('#key-table tbody').innerHTML = rows.map((l) => `<tr data-key="${esc(l.key)}" class="${state.selKeys.has(l.key) ? 'sel' : ''}">
      <td class="ck"><input type="checkbox" aria-label="Select ${esc(l.key)}" data-sel="${esc(l.key)}" ${state.selKeys.has(l.key) ? 'checked' : ''}></td>
      <td>${copyBtn(l.key)}${l.auto ? ' <span class="tag">auto</span>' : ''}</td>
      <td class="who"><b>${esc(l.name || '–')}</b><small>${esc(l.email || l.note || '')}</small></td>
      <td>${badge(l.status)}</td>
      <td><div class="meter"><em><i style="width:${Math.min(100, (l.deviceCount / l.maxDevices) * 100)}%"></i></em>${l.deviceCount} / ${l.maxDevices}</div></td>
      <td>${fmtDate(l.createdAt)}</td>
      <td>${l.lastSeen ? ago(l.lastSeen) : '<span class="muted">–</span>'}</td>
      <td><button class="btn small ghost" data-open="${esc(l.key)}">Open</button></td></tr>`).join('');
    $('#key-empty').hidden = rows.length > 0;
    if (!state.licences.length) $('#key-empty').innerHTML = 'No licence keys yet. Press <b>Add buyer</b> to add one.';
    else if (!rows.length) $('#key-empty').textContent = 'No keys match this search.';
    $('#key-all').checked = rows.length > 0 && rows.every((l) => state.selKeys.has(l.key));
    drawBulk('key');
  }
  $('#key-search').addEventListener('input', drawKeys);
  $('#key-all').addEventListener('change', (e) => { (state.keyRows || []).forEach((l) => (e.target.checked ? state.selKeys.add(l.key) : state.selKeys.delete(l.key))); drawKeys(); });
  $('#key-table').addEventListener('change', (e) => { const k = e.target.dataset?.sel; if (!k) return; e.target.checked ? state.selKeys.add(k) : state.selKeys.delete(k); e.target.closest('tr').classList.toggle('sel', e.target.checked); $('#key-all').checked = (state.keyRows || []).every((l) => state.selKeys.has(l.key)); drawBulk('key'); });

  // ---------- bulk actions (users and keys) ----------
  const BULK = {
    pause: ['Pause', 'pause', 'They stop working at the next check (within 12 hours online). You can resume them any time.'],
    resume: ['Resume', 'play', ''],
    release: ['Release computers', 'pc', 'Every computer on them has to activate again.'],
    delete: ['Delete', 'trash', 'They stop working and cannot be restored. Pausing is usually better.'],
  };
  function drawBulk(kind) {
    const set = kind === 'user' ? state.selUsers : state.selKeys, bar = $(`#${kind}-bulk`);
    bar.hidden = !set.size;
    $('.bulk-n', bar).textContent = `${set.size} selected`;
  }
  async function runBulk(kind, action) {
    const set = kind === 'user' ? state.selUsers : state.selKeys;
    const n = set.size, what = kind === 'user' ? `user${n === 1 ? '' : 's'}` : `licence${n === 1 ? '' : 's'}`;
    const [label, , warn] = BULK[action];
    if (warn && !(await confirmBox(`${label} ${n} ${what}?`, warn, label, action !== 'resume'))) return;
    try {
      const r = await api('bulk', { method: 'POST', body: { action, ...(kind === 'user' ? { emails: [...set] } : { keys: [...set] }) } });
      toast(`${label}: ${r.done} ${what}`);
      if (action === 'delete') set.clear();
      kind === 'user' ? renderUsers() : renderKeys();
    } catch (err) { toast(err.message, true); }
  }
  for (const kind of ['user', 'key']) {
    $(`#${kind}-bulk`).addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.clear != null) { (kind === 'user' ? state.selUsers : state.selKeys).clear(); kind === 'user' ? drawUsers() : drawKeys(); return; }
      if (b.dataset.bulk) runBulk(kind, b.dataset.bulk);
    });
  }
  $('#key-filter').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    state.filter = b.dataset.f; $$('#key-filter button').forEach((x) => x.classList.toggle('on', x === b)); drawKeys();
  });
  $('#key-table').addEventListener('click', (e) => { const tr = e.target.closest('tr[data-key]'); if (tr && !e.target.closest('[data-copy]')) openDrawer(tr.dataset.key); });

  async function newKey() {
    const st = state.settings;
    const p = openModal(`<h2>Add a paid buyer</h2><p class="muted">Once added, the buyer opens Lead Agent, types their name and this email, and it activates in a few seconds.</p>
      <div class="chips" id="new-mode" style="margin-top:14px"><button type="button" data-m="one" class="on">One buyer</button><button type="button" data-m="many">Paste many emails</button><button type="button" data-m="blank">Blank keys</button></div>
      <form id="new-form">
        <div class="row" data-for="one"><label>Buyer name<input name="name" placeholder="optional"></label><label>Buyer email<input name="email" type="email" placeholder="the email they paid with"></label></div>
        <label data-for="many" hidden>Buyer emails<textarea name="emails" rows="6" placeholder="one per line, e.g.&#10;jane@company.com&#10;Mike Lee &lt;mike@agency.com&gt;"></textarea><span class="hint">Emails that already have a licence are skipped.</span></label>
        <label data-for="blank" hidden>How many keys<input name="count" type="number" min="1" max="200" value="5"><span class="hint">Keys with no email, for resellers or bundles. Buyers activate these with the key.</span></label>
        <label>Note<input name="note" placeholder="e.g. paid via PayPal, order #1042"></label>
        <div class="row"><label>Computers<input name="maxDevices" type="number" min="1" max="50" value="${esc(st.defaultMaxDevices || 2)}"></label><label>Expires<input name="expires" type="date"><span class="hint">Leave empty for lifetime</span></label></div>
        <div class="modal-foot"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary" type="submit" id="new-go">Add buyer</button></div>
      </form>`);
    let mode = 'one';
    $('#new-mode').addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      mode = b.dataset.m;
      $$('#new-mode button').forEach((x) => x.classList.toggle('on', x === b));
      $$('#new-form [data-for]').forEach((el) => { el.hidden = el.dataset.for !== mode; });
      $('#new-go').textContent = { one: 'Add buyer', many: 'Add buyers', blank: 'Create keys' }[mode];
    });
    $('#new-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const exp = f.get('expires') ? new Date(`${f.get('expires')}T23:59:59`).getTime() : null;
      const common = { note: f.get('note'), maxDevices: Number(f.get('maxDevices')), expiresAt: exp };
      const body = mode === 'one' ? { ...common, name: f.get('name'), email: f.get('email'), count: 1 }
        : mode === 'many' ? { ...common, emails: f.get('emails') } : { ...common, count: Number(f.get('count')) };
      if (mode === 'one' && !String(f.get('email') || '').trim()) { toast('Enter the email the buyer paid with', true); return; }
      e.submitter.disabled = true;
      try {
        const r = await api('licences', { method: 'POST', body });
        closeModal(true); showCreated(r.licences, r, mode);
        if (state.view === 'keys') renderKeys(); else if (state.view === 'dashboard') renderDashboard();
      } catch (err) { toast(err.message, true); e.submitter.disabled = false; }
    });
    return p;
  }
  function showCreated(list, r = {}, mode = 'one') {
    if (mode === 'many') {
      const extra = [r.skipped?.length ? `${r.skipped.length} already had a licence: ${r.skipped.map(esc).join(', ')}` : '', r.invalid?.length ? `${r.invalid.length} not valid: ${r.invalid.map(esc).join(', ')}` : ''].filter(Boolean);
      openModal(`<h2>${list.length} buyer${list.length === 1 ? '' : 's'} added</h2><p class="muted">They can activate now with their name and email.</p>
        ${extra.length ? `<div class="msg-box">${extra.join('<br>')}</div>` : ''}
        <div class="keylist">${list.map((l) => `<div class="who"><b>${esc(l.email)}</b><small>${esc(l.key)}</small></div>`).join('')}</div>
        <div class="modal-foot"><button class="btn primary" data-close>Done</button></div>`);
      return;
    }
    if (list.length === 1) {
      const l = list[0]; const msg = fillTemplate(l);
      openModal(`<h2>${l.email ? 'Buyer added' : 'Key created'}</h2><p class="muted">${l.email ? `${esc(l.email)} can activate now with their name and email. Send them the Lead Agent zip with this message.` : 'Send this key to the buyer with the Lead Agent zip.'}</p>
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
    $('#d-kind').textContent = 'Licence key'; $('#d-key').className = 'mono';
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
        ${l.revoked ? '<button class="btn" data-act="restore">Resume key</button>' : '<button class="btn danger" data-act="revoke">Pause key</button>'}
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
        revoke: ['Pause this key?', `Lead Agent stops working for this buyer within ${state.settings.passDays || 7} days (within 12 hours on computers that are online). You can resume it any time.`, 'Pause', true],
        'release-all': ['Release all computers?', 'Every computer using this key has to enter it again.', 'Release all', true],
        delete: ['Delete this key for good?', 'The key stops working and cannot be restored. Pausing is usually better.', 'Delete', true],
      }[act];
      if (ask && !(await confirmBox(...ask))) return;
      try {
        await api(`licences/${encodeURIComponent(key)}/${act}`, { method: 'POST', body: {} });
        toast({ revoke: 'Key paused', restore: 'Key resumed', 'release-all': 'All computers released', delete: 'Key deleted' }[act]);
        if (act === 'delete') { closeDrawer(); renderKeys(); } else refreshAfter(key);
      } catch (err) { toast(err.message, true); }
    }));
  }
  function refreshAfter(key) { openDrawer(key); if (state.view === 'keys') renderKeys(); }

  // ---------- users ----------
  async function renderUsers() { const r = await api('users'); state.users = r.users; drawUsers(); }
  const USORT = {
    recent: (a, b) => Math.max(b.lastSeen, b.createdAt) - Math.max(a.lastSeen, a.createdAt),
    newest: (a, b) => b.createdAt - a.createdAt,
    name: (a, b) => (a.name || a.email).localeCompare(b.name || b.email),
    computers: (a, b) => b.computers - a.computers,
  };
  function drawUsers() {
    const q = $('#user-search').value.trim().toLowerCase();
    const f = state.userFilter;
    const rows = state.users.filter((u) => (f === 'all' || (f === 'auto' ? u.auto : f === 'manual' ? !u.auto : u.status === f))
      && (!q || [u.name, u.email, u.note, ...u.keys, ...u.browsers].join(' ').toLowerCase().includes(q))).sort(USORT[state.userSort]);
    state.userRows = rows;
    const counts = state.users.reduce((a, u) => { a.all++; a[u.status] = (a[u.status] || 0) + 1; a[u.auto ? 'auto' : 'manual']++; return a; }, { all: 0, auto: 0, manual: 0 });
    $$('#user-filter button').forEach((b) => { b.querySelector('em').textContent = counts[b.dataset.f] || 0; });
    $('#user-table tbody').innerHTML = rows.map((u) => `<tr data-email="${esc(u.email)}" class="${state.selUsers.has(u.email) ? 'sel' : ''}">
      <td class="ck"><input type="checkbox" aria-label="Select ${esc(u.email)}" data-sel="${esc(u.email)}" ${state.selUsers.has(u.email) ? 'checked' : ''}></td>
      <td><div class="person">${avatar(u)}<div class="who"><b>${esc(u.name || '–')}</b><small>${esc(u.email)}</small></div></div></td>
      <td>${badge(u.status)}${u.auto ? ' <span class="tag" title="Licence created automatically when they activated">auto</span>' : ''}</td>
      <td><div class="meter"><em><i style="width:${u.maxComputers ? Math.min(100, (u.computers / u.maxComputers) * 100) : 0}%"></i></em>${u.computers} / ${u.maxComputers}</div><small class="muted">${esc(u.browsers.join(', '))}</small></td>
      <td>${fmtDate(u.createdAt)}</td>
      <td>${u.lastSeen ? ago(u.lastSeen) : '<span class="muted">–</span>'}</td>
      <td class="row-acts">
        ${u.status === 'revoked' ? `<button class="icon-btn" title="Resume" data-uact="resume">${ICON.play}</button>` : u.status === 'blocked' ? '' : `<button class="icon-btn" title="Pause" data-uact="pause">${ICON.pause}</button>`}
        <button class="icon-btn" title="More" aria-label="More actions" data-umenu>${ICON.more}</button>
      </td></tr>`).join('');
    $('#user-empty').hidden = rows.length > 0;
    if (state.users.length && !rows.length) $('#user-empty').textContent = 'No users match this search.';
    $('#user-all').checked = rows.length > 0 && rows.every((u) => state.selUsers.has(u.email));
    drawBulk('user');
  }
  $('#user-search').addEventListener('input', drawUsers);
  $('#user-sort').addEventListener('change', (e) => { state.userSort = e.target.value; drawUsers(); });
  $('#user-filter').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    state.userFilter = b.dataset.f; $$('#user-filter button').forEach((x) => x.classList.toggle('on', x === b)); drawUsers();
  });
  $('#user-all').addEventListener('change', (e) => { (state.userRows || []).forEach((u) => (e.target.checked ? state.selUsers.add(u.email) : state.selUsers.delete(u.email))); drawUsers(); });
  $('#user-table').addEventListener('change', (e) => {
    const k = e.target.dataset?.sel; if (!k) return;
    e.target.checked ? state.selUsers.add(k) : state.selUsers.delete(k);
    e.target.closest('tr').classList.toggle('sel', e.target.checked);
    $('#user-all').checked = (state.userRows || []).every((u) => state.selUsers.has(u.email));
    drawBulk('user');
  });
  $('#user-table').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-email]'); if (!tr) return;
    const email = tr.dataset.email;
    if (e.target.closest('.ck') || e.target.closest('[data-copy]')) return;
    const act = e.target.closest('[data-uact]'); if (act) { userAction(email, act.dataset.uact); return; }
    const menu = e.target.closest('[data-umenu]'); if (menu) { e.stopPropagation(); openMenu(menu, email); return; }
    openUser(email);
  });
  $('#newest').addEventListener('click', (e) => { const li = e.target.closest('li[data-email]'); if (li) openUser(li.dataset.email); });

  // Row menu ("More")
  function openMenu(btn, email) {
    closeMenu();
    const u = state.users.find((x) => x.email === email) || {};
    const m = document.createElement('div');
    m.className = 'rmenu'; m.id = 'row-menu'; m.setAttribute('role', 'menu');
    m.innerHTML = `<button data-m="open">${ICON.user}Open profile</button>
      <button data-m="copy">${ICON.copy}Copy email</button>
      ${u.status === 'revoked' ? `<button data-m="resume">${ICON.play}Resume</button>` : u.status !== 'blocked' ? `<button data-m="pause">${ICON.pause}Pause</button>` : ''}
      ${u.computers ? `<button data-m="release">${ICON.pc}Release computers</button>` : ''}
      <hr>
      ${u.status === 'blocked' ? `<button data-m="unblock">${ICON.check}Unblock email</button>` : `<button data-m="block" class="danger">${ICON.ban}Block email</button>`}
      <button data-m="delete" class="danger">${ICON.trash}Delete user</button>`;
    document.body.append(m);
    const r = btn.getBoundingClientRect();
    m.style.top = `${Math.max(8, Math.min(window.innerHeight - m.offsetHeight - 8, r.bottom + 4))}px`;
    m.style.left = `${Math.max(8, r.right - m.offsetWidth)}px`;
    m.addEventListener('click', (e) => {
      e.stopPropagation();
      const b = e.target.closest('button'); if (!b) return;
      closeMenu();
      if (b.dataset.m === 'open') openUser(email);
      else if (b.dataset.m === 'copy') copy(email, 'Email copied');
      else userAction(email, b.dataset.m);
    });
    setTimeout(() => document.addEventListener('click', closeMenu, { once: true }), 0);
  }
  function closeMenu() { $('#row-menu')?.remove(); }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });

  const UACT = {
    pause: ['Pause this user?', 'Lead Agent stops working on their computers at the next check (within 12 hours when online). Resume any time.', 'Pause', true, 'User paused'],
    resume: [null, null, null, false, 'User resumed'],
    release: ['Release all their computers?', 'They have to activate again on each computer. Useful when someone changed computers.', 'Release', true, 'Computers released'],
    block: ['Block this email?', 'Their licence is paused, and this email can never activate or get a new licence again until you unblock it.', 'Block', true, 'Email blocked'],
    unblock: ['Unblock this email?', 'They can activate again and their licence is resumed.', 'Unblock', false, 'Email unblocked'],
    delete: ['Delete this user for good?', 'Their licence is removed and Lead Agent stops working for them. With automatic licences on they could sign up again with the same email; block the email instead to keep them out.', 'Delete', true, 'User deleted'],
  };
  async function userAction(email, act) {
    const [t, text, ok, danger, done] = UACT[act];
    if (t && !(await confirmBox(t, `${email}: ${text}`, ok, danger))) return false;
    try {
      await api(`users/${encodeURIComponent(email)}/${act}`, { method: 'POST', body: {} });
      toast(done);
      if (act === 'delete') { state.selUsers.delete(email); if (!$('#drawer').hidden) closeDrawer(); }
      else if (!$('#drawer').hidden && $('#drawer').dataset.email === email) openUser(email);
      if (state.view === 'users') renderUsers(); else if (state.view === 'dashboard') renderDashboard();
      return true;
    } catch (err) { toast(err.message, true); return false; }
  }

  // User profile (drawer)
  async function openUser(email) {
    $('#drawer').hidden = false; $('#scrim').hidden = false; $('#drawer').dataset.email = email;
    $('#d-kind').textContent = 'User'; $('#d-key').className = ''; $('#d-key').textContent = email;
    $('#d-body').innerHTML = '<div class="spinner" style="margin:40px auto"></div>';
    let r; try { r = await api(`users/${encodeURIComponent(email)}`); } catch (e) { $('#d-body').innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    const u = r.user, lic = r.licences;
    $('#d-key').textContent = u.name || u.email;
    $('#d-body').innerHTML = `
      <div class="profile">${avatar(u, true)}<div class="min0"><h3 style="margin:0 0 4px">${esc(u.name || '–')}</h3><button class="copy" data-copy="${esc(u.email)}" data-copy-label="Email copied">${ICON.mail}${esc(u.email)}</button>
        <div class="actions" style="margin-top:8px">${badge(u.status)}<span class="tag">${u.auto ? 'Signed up automatically' : 'Added by you'}</span></div></div></div>
      <div class="stats">
        <div><b>${u.computers}/${u.maxComputers}</b><small>Computers</small></div>
        <div><b>${lic.length}</b><small>Licence${lic.length === 1 ? '' : 's'}</small></div>
        <div><b>${fmtDate(u.createdAt)}</b><small>Signed up</small></div>
        <div><b>${u.lastSeen ? ago(u.lastSeen) : '–'}</b><small>Last seen</small></div>
      </div>
      <section class="actions">
        ${u.status === 'revoked' ? `<button class="btn" data-u="resume">${ICON.play}Resume</button>` : u.status !== 'blocked' ? `<button class="btn" data-u="pause">${ICON.pause}Pause</button>` : ''}
        ${u.computers ? `<button class="btn" data-u="release">${ICON.pc}Release computers</button>` : ''}
        ${r.blocked ? `<button class="btn" data-u="unblock">${ICON.check}Unblock</button>` : `<button class="btn danger" data-u="block">${ICON.ban}Block email</button>`}
        <button class="btn danger ghost" data-u="delete">${ICON.trash}Delete</button>
      </section>
      <section><h3>Profile</h3>
        <form id="u-edit" class="form-grid" style="grid-template-columns:1fr 1fr">
          <label>Name<input name="name" value="${esc(u.name)}"></label>
          <label>Computers allowed<input name="maxDevices" type="number" min="1" max="50" value="${lic[0]?.maxDevices || 2}"></label>
          <label class="full">Private note<input name="note" value="${esc(u.note)}" placeholder="e.g. paid via PayPal, order #1042"></label>
          <div class="full"><button class="btn small" type="submit">Save</button></div>
        </form></section>
      <section><h3>Licence${lic.length === 1 ? '' : 's'} and computers</h3>
        ${lic.map((l) => `<div class="lic"><div class="lic-head">${copyBtn(l.key)}${badge(l.status)}<button class="btn small ghost" data-openkey="${esc(l.key)}">Open key</button></div>
          ${l.devices.length ? l.devices.map((d) => `<div class="dev"><span class="dot" style="width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:var(--pri-soft);color:var(--pri)">${ICON.pc}</span>
            <div><b>${esc(d.label)}</b><small>${esc(d.userName || '')} · activated ${fmtDate(d.firstSeen)} · seen ${ago(d.lastSeen)}</small></div>
            <button class="btn small danger" data-release="${esc(d.id)}" data-key="${esc(l.key)}">Release</button></div>`).join('') : '<p class="muted small" style="margin:6px 0 0">Not activated on a computer yet.</p>'}</div>`).join('')}
      </section>
      <section><h3>History</h3><ul class="feed">${r.events.length ? r.events.map(feedItem).join('') : '<li class="muted">No events.</li>'}</ul></section>`;
    $$('[data-u]', $('#d-body')).forEach((b) => b.addEventListener('click', () => userAction(email, b.dataset.u)));
    $$('[data-openkey]', $('#d-body')).forEach((b) => b.addEventListener('click', () => openDrawer(b.dataset.openkey)));
    $$('[data-release]', $('#d-body')).forEach((b) => b.addEventListener('click', async () => {
      if (!(await confirmBox('Release this computer?', 'Lead Agent stops working on it until they activate again there.', 'Release'))) return;
      try { await api(`licences/${encodeURIComponent(b.dataset.key)}/release`, { method: 'POST', body: { device: b.dataset.release } }); toast('Computer released'); openUser(email); if (state.view === 'users') renderUsers(); } catch (err) { toast(err.message, true); }
    }));
    $('#u-edit').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      try { await api(`users/${encodeURIComponent(email)}/edit`, { method: 'POST', body: { name: f.get('name'), note: f.get('note'), maxDevices: Number(f.get('maxDevices')) } }); toast('Saved'); openUser(email); if (state.view === 'users') renderUsers(); } catch (err) { toast(err.message, true); }
    });
  }

  // ---------- block list (Settings) ----------
  async function renderBlocked() {
    const r = await api('blocked');
    $('#block-list').innerHTML = r.blocked.length ? r.blocked.map((b) => `<li><span class="mono">${esc(b.entry.startsWith('@') ? `*${b.entry}` : b.entry)}</span><small class="muted">${b.entry.startsWith('@') ? 'whole domain' : 'email'} · ${fmtDate(b.at)}</small><button class="btn small ghost" data-unblock="${esc(b.entry)}">Remove</button></li>`).join('')
      : '<li class="muted">Nothing blocked.</li>';
  }
  $('#block-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const v = e.target.elements.entry.value.trim(); if (!v) return;
    try { const r = await api('blocked', { method: 'POST', body: { add: v } }); e.target.reset(); toast(`${r.entry} blocked${r.paused ? `, ${r.paused} licence${r.paused > 1 ? 's' : ''} paused` : ''}`); renderBlocked(); } catch (err) { toast(err.message, true); }
  });
  $('#block-list').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-unblock]'); if (!b) return;
    try { await api('blocked', { method: 'POST', body: { remove: b.dataset.unblock } }); toast('Removed from the block list. Paused licences stay paused until you resume them.'); renderBlocked(); } catch (err) { toast(err.message, true); }
  });

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
    f.elements.ipapiKey.placeholder = st.ipapiKeySet ? 'Saved (type a new key to replace it)' : 'Not set';
    renderBlocked().catch(() => {});
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
