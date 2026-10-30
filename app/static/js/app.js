/* OQC front-end helpers */
(function () {
  function icons() { if (window.lucide) { lucide.createIcons(); } }
  document.addEventListener('DOMContentLoaded', icons);
  document.addEventListener('htmx:afterSwap', icons);
  document.addEventListener('alpine:initialized', icons);
  // Clock
  function tick() { var el = document.getElementById('clock'); if (el) { el.textContent = new Date().toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short' }); } }
  tick(); setInterval(tick, 30000);
  // Chart defaults
  if (window.Chart) {
    var dark = document.documentElement.classList.contains('dark');
    Chart.defaults.font.family = 'Inter, system-ui, sans-serif';
    Chart.defaults.color = dark ? '#94a3b8' : '#64748b';
    Chart.defaults.borderColor = dark ? 'rgba(148,163,184,.15)' : 'rgba(100,116,139,.15)';
    Chart.defaults.plugins.legend.labels.boxWidth = 10;
  }
  window.OQC = {
    palette: ['#0d9488', '#6366f1', '#f59e0b', '#ef4444', '#0ea5e9', '#8b5cf6', '#10b981', '#f97316', '#64748b'],
    chart: function (id, config) { var el = document.getElementById(id); if (!el || !window.Chart) return null; return new Chart(el, config); },
    line: function (id, labels, datasets, opts) {
      datasets = datasets.map(function (d, i) { return Object.assign({ borderColor: OQC.palette[i % 9], backgroundColor: OQC.palette[i % 9] + '22', tension: .35, fill: true, pointRadius: 2 }, d); });
      return OQC.chart(id, { type: 'line', data: { labels: labels, datasets: datasets }, options: Object.assign({ responsive: true, maintainAspectRatio: false, plugins: { legend: { display: datasets.length > 1 } }, scales: { y: { beginAtZero: true, grid: { drawBorder: false } }, x: { grid: { display: false } } } }, opts || {}) });
    },
    bar: function (id, labels, datasets, opts) {
      datasets = datasets.map(function (d, i) { return Object.assign({ backgroundColor: OQC.palette[i % 9], borderRadius: 4, maxBarThickness: 36 }, d); });
      return OQC.chart(id, { type: 'bar', data: { labels: labels, datasets: datasets }, options: Object.assign({ responsive: true, maintainAspectRatio: false, plugins: { legend: { display: datasets.length > 1 } }, scales: { y: { beginAtZero: true }, x: { grid: { display: false } } } }, opts || {}) });
    },
    doughnut: function (id, labels, data, opts) {
      return OQC.chart(id, { type: 'doughnut', data: { labels: labels, datasets: [{ data: data, backgroundColor: OQC.palette, borderWidth: 0 }] }, options: Object.assign({ responsive: true, maintainAspectRatio: false, cutout: '68%', plugins: { legend: { position: 'bottom' } } }, opts || {}) });
    },
    post: function (url, data) { return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify(data || {}) }).then(function (r) { return r.json(); }); },
    toast: function (msg, level) {
      var c = { success: 'bg-emerald-600', error: 'bg-rose-600', info: 'bg-slate-800', warning: 'bg-amber-500' }[level || 'info'];
      var el = document.createElement('div'); el.className = 'fixed bottom-5 right-5 z-[100] rounded-lg px-4 py-2.5 text-sm text-white shadow-lg ' + c; el.textContent = msg; document.body.appendChild(el); setTimeout(function () { el.remove(); }, 3500);
    }
  };
  // Double-submit guard: once a POST form is submitted, its submit buttons are disabled and marked busy so a
  // slow action (bulk invoices, payroll approval, migration) cannot be fired twice. They come back after
  // 10 s in case the server never answered (network drop, a download response that keeps the page).
  document.addEventListener('submit', function (ev) {
    var form = ev.target;
    if (!form || !(form instanceof HTMLFormElement)) return;
    if ((form.getAttribute('method') || 'get').toLowerCase() !== 'post') return;
    if (ev.defaultPrevented || form.hasAttribute('data-allow-resubmit')) return;
    if (form.dataset.submitting === '1') { ev.preventDefault(); return; }
    form.dataset.submitting = '1';
    form.setAttribute('aria-busy', 'true');
    var buttons = form.querySelectorAll('button:not([type=button]):not([type=reset]), input[type=submit]');
    // Disable after the event finishes dispatching so the clicked button's name/value still reach the server.
    setTimeout(function () { buttons.forEach(function (b) { b.disabled = true; b.classList.add('is-busy'); }); }, 0);
    setTimeout(function () {
      delete form.dataset.submitting; form.removeAttribute('aria-busy');
      buttons.forEach(function (b) { b.disabled = false; b.classList.remove('is-busy'); });
    }, 10000);
  }, true);
  window.addEventListener('pageshow', function () {
    document.querySelectorAll('form[aria-busy="true"]').forEach(function (form) {
      delete form.dataset.submitting; form.removeAttribute('aria-busy');
      form.querySelectorAll('.is-busy').forEach(function (b) { b.disabled = false; b.classList.remove('is-busy'); });
    });
  });
  // Masked client contacts: View asks the server; it answers with the value, or sends a one-time code to the viewer
  // and this dialog takes the code (macros.html contact(), web/clients.py reveal_start / reveal_verify).
  var reveal = { btn: null, url: '', challenge: null };
  function rv(id) { return document.getElementById(id); }
  function revealPost(url, data) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Accept': 'application/json' }, body: new URLSearchParams(data || {}) })
      .then(function (r) { return r.json().catch(function () { return { error: 'The server did not answer (' + r.status + ').' }; }); });
  }
  function revealShow(open) {
    var d = rv('reveal-dialog'); if (!d) return;
    d.classList.toggle('hidden', !open); d.classList.toggle('flex', open);
    if (open) { if (!rv('reveal-code-row').hidden) rv('reveal-code').focus(); } else if (reveal.btn && reveal.btn.isConnected) { reveal.btn.focus(); }
  }
  function revealError(msg) { var e = rv('reveal-error'); e.textContent = msg || ''; e.classList.toggle('hidden', !msg); }
  function revealFill(btn, value) {
    var target = btn.dataset.revealInput && document.getElementById(btn.dataset.revealInput);
    if (target) { target.value = value; target.readOnly = false; target.dataset.revealed = '1'; btn.remove(); return; }
    var box = btn.closest('[data-reveal-box]') || btn.parentNode;
    box.textContent = value; box.classList.add('font-mono', 'text-xs');
  }
  document.addEventListener('click', function (ev) {
    if (ev.target.closest('[data-reveal-close]')) { revealShow(false); return; }
    var btn = ev.target.closest('[data-reveal]');
    if (!btn) return;
    ev.preventDefault();
    reveal.btn = btn; reveal.url = btn.dataset.reveal; reveal.challenge = null;
    btn.disabled = true;
    revealPost(reveal.url + '/start').then(function (res) {
      btn.disabled = false;
      if (res.value !== undefined) { revealFill(btn, res.value); return; }
      revealError(res.error || '');
      rv('reveal-code').value = '';
      rv('reveal-code-row').hidden = !res.challenge;
      rv('reveal-submit').hidden = rv('reveal-code-row').hidden;
      reveal.challenge = res.challenge || null;
      var msg = '';
      if (res.challenge && res.sent_to) {
        msg = 'A code to open ' + res.label + ' was sent by ' + res.channel + ' to ' + res.sent_to + '.';
        if (res.simulated) { msg = 'Sending by ' + res.channel + ' is not connected on this server, so no code was delivered.' + (res.totp ? ' Type the code from your authenticator app.' : ' Ask the administrator to connect it in Configuration.'); }
        else if (res.totp) { msg += ' Your authenticator app code works too.'; }
      }
      rv('reveal-msg').textContent = msg;
      revealShow(true);
    }).catch(function () { btn.disabled = false; OQC.toast('Could not reach the server.', 'error'); });
  });
  document.addEventListener('submit', function (ev) {
    if (ev.target.id !== 'reveal-form') return;
    ev.preventDefault(); ev.stopImmediatePropagation();
    revealPost(reveal.url + '/verify', { challenge: reveal.challenge || '', code: rv('reveal-code').value }).then(function (res) {
      if (res.value !== undefined) { revealError(''); revealShow(false); revealFill(reveal.btn, res.value); return; }
      revealError(res.error || 'The code was not accepted.');
    });
  }, true);
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && rv('reveal-dialog') && !rv('reveal-dialog').classList.contains('hidden')) revealShow(false);
  });
  if ('serviceWorker' in navigator && location.protocol === 'https:') { navigator.serviceWorker.register('/static/sw.js').catch(function(){}); }
})();
