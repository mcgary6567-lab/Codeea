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
  if ('serviceWorker' in navigator && location.protocol === 'https:') { navigator.serviceWorker.register('/static/sw.js').catch(function(){}); }
})();
