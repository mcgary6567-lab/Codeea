/* Interactive reports: the Oracle-APEX style toolbar on every list table (docs/INTERACTIVE_REPORTS.md).
 *
 * Attaches to every <table data-ir> (macros.html table_start). The main table of a page gets the full toolbar:
 * search, Rows, Actions (Columns, Filter, Data, Format, Chart, Group By, Pivot, Report, Download, Subscription,
 * Help), Export CSV, Import CSV and row checkboxes. Other tables on the page get a compact Actions menu.
 *
 * Everything runs on the rows the server sent, so a user can only ever work with what they may see. Rows=All and
 * the "All records" exports fetch the same page again (same filters, same permissions) in chunks of 1000.
 * Settings live in localStorage per page; saved reports and subscriptions on the server (/ir/...).
 */
(function () {
  'use strict';

  var PATH = location.pathname;
  var ROW_CHOICES = ['10', '25', '50', '100', '500', '1000', 'all'];
  var CHUNK = 1000;
  var OPS = [['contains', 'contains'], ['not_contains', 'does not contain'], ['eq', 'equals'], ['neq', 'not equal'],
             ['gt', 'greater than'], ['gte', 'at least'], ['lt', 'less than'], ['lte', 'at most'],
             ['starts', 'starts with'], ['empty', 'is empty'], ['not_empty', 'is not empty']];
  var FUNCS = [['sum', 'Sum'], ['avg', 'Average'], ['count', 'Count'], ['distinct', 'Count distinct'], ['min', 'Minimum'], ['max', 'Maximum']];
  var COLORS = [['#fef9c3', 'Yellow'], ['#dcfce7', 'Green'], ['#fee2e2', 'Red'], ['#dbeafe', 'Blue'], ['#f3e8ff', 'Purple'], ['#ffedd5', 'Orange']];

  // ------------------------------------------------------------------------------------------------ helpers
  function txt(el) { return el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : ''; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function store(key, value) {
    try {
      if (value === undefined) { return JSON.parse(localStorage.getItem(key) || 'null'); }
      if (value === null) { localStorage.removeItem(key); } else { localStorage.setItem(key, JSON.stringify(value)); }
    } catch (e) { return null; }
  }
  // 'PKR 1,250.50', '£12', '45%' -> 1250.5, 12, 45; null when the cell is not a number (mirrors services/ir.py number)
  function num(s) {
    s = String(s == null ? '' : s).trim();
    if (!s || !/\d/.test(s)) { return null; }
    // a currency in front ('PKR 1,250', '£12') or a unit behind ('45%'); never a code's letters ('C-00045')
    var t = s.replace(/^(?:[A-Za-z]{3}\s+|[£$€₨]\s*)|\s*(?:%|[A-Za-z]{3})$/g, '').replace(/,/g, '');
    return /^-?\d+(\.\d+)?$/.test(t) ? parseFloat(t) : null;
  }
  function dateVal(s) {
    if (!/\d/.test(s) || !/[a-z]{3}|\d[-/]\d/i.test(s)) { return null; }
    var d = Date.parse(s);
    return isNaN(d) ? null : d;
  }
  function cmp(a, b) {
    var na = num(a), nb = num(b);
    if (na !== null && nb !== null) { return na - nb; }
    var da = dateVal(a), db = dateVal(b);
    if (da !== null && db !== null) { return da - db; }
    return String(a).localeCompare(String(b), undefined, {numeric: true, sensitivity: 'base'});
  }
  function matches(cell, op, value) {
    var a = String(cell || '').trim(), b = String(value || '').trim(), al = a.toLowerCase(), bl = b.toLowerCase();
    switch (op) {
      case 'empty': return a === '';
      case 'not_empty': return a !== '';
      case 'contains': return al.indexOf(bl) !== -1;
      case 'not_contains': return al.indexOf(bl) === -1;
      case 'starts': return al.indexOf(bl) === 0;
      case 'eq': case 'neq':
        var na = num(a), nb = num(b), same = (na !== null && nb !== null) ? na === nb : al === bl;
        return op === 'eq' ? same : !same;
      default:
        var c = cmp(a, b);
        return {gt: c > 0, gte: c >= 0, lt: c < 0, lte: c <= 0}[op];
    }
  }
  function aggregate(values, fn) {
    if (fn === 'count') { return values.filter(function (v) { return v !== ''; }).length; }
    if (fn === 'distinct') { return new Set(values.filter(function (v) { return v !== ''; })).size; }
    var nums = values.map(num).filter(function (v) { return v !== null; });
    if (!nums.length) { return ''; }
    if (fn === 'sum') { return round(nums.reduce(function (s, v) { return s + v; }, 0)); }
    if (fn === 'avg') { return round(nums.reduce(function (s, v) { return s + v; }, 0) / nums.length); }
    if (fn === 'min') { return Math.min.apply(null, nums); }
    if (fn === 'max') { return Math.max.apply(null, nums); }
    return '';
  }
  function round(v) { return Math.round(v * 100) / 100; }
  function fmt(v) { return typeof v === 'number' ? v.toLocaleString(undefined, {maximumFractionDigits: 2}) : v; }
  function csv(headers, rows) {
    var cell = function (v) { v = String(v == null ? '' : v); return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
    return '﻿' + [headers].concat(rows).map(function (r) { return r.map(cell).join(','); }).join('\r\n');
  }
  function download(name, content, type) {
    var blob = content instanceof Blob ? content : new Blob([content], {type: type || 'text/csv;charset=utf-8'});
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
  function slug(s) { return (s || 'report').replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '').toLowerCase().slice(0, 60) || 'report'; }
  function today() { return new Date().toISOString().slice(0, 10); }
  function title() {
    var h = document.querySelector('main h1, h1');
    return txt(h) || document.title.split('·')[0].trim() || 'Report';
  }
  function icons() { if (window.lucide) { window.lucide.createIcons(); } }
  // Alpine watches the DOM (tbody carries x-data); thousands of row moves through its observer take a minute.
  function quiet(fn) { return (window.Alpine && window.Alpine.mutateDom) ? window.Alpine.mutateDom(fn) : fn(); }
  function api(method, url, body) {
    return fetch(url, {method: method, credentials: 'same-origin', headers: body ? {'Content-Type': 'application/json'} : {},
                       body: body ? JSON.stringify(body) : undefined})
      .then(function (r) {
        if (!r.ok) { return r.json().catch(function () { return {}; }).then(function (j) { throw new Error(j.detail || ('Request failed (' + r.status + ')')); }); }
        var ct = r.headers.get('content-type') || '';
        return ct.indexOf('json') !== -1 ? r.json() : r.blob();
      });
  }
  function toast(msg, bad) {
    var t = document.createElement('div');
    t.className = 'fixed bottom-4 right-4 z-[60] max-w-sm rounded-md px-4 py-2.5 text-sm text-white shadow-lg ' + (bad ? 'bg-rose-600' : 'bg-slate-800');
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, bad ? 6000 : 3500);
  }
  function options(list, selected) {
    return list.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (String(o[0]) === String(selected) ? ' selected' : '') + '>' + esc(o[1]) + '</option>'; }).join('');
  }
  var FIELD = 'w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-sm';
  var BTN = 'inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium';

  // A dialog: fields html, then onOk(form) -> false keeps it open. Returns the dialog element.
  function dialog(heading, body, onOk, okLabel, wide) {
    var wrap = document.createElement('div');
    wrap.className = 'fixed inset-0 z-50 flex items-center justify-center p-4';
    wrap.innerHTML = '<div class="absolute inset-0 bg-slate-900/50" data-close></div>' +
      '<form class="relative max-h-[90vh] w-full ' + (wide ? 'max-w-4xl' : 'max-w-lg') + ' overflow-y-auto rounded-md bg-white shadow-2xl" role="dialog" aria-modal="true">' +
      '<div class="flex items-center justify-between border-b border-slate-200 bg-brand-600 px-4 py-2.5 text-white"><h3 class="font-semibold">' + esc(heading) + '</h3>' +
      '<button type="button" data-close class="rounded p-0.5 hover:bg-white/15" aria-label="Close"><i data-lucide="x" class="h-5 w-5"></i></button></div>' +
      '<div class="space-y-3 p-4 text-sm">' + body + '</div>' +
      (onOk ? '<div class="flex justify-end gap-2 border-t border-slate-200 px-4 py-3"><button type="button" data-close class="' + BTN + ' border border-slate-300 bg-white text-slate-700 hover:bg-slate-50">Cancel</button>' +
              '<button type="submit" class="' + BTN + ' bg-brand-600 text-white hover:bg-brand-700">' + esc(okLabel || 'Apply') + '</button></div>' : '') +
      '</form>';
    var close = function () { wrap.remove(); document.removeEventListener('keydown', onKey); };
    var onKey = function (e) { if (e.key === 'Escape') { close(); } };
    wrap.addEventListener('click', function (e) { if (e.target.closest('[data-close]')) { close(); } });
    wrap.querySelector('form').addEventListener('submit', function (e) {
      e.preventDefault();
      if (!onOk || onOk(e.target) !== false) { close(); }
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(wrap);
    icons();
    var first = wrap.querySelector('form .p-4 input, form .p-4 select, form .p-4 textarea');
    if (first) { first.focus(); }
    wrap.close = close;
    return wrap;
  }

  // ------------------------------------------------------------------------------------------------ the report
  function Report(table, index, isMain) {
    this.table = table;
    this.index = index;
    this.main = isMain;
    this.wrap = table.parentElement;
    this.key = 'oqc.ir.' + PATH + '.' + index;
    var head = table.tHead && table.tHead.rows[0];
    this.headers = head ? Array.prototype.map.call(head.cells, txt) : [];
    var body = table.tBodies[0];
    this.trs = body ? Array.prototype.filter.call(body.rows, function (tr) { return !(tr.cells.length === 1 && tr.cells[0].colSpan > 1); }) : [];
    // a column of buttons is left out of downloads: no heading, or "Action(s)" over cells that hold only controls
    // (the audit log's "Action" column is data: login, create...)
    var sample = this.trs.slice(0, 25);
    this.actionCols = this.headers.map(function (h, i) {
      if (!h) { return i; }
      if (!/^actions?$/i.test(h) || !sample.length) { return -1; }
      var onlyControls = sample.every(function (tr) {
        var td = tr.cells[i];
        if (!td) { return true; }
        var controls = td.querySelectorAll('a, button, form, select, input');
        var rest = txt(td);
        Array.prototype.forEach.call(controls, function (c) { rest = rest.replace(txt(c), ''); });
        return controls.length > 0 && !rest.trim();
      });
      return onlyControls ? i : -1;
    }).filter(function (i) { return i >= 0; });
    this.data = this.trs.map(function (tr) { return Array.prototype.map.call(tr.cells, txt); });
    this.pager = this.findPager();
    this.total = this.pager ? parseInt(this.pager.getAttribute('data-total'), 10) || this.trs.length : this.trs.length;
    this.allRecords = false;
    var saved = store(this.key);
    this.state = this.blank();
    if (saved && typeof saved === 'object') { for (var k in saved) { if (k in this.state) { this.state[k] = saved[k]; } } }
    this.state.view = 'report';
    this.lastSort = this.lastHl = this.lastHidden = '[]';  // the page as the server drew it
    this.build();
    this.apply();
    if (this.main && !saved) { this.loadDefault(); }
  }

  Report.prototype.blank = function () {
    return {hidden: [], filters: [], sort: [], highlights: [], aggregates: [], search: '', limit: null, view: 'report', group: null, pivot: null};
  };

  Report.prototype.findPager = function () {
    var el = this.wrap.nextElementSibling;
    for (var i = 0; el && i < 3; i++, el = el.nextElementSibling) {
      if (el.matches('[data-ir-pager]')) { return el; }
      if (el.querySelector && el.querySelector('table[data-ir]')) { return null; }
      var inner = el.querySelector && el.querySelector('[data-ir-pager]');
      if (inner) { return inner; }
    }
    return null;
  };

  Report.prototype.label = function (i) { return this.headers[i] || ('Column ' + (i + 1)); };
  Report.prototype.dataCols = function () {
    var self = this;
    return this.headers.map(function (h, i) { return i; }).filter(function (i) { return self.actionCols.indexOf(i) === -1; });
  };
  Report.prototype.colOptions = function (selected, withCount) {
    var self = this;
    var list = this.dataCols().map(function (i) { return [i, self.label(i)]; });
    if (withCount) { list.unshift(['', '(row count)']); }
    return options(list, selected);
  };

  // ---- toolbar
  Report.prototype.build = function () {
    var self = this;
    var bar = document.createElement('div');
    bar.className = 'ir-bar mb-2 flex flex-wrap items-center gap-2 text-xs';
    var rowsNow = new URLSearchParams(location.search).get('rows') || (this.pager ? this.pager.getAttribute('data-per-page') : (this.state.limit || 'all'));
    var html = '';
    if (this.main) {
      html += '<label class="relative"><span class="sr-only">Search the loaded rows</span><i data-lucide="search" class="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"></i>' +
              '<input type="search" data-ir-search placeholder="Search rows" value="' + esc(this.state.search) + '" class="w-48 rounded border border-slate-300 bg-white py-1.5 pl-7 pr-2 text-xs"></label>' +
              '<label class="inline-flex items-center gap-1 text-slate-600">Rows <select data-ir-rows class="rounded border border-slate-300 bg-white px-1.5 py-1.5 text-xs">' +
              options(ROW_CHOICES.map(function (r) { return [r, r === 'all' ? 'All' : r]; }).concat(ROW_CHOICES.indexOf(String(rowsNow)) === -1 ? [[rowsNow, rowsNow]] : []), String(rowsNow).toLowerCase()) +
              '</select></label>';
    }
    html += '<div class="relative" data-ir-menu-wrap><button type="button" data-ir-actions class="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 font-medium text-slate-700 hover:bg-slate-50" aria-haspopup="true">' +
            (this.main ? 'Actions' : '<i data-lucide="sliders-horizontal" class="h-3.5 w-3.5"></i>Report') + '<i data-lucide="chevron-down" class="h-3.5 w-3.5"></i></button>' +
            '<div data-ir-menu class="absolute left-0 z-40 mt-1 hidden w-56 rounded-md border border-slate-200 bg-white py-1 text-sm shadow-xl"></div></div>';
    var mayExport = !document.body.hasAttribute('data-ir-no-export');
    if (this.main && mayExport) {
      html += '<div class="relative" data-ir-menu-wrap><button type="button" data-ir-export class="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 font-medium text-slate-700 hover:bg-slate-50">' +
              '<i data-lucide="file-down" class="h-3.5 w-3.5"></i>Export CSV<i data-lucide="chevron-down" class="h-3.5 w-3.5"></i></button>' +
              '<div data-ir-menu class="absolute left-0 z-40 mt-1 hidden w-64 rounded-md border border-slate-200 bg-white py-1 text-sm shadow-xl">' +
              this.item('export:all', 'database', 'All records', (this.pager ? this.total + ' rows, every page' : this.trs.length + ' rows')) +
              this.item('export:filtered', 'filter', 'Filtered records', 'with your search and filters') +
              this.item('export:selected', 'check-square', 'Selected records', 'the rows you ticked') + '</div></div>';
    }
    if (this.main) {
      var imp = document.body.getAttribute('data-ir-import');
      if (imp) {
        html += '<a data-ir-import href="/admin/migration?entity=' + encodeURIComponent(imp) + '&back=' + encodeURIComponent(location.pathname + location.search) +
                '" class="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1.5 font-medium text-slate-700 hover:bg-slate-50" title="Bulk upload ' +
                esc(document.body.getAttribute('data-ir-import-label') || '') + ' from a CSV or Excel file"><i data-lucide="upload" class="h-3.5 w-3.5"></i>Import CSV</a>';
      }
    }
    html += '<span data-ir-chips class="flex flex-wrap items-center gap-1"></span><span data-ir-count class="ml-auto text-slate-500"></span>';
    bar.innerHTML = html;
    this.wrap.parentNode.insertBefore(bar, this.wrap);
    this.bar = bar;
    this.note = document.createElement('div');
    this.note.className = 'mb-2 hidden rounded border border-brand-200 bg-brand-50 px-3 py-2 text-xs text-brand-800';
    this.wrap.parentNode.insertBefore(this.note, this.wrap);
    this.panel = document.createElement('div');
    this.panel.className = 'hidden';
    this.wrap.parentNode.insertBefore(this.panel, this.wrap);
    this.fillActions();
    if (this.main) { this.addCheckboxes(); this.carryRows(); }
    bar.addEventListener('click', function (e) { self.onClick(e); });
    document.addEventListener('click', function (e) { if (!e.target.closest('[data-ir-menu-wrap]')) { self.closeMenus(); } });
    var search = bar.querySelector('[data-ir-search]');
    if (search) {
      var t;
      search.addEventListener('input', function () { clearTimeout(t); t = setTimeout(function () { self.state.search = search.value; self.apply(); }, 150); });
    }
    var rows = bar.querySelector('[data-ir-rows]');
    if (rows) { rows.addEventListener('change', function () { self.setRows(rows.value); }); }
    icons();
  };

  Report.prototype.item = function (action, icon, label, hint) {
    return '<button type="button" data-ir-do="' + action + '" class="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-brand-50">' +
           '<i data-lucide="' + icon + '" class="h-4 w-4 text-slate-500"></i><span class="flex-1">' + esc(label) +
           (hint ? '<span class="block text-[11px] text-slate-400">' + esc(hint) + '</span>' : '') + '</span></button>';
  };
  Report.prototype.group = function (key, icon, label, items) {
    return '<div data-ir-group><button type="button" data-ir-toggle="' + key + '" class="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-brand-50">' +
           '<i data-lucide="' + icon + '" class="h-4 w-4 text-slate-500"></i><span class="flex-1">' + esc(label) + '</span><i data-lucide="chevron-right" class="h-3.5 w-3.5 text-slate-400"></i></button>' +
           '<div data-ir-sub="' + key + '" class="hidden border-y border-slate-100 bg-slate-50 pl-4">' + items + '</div></div>';
  };
  Report.prototype.fillActions = function () {
    var m = this.bar.querySelector('[data-ir-actions]').nextElementSibling, h = '';
    h += this.item('columns', 'columns-3', 'Columns');
    h += this.item('filter', 'filter', 'Filter');
    h += this.group('data', 'table', 'Data', this.item('sort', 'arrow-up-down', 'Sort') + this.item('aggregate', 'sigma', 'Aggregate'));
    h += this.group('format', 'paintbrush', 'Format', this.item('highlight', 'highlighter', 'Highlight') + (this.main ? this.item('rows', 'rows-3', 'Rows per page') : ''));
    h += '<div class="my-1 border-t border-slate-100"></div>';
    h += this.item('chart', 'bar-chart-3', 'Chart') + this.item('groupby', 'group', 'Group By') + this.item('pivot', 'grid-3x3', 'Pivot');
    h += '<div class="my-1 border-t border-slate-100"></div>';
    h += this.group('report', 'bookmark', 'Report', this.item('save', 'save', 'Save report') + this.item('saved', 'folder-open', 'Saved reports') + this.item('reset', 'rotate-ccw', 'Reset'));
    if (!document.body.hasAttribute('data-ir-no-export')) {
      h += this.group('download', 'download', 'Download', this.item('dl:csv', 'file-text', 'CSV', 'current view') + this.item('dl:xlsx', 'file-spreadsheet', 'Excel (.xlsx)', 'current view') +
                      this.item('dl:print', 'printer', 'Print / PDF', 'current view'));
      h += this.item('subscription', 'mail', 'Subscription', 'e-mail this report on a schedule');
    }
    h += '<div class="my-1 border-t border-slate-100"></div>' + this.item('help', 'circle-help', 'Help');
    m.innerHTML = h;
  };

  Report.prototype.closeMenus = function () {
    this.bar.querySelectorAll('[data-ir-menu]').forEach(function (m) { m.classList.add('hidden'); });
  };

  Report.prototype.onClick = function (e) {
    var btn = e.target.closest('button, a');
    if (!btn) { return; }
    if (btn.hasAttribute('data-ir-actions') || btn.hasAttribute('data-ir-export')) {
      var menu = btn.nextElementSibling, open = menu.classList.contains('hidden');
      this.closeMenus();
      if (open) { menu.classList.remove('hidden'); }
      return;
    }
    if (btn.hasAttribute('data-ir-toggle')) {
      var sub = this.bar.querySelector('[data-ir-sub="' + btn.getAttribute('data-ir-toggle') + '"]');
      sub.classList.toggle('hidden');
      return;
    }
    if (btn.hasAttribute('data-ir-chip')) {
      var parts = btn.getAttribute('data-ir-chip').split(':');
      this.state[parts[0]].splice(parseInt(parts[1], 10), 1);
      this.apply();
      return;
    }
    if (btn.hasAttribute('data-ir-clear-search')) { this.state.search = ''; var s = this.bar.querySelector('[data-ir-search]'); if (s) { s.value = ''; } this.apply(); return; }
    var act = btn.getAttribute('data-ir-do');
    if (!act) { return; }
    this.closeMenus();
    this.run(act);
  };

  Report.prototype.run = function (act) {
    var self = this;
    var go = {
      columns: function () { self.dlgColumns(); }, filter: function () { self.dlgFilter(); }, sort: function () { self.dlgSort(); },
      aggregate: function () { self.dlgAggregate(); }, highlight: function () { self.dlgHighlight(); }, rows: function () { self.dlgRows(); },
      chart: function () { self.dlgChart(); }, groupby: function () { self.dlgGroup(); }, pivot: function () { self.dlgPivot(); },
      save: function () { self.dlgSave(); }, saved: function () { self.dlgSaved(); },
      reset: function () { self.state = self.blank(); store(self.key, null); self.apply(); toast('Report reset.'); },
      subscription: function () { self.dlgSubscription(); }, help: function () { self.dlgHelp(); },
      'dl:csv': function () { self.exportView('csv'); }, 'dl:xlsx': function () { self.exportView('xlsx'); }, 'dl:print': function () { self.print(); },
      'export:all': function () { self.exportScope('all'); }, 'export:filtered': function () { self.exportScope('filtered'); },
      'export:selected': function () { self.exportScope('selected'); }
    }[act];
    if (go) { go(); }
  };

  // ---- applying the settings to the table on screen
  Report.prototype.keep = function (row) {
    var st = this.state, q = (st.search || '').trim().toLowerCase();
    if (q && !row.some(function (c) { return c.toLowerCase().indexOf(q) !== -1; })) { return false; }
    for (var i = 0; i < st.filters.length; i++) {
      var f = st.filters[i];
      if (!matches(row[f.col] || '', f.op, f.value)) { return false; }
    }
    return true;
  };
  Report.prototype.sortRows = function (list, get) {
    var sort = this.state.sort;
    if (!sort.length) { return list; }
    return list.slice().sort(function (a, b) {
      for (var i = 0; i < sort.length; i++) {
        var c = cmp(get(a)[sort[i].col] || '', get(b)[sort[i].col] || '');
        if (c) { return sort[i].dir === 'desc' ? -c : c; }
      }
      return 0;
    });
  };
  Report.prototype.visibleCols = function () {
    var hidden = this.state.hidden;
    return this.headers.map(function (h, i) { return i; }).filter(function (i) { return hidden.indexOf(i) === -1; });
  };

  Report.prototype.apply = function () {
    var self = this;
    quiet(function () { self.paint(); });
    this.after();
  };

  // only what changed is written: rows move when the sort changes, cells when columns or highlights change
  Report.prototype.paint = function () {
    var self = this, st = this.state, data = this.data;
    var sortKey = JSON.stringify(st.sort), hlKey = JSON.stringify(st.highlights), hidKey = JSON.stringify(st.hidden);
    var order = this.sortRows(this.trs.map(function (tr, i) { return i; }), function (i) { return data[i]; });
    var shown = 0, repaint = hlKey !== this.lastHl, hadHl = this.lastHl && this.lastHl !== '[]';
    order.forEach(function (i) {
      var tr = self.trs[i], ok = self.keep(data[i]);
      if (ok && !self.pager && st.limit && st.limit !== 'all' && shown >= parseInt(st.limit, 10)) { ok = false; }
      var want = ok ? '' : 'none';
      if (tr.style.display !== want) { tr.style.display = want; }
      if (ok) { shown++; }
      if (repaint) {
        if (hadHl) { tr.style.backgroundColor = ''; Array.prototype.forEach.call(tr.cells, function (td) { td.style.backgroundColor = ''; }); }
        for (var h = 0; h < st.highlights.length; h++) {
          var hl = st.highlights[h];
          if (matches(data[i][hl.col] || '', hl.op, hl.value)) {
            if (hl.scope === 'cell') { var td = self.cell(tr, hl.col); if (td) { td.style.backgroundColor = hl.color; } } else { tr.style.backgroundColor = hl.color; }
            break;
          }
        }
      }
    });
    if (sortKey !== this.lastSort) {
      // rows the report does not manage (the "nothing here" row) stay first
      var frag = document.createDocumentFragment();
      order.forEach(function (i) { frag.appendChild(self.trs[i]); });
      this.table.tBodies[0].appendChild(frag);
    }
    if (hidKey !== this.lastHidden) {
      var hidden = st.hidden;
      var head = this.table.tHead && this.table.tHead.rows[0];
      if (head) { this.dataCells(head).forEach(function (th, i) { th.style.display = hidden.indexOf(i) === -1 ? '' : 'none'; }); }
      this.trs.forEach(function (tr) { self.dataCells(tr).forEach(function (td, i) { td.style.display = hidden.indexOf(i) === -1 ? '' : 'none'; }); });
    }
    this.lastSort = sortKey;
    this.lastHl = hlKey;
    this.lastHidden = hidKey;
    this.shown = shown;
    this.footer();
  };

  Report.prototype.after = function () {
    var self = this, st = this.state, shown = this.shown;
    this.chips();
    var count = this.bar.querySelector('[data-ir-count]');
    var loaded = this.trs.length;
    count.textContent = (shown === loaded ? loaded + ' row' + (loaded === 1 ? '' : 's') : shown + ' of ' + loaded + ' rows') +
                        (this.pager && this.total > loaded ? ' on this page · ' + this.total + ' in all' : '') + this.selectedLabel();
    // remembered on this computer: columns, filters, sort, highlights, totals (not the passing search text)
    var st2 = {};
    for (var k in st) { if (k !== 'view' && k !== 'search') { st2[k] = st[k]; } }
    store(this.key, st2);
    if (st.view === 'group' && st.group) { this.renderGroup(); } else if (st.view === 'pivot' && st.pivot) { this.renderPivot(); } else { this.showReport(); }
  };

  Report.prototype.dataCells = function (tr) {
    return Array.prototype.filter.call(tr.cells, function (c) { return !c.hasAttribute('data-ir-sel'); });
  };
  Report.prototype.cell = function (tr, col) { return this.dataCells(tr)[col]; };

  Report.prototype.footer = function () {
    var old = this.table.querySelector('tfoot[data-ir-foot]');
    if (old) { old.remove(); }
    var aggs = this.state.aggregates;
    if (!aggs.length) { return; }
    var self = this, rows = this.data.filter(function (r, i) { return self.trs[i].style.display !== 'none'; });
    var foot = document.createElement('tfoot');
    foot.setAttribute('data-ir-foot', '');
    var tr = document.createElement('tr');
    tr.className = 'bg-slate-50 font-semibold';
    if (this.main) { var sel = document.createElement('td'); sel.setAttribute('data-ir-sel', ''); sel.className = 'border border-slate-200'; tr.appendChild(sel); }
    this.headers.forEach(function (h, i) {
      var td = document.createElement('td');
      td.className = 'border border-slate-200 px-3 py-1.5 text-xs';
      if (self.state.hidden.indexOf(i) !== -1) { td.style.display = 'none'; }
      var parts = aggs.filter(function (a) { return a.col === i; }).map(function (a) {
        var label = FUNCS.filter(function (f) { return f[0] === a.fn; })[0][1];
        return label + ': ' + fmt(aggregate(rows.map(function (r) { return r[i] || ''; }), a.fn));
      });
      td.textContent = parts.join(' · ');
      tr.appendChild(td);
    });
    foot.appendChild(tr);
    this.table.appendChild(foot);
  };

  Report.prototype.chips = function () {
    var self = this, st = this.state, h = '';
    var chip = function (key, i, label) {
      return '<button type="button" data-ir-chip="' + key + ':' + i + '" class="inline-flex items-center gap-1 rounded-full border border-brand-200 bg-brand-50 px-2 py-0.5 text-[11px] text-brand-800 hover:bg-brand-100" title="Remove">' +
             esc(label) + '<span aria-hidden="true">×</span></button>';
    };
    st.filters.forEach(function (f, i) {
      var op = OPS.filter(function (o) { return o[0] === f.op; })[0];
      h += chip('filters', i, self.label(f.col) + ' ' + (op ? op[1] : f.op) + (f.op === 'empty' || f.op === 'not_empty' ? '' : ' "' + f.value + '"'));
    });
    st.sort.forEach(function (s, i) { h += chip('sort', i, 'Sort: ' + self.label(s.col) + (s.dir === 'desc' ? ' ↓' : ' ↑')); });
    st.highlights.forEach(function (x, i) { h += chip('highlights', i, 'Highlight: ' + self.label(x.col)); });
    st.aggregates.forEach(function (a, i) { h += chip('aggregates', i, a.fn + '(' + self.label(a.col) + ')'); });
    if (st.hidden.length) { h += '<span class="text-[11px] text-slate-500">' + st.hidden.length + ' column(s) hidden</span>'; }
    if (st.search) { h += '<button type="button" data-ir-clear-search class="inline-flex items-center gap-1 rounded-full border border-slate-200 px-2 py-0.5 text-[11px]">Search "' + esc(st.search) + '"<span aria-hidden="true">×</span></button>'; }
    this.bar.querySelector('[data-ir-chips]').innerHTML = h;  // no icons here: createIcons scans the whole page
  };

  // ---- Rows
  Report.prototype.setRows = function (value) {
    var many = value === 'all' ? this.total : parseInt(value, 10);
    if (this.pager && many > 2000 && this.total > 2000 &&
        !window.confirm('This list has ' + this.total.toLocaleString() + ' records. Showing ' + (value === 'all' ? 'them all' : many.toLocaleString()) +
                        ' on one page can take a while to load.\n\nTo download them, Export CSV › All records is faster.\n\nShow them anyway?')) {
      var sel = this.bar.querySelector('[data-ir-rows]');
      if (sel) { sel.value = this.pager.getAttribute('data-per-page'); }
      return;
    }
    if (this.pager || new URLSearchParams(location.search).get('rows')) {
      var url = new URL(location.href);
      url.searchParams.set('rows', value);
      url.searchParams.delete('page');
      location.href = url.toString();
      return;
    }
    this.state.limit = value;
    this.apply();
  };
  Report.prototype.carryRows = function () {
    var rows = new URLSearchParams(location.search).get('rows');
    if (!rows || !this.pager) { return; }
    this.pager.querySelectorAll('a[href]').forEach(function (a) {
      var u = new URL(a.getAttribute('href'), location.href);
      u.searchParams.set('rows', rows);
      a.setAttribute('href', u.pathname + u.search);
    });
  };

  // ---- checkboxes
  Report.prototype.addCheckboxes = function () {
    var self = this;
    quiet(function () { self.insertBoxes(); });
  };
  Report.prototype.insertBoxes = function () {
    var self = this, head = this.table.tHead && this.table.tHead.rows[0];
    if (!head || !this.trs.length) { return; }
    var th = document.createElement('th');
    th.setAttribute('data-ir-sel', '');
    th.className = 'w-8 border border-slate-300 px-2 py-2 text-center';
    th.innerHTML = '<input type="checkbox" data-ir-all aria-label="Select every row shown" class="h-3.5 w-3.5 rounded border-slate-400">';
    head.insertBefore(th, head.firstChild);
    this.trs.forEach(function (tr) {
      var td = document.createElement('td');
      td.setAttribute('data-ir-sel', '');
      td.className = 'w-8 px-2 text-center';
      td.innerHTML = '<input type="checkbox" data-ir-row aria-label="Select this row" class="h-3.5 w-3.5 rounded border-slate-400">';
      tr.insertBefore(td, tr.firstChild);
    });
    var empty = Array.prototype.filter.call(this.table.tBodies[0].rows, function (tr) { return tr.cells.length === 1 && tr.cells[0].colSpan > 1; });
    empty.forEach(function (tr) { tr.cells[0].colSpan += 1; });
    this.table.addEventListener('change', function (e) {
      if (e.target.hasAttribute('data-ir-all')) {
        var on = e.target.checked;
        self.trs.forEach(function (tr) { if (tr.style.display !== 'none') { tr.querySelector('[data-ir-row]').checked = on; } });
        self.allRecords = false;
      } else if (e.target.hasAttribute('data-ir-row')) {
        self.allRecords = false;
        if (!e.target.checked) { self.table.querySelector('[data-ir-all]').checked = false; }
      } else { return; }
      self.selectionNote();
      self.apply();
    });
  };
  Report.prototype.selected = function () {
    var self = this;
    return this.trs.map(function (tr, i) { return i; }).filter(function (i) {
      var box = self.trs[i].querySelector('[data-ir-row]');
      return box && box.checked;
    });
  };
  Report.prototype.selectedLabel = function () {
    if (!this.main) { return ''; }
    if (this.allRecords) { return ' · all ' + this.total + ' selected'; }
    var n = this.selected().length;
    return n ? ' · ' + n + ' selected' : '';
  };
  Report.prototype.selectionNote = function () {
    var self = this, n = this.selected().length, shown = this.trs.filter(function (tr) { return tr.style.display !== 'none'; }).length;
    if (this.allRecords) {
      this.note.innerHTML = 'All <b>' + this.total + '</b> records are selected, on every page. <button type="button" data-ir-none class="font-semibold underline">Clear selection</button>';
    } else if (this.pager && n && n === shown && this.total > n) {
      this.note.innerHTML = 'All ' + n + ' rows on this page are selected. <button type="button" data-ir-every class="font-semibold underline">Select all ' + this.total + ' records</button>';
    } else { this.note.classList.add('hidden'); return; }
    this.note.classList.remove('hidden');
    this.note.onclick = function (e) {
      if (e.target.hasAttribute('data-ir-every')) { self.allRecords = true; }
      if (e.target.hasAttribute('data-ir-none')) {
        self.allRecords = false;
        self.trs.forEach(function (tr) { tr.querySelector('[data-ir-row]').checked = false; });
        self.table.querySelector('[data-ir-all]').checked = false;
      }
      self.selectionNote();
      self.apply();
    };
  };

  // ---- getting rows out
  Report.prototype.viewRows = function (rows) {
    // filters + sort on plain arrays (the same rules as on screen), then the visible data columns
    var self = this, cols = this.visibleCols().filter(function (i) { return self.actionCols.indexOf(i) === -1; });
    var kept = this.sortRows(rows.filter(function (r) { return self.keep(r); }), function (r) { return r; });
    return {headers: cols.map(function (i) { return self.label(i); }), rows: kept.map(function (r) { return cols.map(function (i) { return r[i] || ''; }); })};
  };
  Report.prototype.plainRows = function (rows) {
    var self = this, cols = this.visibleCols().filter(function (i) { return self.actionCols.indexOf(i) === -1; });
    return {headers: cols.map(function (i) { return self.label(i); }), rows: rows.map(function (r) { return cols.map(function (i) { return r[i] || ''; }); })};
  };
  Report.prototype.fetchAll = function () {
    var self = this;
    if (!this.pager || this.total <= this.trs.length) { return Promise.resolve(this.data.slice()); }
    var pages = Math.ceil(this.total / CHUNK), out = [], p = 1;
    var step = function () {
      self.progress('Collecting records: page ' + p + ' of ' + pages + '…');
      var url = new URL(location.href);
      url.searchParams.set('rows', String(CHUNK));
      url.searchParams.set('page', String(p));
      return fetch(url.toString(), {credentials: 'same-origin'}).then(function (r) {
        if (!r.ok) { throw new Error('Page ' + p + ' answered ' + r.status); }
        return r.text();
      }).then(function (html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var t = doc.querySelectorAll('table[data-ir]')[self.index];
        if (t && t.tBodies[0]) {
          Array.prototype.forEach.call(t.tBodies[0].rows, function (tr) {
            if (!(tr.cells.length === 1 && tr.cells[0].colSpan > 1)) { out.push(Array.prototype.map.call(tr.cells, txt)); }
          });
        }
        p += 1;
        return p <= pages ? step() : out;
      });
    };
    return step().then(function (rows) { self.progress(null); return rows; }, function (err) { self.progress(null); throw err; });
  };
  Report.prototype.progress = function (msg) {
    if (!msg) { this.selectionNote(); return; }
    this.note.textContent = msg;
    this.note.classList.remove('hidden');
  };
  Report.prototype.fileName = function (ext) { return slug(title()) + '-' + today() + '.' + ext; };
  Report.prototype.save = function (out, fmt) {
    var self = this;
    if (!out.rows.length) { toast('No rows to export.', true); return; }
    if (fmt === 'csv') { download(this.fileName('csv'), csv(out.headers, out.rows)); toast(out.rows.length + ' row(s) exported.'); return; }
    api('POST', '/ir/export', {name: title(), headers: out.headers, rows: out.rows, format: 'xlsx'}).then(function (blob) {
      download(self.fileName('xlsx'), blob);
      toast(out.rows.length + ' row(s) exported to Excel.');
    }, function (err) { toast(err.message, true); });
  };
  Report.prototype.exportView = function (fmt) {
    if (this.state.view === 'group' && this.groupOut) { this.save(this.groupOut, fmt); return; }
    if (this.state.view === 'pivot' && this.pivotOut) { this.save(this.pivotOut, fmt); return; }
    this.save(this.viewRows(this.data), fmt);
  };
  Report.prototype.exportScope = function (scope) {
    var self = this;
    if (scope === 'selected') {
      if (this.allRecords) {
        this.fetchAll().then(function (rows) { self.save(self.plainRows(rows), 'csv'); }, function (e) { toast(e.message, true); });
        return;
      }
      var idx = this.selected();
      if (!idx.length) { toast('Tick the rows to export first (the box at the start of each row).', true); return; }
      this.save(this.plainRows(idx.map(function (i) { return self.data[i]; })), 'csv');
      return;
    }
    this.fetchAll().then(function (rows) {
      self.save(scope === 'all' ? self.plainRows(rows) : self.viewRows(rows), 'csv');
    }, function (e) { toast(e.message, true); });
  };
  Report.prototype.print = function () {
    var out = this.state.view === 'group' && this.groupOut ? this.groupOut : this.state.view === 'pivot' && this.pivotOut ? this.pivotOut : this.viewRows(this.data);
    var w = window.open('', '_blank');
    if (!w) { toast('Allow pop-ups to print.', true); return; }
    w.document.write('<!doctype html><html><head><meta charset="utf-8"><title>' + esc(title()) + '</title><style>body{font:12px system-ui,sans-serif;margin:16px}' +
      'table{border-collapse:collapse;width:100%}th,td{border:1px solid #cbd5e1;padding:4px 6px;text-align:left}th{background:#e8f1fb}</style></head><body>' +
      '<h2>' + esc(title()) + '</h2><p>' + out.rows.length + ' row(s) · ' + new Date().toLocaleString() + '</p><table><thead><tr>' +
      out.headers.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      out.rows.map(function (r) { return '<tr>' + r.map(function (c) { return '<td>' + esc(c) + '</td>'; }).join('') + '</tr>'; }).join('') +
      '</tbody></table></body></html>');
    w.document.close();
    w.focus();
    setTimeout(function () { w.print(); }, 300);
  };

  // ---- dialogs
  Report.prototype.dlgColumns = function () {
    var self = this;
    var body = '<p class="text-xs text-slate-500">Untick a column to hide it. Hidden columns are left out of downloads too.</p><div class="grid grid-cols-2 gap-1">' +
      this.headers.map(function (h, i) {
        return '<label class="flex items-center gap-2 rounded px-2 py-1 hover:bg-slate-50"><input type="checkbox" name="c" value="' + i + '"' + (self.state.hidden.indexOf(i) === -1 ? ' checked' : '') +
               ' class="h-3.5 w-3.5 rounded border-slate-400">' + esc(self.label(i)) + (self.actionCols.indexOf(i) !== -1 ? ' <span class="text-[11px] text-slate-400">(buttons)</span>' : '') + '</label>';
      }).join('') + '</div><div class="flex gap-3 text-xs"><button type="button" data-all class="text-brand-700 underline">Show all</button><button type="button" data-none class="text-brand-700 underline">Hide all</button></div>';
    var d = dialog('Columns', body, function (f) {
      var shown = Array.prototype.map.call(f.querySelectorAll('[name=c]:checked'), function (c) { return parseInt(c.value, 10); });
      if (!shown.length) { toast('Keep at least one column.', true); return false; }
      self.state.hidden = self.headers.map(function (h, i) { return i; }).filter(function (i) { return shown.indexOf(i) === -1; });
      self.apply();
    });
    d.querySelector('[data-all]').onclick = function () { d.querySelectorAll('[name=c]').forEach(function (c) { c.checked = true; }); };
    d.querySelector('[data-none]').onclick = function () { d.querySelectorAll('[name=c]').forEach(function (c) { c.checked = false; }); };
  };
  Report.prototype.dlgFilter = function () {
    var self = this;
    var note = this.pager && this.total > this.trs.length ? '<p class="rounded bg-amber-50 p-2 text-xs text-amber-800">Filters work on the ' + this.trs.length + ' rows on this page. Choose <b>Rows: All</b> to filter all ' + this.total + ' records, or use Export CSV › Filtered records.</p>' : '';
    dialog('Filter', note + '<label class="block">Column<select name="col" class="' + FIELD + '">' + this.colOptions() + '</select></label>' +
      '<label class="block">Operator<select name="op" class="' + FIELD + '">' + options(OPS, 'contains') + '</select></label>' +
      '<label class="block">Value<input name="value" class="' + FIELD + '" autocomplete="off"></label>', function (f) {
      self.state.filters.push({col: parseInt(f.col.value, 10), op: f.op.value, value: f.value.value});
      self.apply();
    });
  };
  Report.prototype.dlgSort = function () {
    var self = this, s = this.state.sort, body = '';
    for (var i = 0; i < 3; i++) {
      body += '<div class="grid grid-cols-3 gap-2"><select name="col' + i + '" class="' + FIELD + ' col-span-2"><option value="">' + (i ? '(then by)' : '(choose a column)') + '</option>' +
              this.colOptions(s[i] ? s[i].col : '') + '</select><select name="dir' + i + '" class="' + FIELD + '">' + options([['asc', 'Ascending'], ['desc', 'Descending']], s[i] ? s[i].dir : 'asc') + '</select></div>';
    }
    dialog('Sort', body, function (f) {
      var out = [];
      for (var j = 0; j < 3; j++) { if (f['col' + j].value !== '') { out.push({col: parseInt(f['col' + j].value, 10), dir: f['dir' + j].value}); } }
      self.state.sort = out;
      self.apply();
    });
  };
  Report.prototype.dlgAggregate = function () {
    var self = this;
    dialog('Aggregate', '<p class="text-xs text-slate-500">Adds a total line under the table, over the rows shown.</p><label class="block">Function<select name="fn" class="' + FIELD + '">' + options(FUNCS, 'sum') + '</select></label>' +
      '<label class="block">Column<select name="col" class="' + FIELD + '">' + this.colOptions() + '</select></label>', function (f) {
      self.state.aggregates.push({fn: f.fn.value, col: parseInt(f.col.value, 10)});
      self.apply();
    });
  };
  Report.prototype.dlgHighlight = function () {
    var self = this;
    dialog('Highlight', '<label class="block">When column<select name="col" class="' + FIELD + '">' + this.colOptions() + '</select></label>' +
      '<div class="grid grid-cols-2 gap-2"><label class="block">Operator<select name="op" class="' + FIELD + '">' + options(OPS, 'eq') + '</select></label>' +
      '<label class="block">Value<input name="value" class="' + FIELD + '"></label></div>' +
      '<div class="grid grid-cols-2 gap-2"><label class="block">Colour<select name="color" class="' + FIELD + '">' + options(COLORS, COLORS[0][0]) + '</select></label>' +
      '<label class="block">Colour the<select name="scope" class="' + FIELD + '">' + options([['row', 'whole row'], ['cell', 'cell only']], 'row') + '</select></label></div>', function (f) {
      self.state.highlights.push({col: parseInt(f.col.value, 10), op: f.op.value, value: f.value.value, color: f.color.value, scope: f.scope.value});
      self.apply();
    }, 'Add highlight');
  };
  Report.prototype.dlgRows = function () {
    var self = this, now = (this.bar.querySelector('[data-ir-rows]') || {}).value || 'all';
    dialog('Rows per page', '<select name="rows" class="' + FIELD + '">' + options(ROW_CHOICES.map(function (r) { return [r, r === 'all' ? 'All' : r]; }), now) + '</select>', function (f) { self.setRows(f.rows.value); });
  };

  // ---- Group By, Pivot and Chart (computed on the rows shown, with the filters applied)
  Report.prototype.shownData = function () {
    var self = this;
    return this.data.filter(function (r, i) { return self.trs[i].style.display !== 'none' || (!self.pager && self.state.limit && self.keep(r)); });
  };
  Report.prototype.aggPicker = function (n, cur) {
    var h = '';
    for (var i = 0; i < n; i++) {
      var a = (cur && cur[i]) || {};
      h += '<div class="grid grid-cols-2 gap-2"><select name="fn' + i + '" class="' + FIELD + '"><option value="">' + (i ? '(another value)' : '(value)') + '</option>' + options(FUNCS, a.fn) + '</select>' +
           '<select name="vc' + i + '" class="' + FIELD + '">' + this.colOptions(a.col) + '</select></div>';
    }
    return h;
  };
  Report.prototype.readAggs = function (f, n) {
    var out = [];
    for (var i = 0; i < n; i++) { if (f['fn' + i].value) { out.push({fn: f['fn' + i].value, col: parseInt(f['vc' + i].value, 10)}); } }
    return out;
  };
  Report.prototype.dlgGroup = function () {
    var self = this, g = this.state.group || {};
    dialog('Group By', '<p class="text-xs text-slate-500">One line per value of the chosen column(s), with a row count and the values you add.</p>' +
      '<label class="block">Group by<select name="g1" class="' + FIELD + '">' + this.colOptions(g.cols ? g.cols[0] : '') + '</select></label>' +
      '<label class="block">Then by (optional)<select name="g2" class="' + FIELD + '"><option value="">—</option>' + this.colOptions(g.cols && g.cols[1] !== undefined ? g.cols[1] : '') + '</select></label>' +
      this.aggPicker(3, g.aggs), function (f) {
      var cols = [parseInt(f.g1.value, 10)];
      if (f.g2.value !== '') { cols.push(parseInt(f.g2.value, 10)); }
      self.state.group = {cols: cols, aggs: self.readAggs(f, 3)};
      self.state.view = 'group';
      self.apply();
    }, 'Group');
  };
  Report.prototype.renderGroup = function () {
    var self = this, g = this.state.group, groups = {}, order = [];
    this.shownData().forEach(function (r) {
      var key = g.cols.map(function (c) { return r[c] || '(blank)'; }).join('\u0001');
      if (!groups[key]) { groups[key] = []; order.push(key); }
      groups[key].push(r);
    });
    order.sort(function (a, b) { return cmp(a, b); });
    var headers = g.cols.map(function (c) { return self.label(c); }).concat(['Rows']).concat(g.aggs.map(function (a) { return a.fn + '(' + self.label(a.col) + ')'; }));
    var rows = order.map(function (k) {
      var list = groups[k];
      return k.split('\u0001').concat([list.length]).concat(g.aggs.map(function (a) { return aggregate(list.map(function (r) { return r[a.col] || ''; }), a.fn); }));
    });
    this.groupOut = {headers: headers, rows: rows};
    this.showPanel('Group By: ' + g.cols.map(function (c) { return self.label(c); }).join(', '), headers, rows);
  };
  Report.prototype.dlgPivot = function () {
    var self = this, p = this.state.pivot || {};
    dialog('Pivot', '<p class="text-xs text-slate-500">Rows from one column, columns from another, and a value in each cell.</p>' +
      '<label class="block">Rows<select name="r" class="' + FIELD + '">' + this.colOptions(p.row) + '</select></label>' +
      '<label class="block">Columns<select name="c" class="' + FIELD + '">' + this.colOptions(p.col) + '</select></label>' +
      '<div class="grid grid-cols-2 gap-2"><label class="block">Value<select name="fn" class="' + FIELD + '">' + options(FUNCS, p.fn || 'count') + '</select></label>' +
      '<label class="block">Of column<select name="v" class="' + FIELD + '">' + this.colOptions(p.val) + '</select></label></div>', function (f) {
      self.state.pivot = {row: parseInt(f.r.value, 10), col: parseInt(f.c.value, 10), fn: f.fn.value, val: parseInt(f.v.value, 10)};
      self.state.view = 'pivot';
      self.apply();
    }, 'Pivot');
  };
  Report.prototype.renderPivot = function () {
    var self = this, p = this.state.pivot, cells = {}, rowKeys = [], colKeys = [], data = this.shownData();
    data.forEach(function (r) {
      var rk = r[p.row] || '(blank)', ck = r[p.col] || '(blank)';
      if (rowKeys.indexOf(rk) === -1) { rowKeys.push(rk); }
      if (colKeys.indexOf(ck) === -1) { colKeys.push(ck); }
      (cells[rk + '\u0001' + ck] = cells[rk + '\u0001' + ck] || []).push(r[p.val] || '');
    });
    rowKeys.sort(cmp);
    colKeys.sort(cmp);
    var headers = [this.label(p.row) + ' \\ ' + this.label(p.col)].concat(colKeys).concat(['Total']);
    var rows = rowKeys.map(function (rk) {
      var all = [];
      return [rk].concat(colKeys.map(function (ck) { var v = cells[rk + '\u0001' + ck] || []; all = all.concat(v); return v.length ? aggregate(v, p.fn) : ''; }))
                 .concat([aggregate(all, p.fn)]);
    });
    var total = ['Total'].concat(colKeys.map(function (ck) {
      return aggregate(data.filter(function (r) { return (r[p.col] || '(blank)') === ck; }).map(function (r) { return r[p.val] || ''; }), p.fn);
    })).concat([aggregate(data.map(function (r) { return r[p.val] || ''; }), p.fn)]);
    rows.push(total);
    this.pivotOut = {headers: headers, rows: rows};
    this.showPanel('Pivot: ' + p.fn + ' of ' + this.label(p.val), headers, rows);
  };
  Report.prototype.showPanel = function (heading, headers, rows) {
    var self = this;
    this.wrap.style.display = 'none';
    if (this.pager) { this.pager.style.display = 'none'; }
    this.panel.className = 'mb-3';
    this.panel.innerHTML = '<div class="mb-2 flex flex-wrap items-center gap-2"><span class="text-sm font-semibold text-slate-700">' + esc(heading) + '</span>' +
      '<span class="text-xs text-slate-500">' + rows.length + ' line(s), from the rows shown</span>' +
      '<button type="button" data-back class="ml-auto inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1 text-xs hover:bg-slate-50"><i data-lucide="arrow-left" class="h-3.5 w-3.5"></i>Back to the report</button>' +
      '<button type="button" data-csv class="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2.5 py-1 text-xs hover:bg-slate-50"><i data-lucide="file-down" class="h-3.5 w-3.5"></i>CSV</button></div>' +
      '<div class="overflow-x-auto rounded-md border border-slate-300 bg-white"><table class="min-w-full border-collapse text-[13px]"><thead><tr class="bg-[#e8f1fb]">' +
      headers.map(function (h) { return '<th class="border border-slate-300 px-3 py-2 text-left text-xs font-semibold text-slate-700">' + esc(h) + '</th>'; }).join('') +
      '</tr></thead><tbody>' + rows.map(function (r) { return '<tr class="hover:bg-[#f1f6fc]">' + r.map(function (c) { return '<td class="border border-slate-200 px-3 py-1.5">' + esc(fmt(c)) + '</td>'; }).join('') + '</tr>'; }).join('') +
      '</tbody></table></div>';
    this.panel.querySelector('[data-back]').onclick = function () { self.state.view = 'report'; self.apply(); };
    this.panel.querySelector('[data-csv]').onclick = function () { self.exportView('csv'); };
    icons();
  };
  Report.prototype.showReport = function () {
    this.wrap.style.display = '';
    if (this.pager) { this.pager.style.display = ''; }
    this.panel.className = 'hidden';
    this.panel.innerHTML = '';
  };
  Report.prototype.dlgChart = function () {
    var self = this;
    dialog('Chart', '<div class="grid grid-cols-2 gap-2"><label class="block">Type<select name="type" class="' + FIELD + '">' +
      options([['bar', 'Bar'], ['hbar', 'Horizontal bar'], ['line', 'Line'], ['pie', 'Pie'], ['doughnut', 'Doughnut']], 'bar') + '</select></label>' +
      '<label class="block">Label<select name="label" class="' + FIELD + '">' + this.colOptions() + '</select></label></div>' +
      '<div class="grid grid-cols-2 gap-2"><label class="block">Value<select name="fn" class="' + FIELD + '">' + options(FUNCS, 'count') + '</select></label>' +
      '<label class="block">Of column<select name="val" class="' + FIELD + '">' + this.colOptions() + '</select></label></div>' +
      '<div class="grid grid-cols-2 gap-2"><label class="block">Order<select name="order" class="' + FIELD + '">' + options([['value', 'Biggest first'], ['label', 'By label']], 'value') + '</select></label>' +
      '<label class="block">Show at most<select name="top" class="' + FIELD + '">' + options([['10', '10'], ['20', '20'], ['50', '50'], ['0', 'All']], '20') + '</select></label></div>', function (f) {
      self.chart({type: f.type.value, label: parseInt(f.label.value, 10), fn: f.fn.value, val: parseInt(f.val.value, 10), order: f.order.value, top: parseInt(f.top.value, 10)});
    }, 'Show chart');
  };
  Report.prototype.chart = function (c) {
    if (!window.Chart) { toast('The chart library did not load.', true); return; }
    var self = this, groups = {}, order = [];
    this.shownData().forEach(function (r) {
      var k = r[c.label] || '(blank)';
      if (!groups[k]) { groups[k] = []; order.push(k); }
      groups[k].push(r[c.val] || '');
    });
    var points = order.map(function (k) { return [k, Number(aggregate(groups[k], c.fn)) || 0]; });
    points.sort(c.order === 'label' ? function (a, b) { return cmp(a[0], b[0]); } : function (a, b) { return b[1] - a[1]; });
    if (c.top) { points = points.slice(0, c.top); }
    var heading = (FUNCS.filter(function (f) { return f[0] === c.fn; })[0][1]) + ' of ' + this.label(c.val) + ' by ' + this.label(c.label);
    var d = dialog(heading, '<div class="h-[420px]"><canvas></canvas></div><div class="flex justify-end"><button type="button" data-png class="' + BTN + ' border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"><i data-lucide="image-down" class="h-4 w-4"></i>Download image</button></div>', null, null, true);
    var palette = ['#1d6fcf', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#14b8a6', '#f97316', '#64748b', '#ec4899', '#84cc16'];
    var circular = c.type === 'pie' || c.type === 'doughnut';
    var chart = new window.Chart(d.querySelector('canvas'), {
      type: c.type === 'hbar' ? 'bar' : c.type,
      data: {labels: points.map(function (p) { return p[0]; }),
             datasets: [{label: heading, data: points.map(function (p) { return p[1]; }),
                         backgroundColor: circular ? points.map(function (p, i) { return palette[i % palette.length]; }) : '#1d6fcf', borderColor: '#1d6fcf'}]},
      options: {indexAxis: c.type === 'hbar' ? 'y' : 'x', responsive: true, maintainAspectRatio: false, plugins: {legend: {display: circular}}}
    });
    d.querySelector('[data-png]').onclick = function () {
      var a = document.createElement('a');
      a.href = chart.toBase64Image();
      a.download = self.fileName('png');
      a.click();
    };
  };

  // ---- saved reports and subscriptions (server)
  Report.prototype.config = function () {
    var c = {table: this.index, actions: this.actionCols};
    for (var k in this.state) { if (k !== 'view') { c[k] = this.state[k]; } }
    return c;
  };
  Report.prototype.useConfig = function (cfg) {
    this.state = this.blank();
    for (var k in cfg) { if (k in this.state && k !== 'view') { this.state[k] = cfg[k]; } }
    var s = this.bar.querySelector('[data-ir-search]');
    if (s) { s.value = this.state.search || ''; }
    this.apply();
  };
  Report.prototype.loadDefault = function () {
    var self = this;
    api('GET', '/ir/views?path=' + encodeURIComponent(PATH)).then(function (j) {
      var d = (j.views || []).filter(function (v) { return v.default && v.mine && (v.config.table || 0) === self.index; })[0];
      if (d) { self.useConfig(d.config); toast('Your default report "' + d.name + '" is applied.'); }
    }, function () {});
  };
  Report.prototype.dlgSave = function () {
    var self = this;
    dialog('Save report', '<p class="text-xs text-slate-500">Saves the columns, filters, sort, highlights and totals of this table under a name.</p>' +
      '<label class="block">Name<input name="name" required maxlength="120" class="' + FIELD + '"></label>' +
      '<label class="flex items-center gap-2"><input type="checkbox" name="default" class="h-3.5 w-3.5 rounded border-slate-400">Open this page with it</label>' +
      '<label class="flex items-center gap-2"><input type="checkbox" name="shared" class="h-3.5 w-3.5 rounded border-slate-400">Share with everyone who can open this page</label>', function (f) {
      api('POST', '/ir/views', {path: PATH, name: f.name.value, config: self.config(), shared: f.shared.checked, default: f['default'].checked})
        .then(function (v) { toast('Report "' + v.name + '" saved.'); }, function (e) { toast(e.message, true); });
    }, 'Save');
  };
  Report.prototype.dlgSaved = function () {
    var self = this;
    var d = dialog('Saved reports', '<div data-list class="text-sm text-slate-500">Loading…</div>');
    api('GET', '/ir/views?path=' + encodeURIComponent(PATH)).then(function (j) {
      var views = (j.views || []).filter(function (v) { return (v.config.table || 0) === self.index; });
      var list = d.querySelector('[data-list]');
      if (!views.length) { list.textContent = 'No saved reports for this table yet. Use Report › Save report.'; return; }
      list.innerHTML = '<ul class="divide-y divide-slate-100">' + views.map(function (v) {
        return '<li class="flex items-center gap-2 py-2"><span class="flex-1 text-slate-800">' + esc(v.name) +
               '<span class="block text-[11px] text-slate-400">' + (v.mine ? 'yours' : 'shared by ' + esc(v.owner)) + (v['default'] ? ' · opens by default' : '') + (v.shared && v.mine ? ' · shared' : '') + '</span></span>' +
               '<button type="button" data-apply="' + v.id + '" class="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50">Apply</button>' +
               (v.mine ? '<button type="button" data-del="' + v.id + '" class="rounded px-2 py-1 text-xs text-rose-700 hover:bg-rose-50">Delete</button>' : '') + '</li>';
      }).join('') + '</ul>';
      list.onclick = function (e) {
        var a = e.target.getAttribute('data-apply'), del = e.target.getAttribute('data-del');
        if (a) { self.useConfig(views.filter(function (v) { return String(v.id) === a; })[0].config); d.close(); }
        if (del && window.confirm('Delete this saved report?')) {
          api('POST', '/ir/views/' + del + '/delete').then(function () { e.target.closest('li').remove(); toast('Deleted.'); }, function (er) { toast(er.message, true); });
        }
      };
    }, function (e) { d.querySelector('[data-list]').textContent = e.message; });
  };
  Report.prototype.dlgSubscription = function () {
    var self = this;
    var d = dialog('Subscription', '<p class="text-xs text-slate-500">E-mails this report as a CSV file, built with what you can see and with the columns, filters and sort you have now.</p>' +
      '<label class="block">Name<input name="name" required maxlength="120" value="' + esc(title()) + '" class="' + FIELD + '"></label>' +
      '<label class="block">Send to<input name="emails" placeholder="you@example.com, colleague@example.com" class="' + FIELD + '"><span class="text-[11px] text-slate-400">Leave empty to send it to yourself.</span></label>' +
      '<label class="block">How often<select name="frequency" class="' + FIELD + '">' + options([['daily', 'Every day'], ['weekly', 'Every week'], ['monthly', 'Every month']], 'weekly') + '</select></label>' +
      '<div class="border-t border-slate-100 pt-3"><div class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Your subscriptions on this page</div><div data-subs class="text-xs text-slate-500">Loading…</div></div>', function (f) {
      api('POST', '/ir/subscriptions', {path: PATH, query: location.search, name: f.name.value, emails: f.emails.value, frequency: f.frequency.value, config: self.config()})
        .then(function (s) { toast('Subscribed: "' + s.name + '", first e-mail ' + (s.next_run_at || '').replace('T', ' ') + ' UTC.'); }, function (e) { toast(e.message, true); });
    }, 'Subscribe');
    var box = d.querySelector('[data-subs]');
    var load = function () {
      api('GET', '/ir/subscriptions?path=' + encodeURIComponent(PATH)).then(function (j) {
        var subs = j.subscriptions || [];
        if (!subs.length) { box.textContent = 'None yet.'; return; }
        box.innerHTML = '<ul class="divide-y divide-slate-100">' + subs.map(function (s) {
          return '<li class="flex items-center gap-2 py-1.5"><span class="flex-1 text-slate-700">' + esc(s.name) + ' · ' + esc(s.frequency) + ' to ' + esc(s.emails) +
                 '<span class="block text-[11px] text-slate-400">next ' + esc((s.next_run_at || '').replace('T', ' ')) + ' UTC' + (s.last_status ? ' · last: ' + esc(s.last_status) : '') + '</span></span>' +
                 '<button type="button" data-send="' + s.id + '" class="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50">Send now</button>' +
                 '<button type="button" data-del="' + s.id + '" class="rounded px-2 py-1 text-rose-700 hover:bg-rose-50">Remove</button></li>';
        }).join('') + '</ul>';
      }, function (e) { box.textContent = e.message; });
    };
    box.onclick = function (e) {
      var send = e.target.getAttribute('data-send'), del = e.target.getAttribute('data-del');
      if (send) { e.target.textContent = 'Sending…'; api('POST', '/ir/subscriptions/' + send + '/send').then(function (s) { toast(s.last_status || 'Sent.', /^Failed/.test(s.last_status || '')); load(); }, function (er) { toast(er.message, true); }); }
      if (del && window.confirm('Remove this subscription?')) { api('POST', '/ir/subscriptions/' + del + '/delete').then(load, function (er) { toast(er.message, true); }); }
    };
    load();
  };
  Report.prototype.dlgHelp = function () {
    dialog('Help: interactive report', '<ul class="list-disc space-y-1.5 pl-5 text-slate-700">' +
      '<li><b>Search</b> narrows the rows on the screen. The filter bar above the table, when there is one, asks the server.</li>' +
      '<li><b>Rows</b> sets how many records each page shows: 10 to 1,000, or All (up to 10,000).</li>' +
      '<li><b>Columns</b> hides columns. <b>Filter</b> keeps rows that match a rule; each rule shows as a chip you can remove.</li>' +
      '<li><b>Data › Sort</b> orders by up to three columns. <b>Data › Aggregate</b> adds totals under the table.</li>' +
      '<li><b>Format › Highlight</b> colours rows or cells that match a rule.</li>' +
      '<li><b>Chart</b>, <b>Group By</b> and <b>Pivot</b> summarise the rows shown; each can be downloaded.</li>' +
      '<li><b>Report</b> saves the current settings under a name, optionally as your default or shared with colleagues.</li>' +
      '<li><b>Download</b> gives the current view as CSV, Excel or a printable page. <b>Export CSV</b> gives all records, the filtered records, or the rows you ticked, across every page.</li>' +
      '<li><b>Subscription</b> e-mails the report as a CSV every day, week or month.</li>' +
      '<li><b>Import CSV</b> (where shown) opens the bulk import for this list: upload, map columns, validate, preview, import, and download an error file.</li>' +
      '<li>Tick the boxes at the start of the rows to select them; the box in the heading selects every row shown.</li></ul>' +
      '<p class="text-xs text-slate-500">You only ever see and export what your role allows. Settings are remembered on this computer until you press Report › Reset.</p>');
  };

  // ------------------------------------------------------------------------------------------------ start
  function start() {
    if (!document.body.hasAttribute('data-ir-staff')) { return; }
    var tables = Array.prototype.slice.call(document.querySelectorAll('main table[data-ir], table[data-ir]'));
    tables = tables.filter(function (t, i) { return tables.indexOf(t) === i; });
    if (!tables.length) { return; }
    var mainIdx = -1;
    tables.forEach(function (t, i) {
      if (mainIdx === -1 && hasPager(t)) { mainIdx = i; }
    });
    if (mainIdx === -1) { mainIdx = 0; }
    window.OQCReports = tables.map(function (t, i) {
      var dataRows = t.tBodies[0] ? Array.prototype.filter.call(t.tBodies[0].rows, function (tr) { return !(tr.cells.length === 1 && tr.cells[0].colSpan > 1); }).length : 0;
      if (i !== mainIdx && dataRows < 2) { return null; }
      try { return new Report(t, i, i === mainIdx); } catch (e) { if (window.console) { console.error('report toolbar', e); } return null; }
    });
  }
  function hasPager(t) {
    var el = t.parentElement.nextElementSibling;
    for (var i = 0; el && i < 3; i++, el = el.nextElementSibling) {
      if (el.matches('[data-ir-pager]') || (el.querySelector && el.querySelector('[data-ir-pager]'))) { return true; }
      if (el.querySelector && el.querySelector('table[data-ir]')) { return false; }
    }
    return false;
  }
  // after an "Import CSV" from a list, the import pages offer the way back
  function importReturn() {
    if (PATH.indexOf('/admin/migration') !== 0) { return; }
    var back = new URLSearchParams(location.search).get('back');
    if (back) { try { sessionStorage.setItem('oqc.ir.back', back); } catch (e) { /* private window */ } }
    else { try { back = sessionStorage.getItem('oqc.ir.back'); } catch (e) { back = null; } }
    if (!back || back.charAt(0) !== '/' || back.charAt(1) === '/' || document.querySelector('a[href="' + back + '"]')) { return; }
    var a = document.createElement('a');
    a.href = back;
    a.className = 'fixed bottom-4 left-4 z-40 inline-flex items-center gap-1.5 rounded-md bg-slate-800 px-3 py-2 text-xs font-medium text-white shadow-lg hover:bg-slate-700';
    a.innerHTML = '<i data-lucide="arrow-left" class="h-3.5 w-3.5"></i>Back to the list';
    document.body.appendChild(a);
    icons();
  }
  function boot() { setTimeout(function () { start(); importReturn(); }, 0); }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', boot); } else { boot(); }
})();
