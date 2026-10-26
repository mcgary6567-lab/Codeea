/* Area sidebar (templates/_sidebar.html): search, accordion groups, favourites, recent pages and the icon rail.
 *
 * Remembered in this browser only (localStorage): the rail on/off, which groups are open per area, and the last
 * pages opened. Favourites are the user's own (/favourites), shared with the star in the header.
 */
(function () {
  'use strict';
  var sb = document.getElementById('area-sidebar');
  if (!sb) { return; }
  var area = sb.getAttribute('data-area') || '';
  var activeUrl = sb.getAttribute('data-active') || '';
  var input = sb.querySelector('#sb-q');
  var list = sb.querySelector('[data-sb-list]');
  var groups = Array.prototype.slice.call(sb.querySelectorAll('[data-sb-grp]'));
  var OPEN_KEY = 'oqc-sb-open:' + area, RECENT_KEY = 'oqc-sb-recent', RAIL_KEY = 'oqc-sb-rail';

  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window: nothing remembered */ } }
  function icons() { if (window.lucide) { window.lucide.createIcons(); } }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  // ------------------------------------------------------------------ groups (accordion)
  function setOpen(g, open) {
    g.classList.toggle('is-open', open);
    var b = g.querySelector('.sb-grp-btn');
    if (b) { b.setAttribute('aria-expanded', open ? 'true' : 'false'); }
  }
  function saveOpen() {
    set(OPEN_KEY, JSON.stringify(groups.filter(function (g) { return g.classList.contains('is-open'); })
      .map(function (g) { return g.getAttribute('data-sb-grp'); })));
  }
  (function restoreOpen() {
    var saved = null;
    try { saved = JSON.parse(get(OPEN_KEY) || 'null'); } catch (e) { saved = null; }
    if (!Array.isArray(saved)) { return; }
    groups.forEach(function (g) {
      if (g.classList.contains('sb-grp-flat')) { return; }
      setOpen(g, saved.indexOf(g.getAttribute('data-sb-grp')) >= 0 || g.classList.contains('is-current'));
    });
  })();
  var allBtn = sb.querySelector('[data-sb-all]');
  function syncAllBtn() {
    if (!allBtn) { return; }
    var any = groups.some(function (g) { return g.classList.contains('is-open') && !g.classList.contains('sb-grp-flat'); });
    allBtn.textContent = any ? 'Collapse all' : 'Expand all';
  }
  syncAllBtn();

  // ------------------------------------------------------------------ icon rail
  var root = document.documentElement;
  var railBtn = sb.querySelector('[data-sb-rail]');
  function setRail(on) {
    root.classList.toggle('sb-rail', on);
    set(RAIL_KEY, on ? '1' : '0');
    if (railBtn) {
      railBtn.title = on ? 'Expand the menu' : 'Collapse to icons';
      railBtn.setAttribute('aria-label', on ? 'Expand the menu' : 'Collapse the menu to icons');
    }
    tip.hidden = true;
  }
  var tip = document.createElement('div');
  tip.className = 'oqc-sb-tip'; tip.hidden = true; document.body.appendChild(tip);
  sb.addEventListener('mouseover', function (e) {
    if (!root.classList.contains('sb-rail')) { tip.hidden = true; return; }
    var t = e.target.closest('.sb-grp-btn, .sb-area, .sb-grp-flat .sb-item, [data-sb-rail]');
    if (!t) { tip.hidden = true; return; }
    var label = t.getAttribute('title') || (t.querySelector('.sb-t') || {}).textContent || '';
    var r = t.getBoundingClientRect();
    tip.textContent = label; tip.style.left = (r.right + 8) + 'px'; tip.style.top = (r.top + r.height / 2 - 11) + 'px'; tip.hidden = !label;
  });
  sb.addEventListener('mouseleave', function () { tip.hidden = true; });
  if (railBtn) { railBtn.title = root.classList.contains('sb-rail') ? 'Expand the menu' : 'Collapse to icons'; }

  // ------------------------------------------------------------------ search
  var labels = Array.prototype.slice.call(sb.querySelectorAll('[data-sb-label]'));
  var empty = sb.querySelector('[data-sb-empty]');
  function highlight(el, text, q) {
    var span = el.querySelector('.sb-t');
    var i = q ? text.toLowerCase().indexOf(q) : -1;
    span.innerHTML = i < 0 ? esc(text) : esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + q.length)) + '</mark>' + esc(text.slice(i + q.length));
  }
  function search() {
    var q = input.value.trim().toLowerCase();
    sb.classList.toggle('is-searching', !!q);
    var shown = 0;
    groups.forEach(function (g) {
      var groupHit = q && g.getAttribute('data-sb-grp').toLowerCase().indexOf(q) >= 0;
      var any = false;
      g.querySelectorAll('[data-sb-label]').forEach(function (a) {
        var text = a.getAttribute('data-sb-label');
        var hit = !q || groupHit || text.toLowerCase().indexOf(q) >= 0;
        a.parentNode.hidden = !hit;
        a.classList.remove('is-kbd');
        highlight(a, text, q);
        if (hit) { any = true; shown += 1; }
      });
      g.hidden = !!q && !any;
    });
    empty.hidden = !q || shown > 0;
    sb.querySelector('[data-sb-q]').textContent = input.value.trim();
  }
  function visibleItems() {
    return labels.filter(function (a) { return !a.parentNode.hidden && !a.closest('[data-sb-grp]').hidden; });
  }
  input.addEventListener('input', search);
  input.addEventListener('keydown', function (e) {
    var items = visibleItems(), cur = items.findIndex(function (a) { return a.classList.contains('is-kbd'); });
    if (e.key === 'Escape') { input.value = ''; search(); input.blur(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!items.length) { return; }
      if (cur >= 0) { items[cur].classList.remove('is-kbd'); }
      cur = e.key === 'ArrowDown' ? (cur + 1) % items.length : (cur <= 0 ? items.length - 1 : cur - 1);
      items[cur].classList.add('is-kbd'); items[cur].scrollIntoView({ block: 'nearest' });
    }
    if (e.key === 'Enter' && input.value.trim()) {
      var go = cur >= 0 ? items[cur] : items[0];
      if (go) { e.preventDefault(); window.location.href = go.getAttribute('href'); }
    }
  });
  document.addEventListener('keydown', function (e) {
    var typing = /INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || '') || (document.activeElement || {}).isContentEditable;
    var slash = e.key === '/' && !typing;
    var ctrlK = (e.ctrlKey || e.metaKey) && (e.key || '').toLowerCase() === 'k';
    if ((slash || ctrlK) && sb.offsetParent !== null) {
      e.preventDefault();
      if (root.classList.contains('sb-rail')) { setRail(false); }
      input.focus(); input.select();
    }
  });

  // ------------------------------------------------------------------ favourites
  var favBox = sb.querySelector('[data-sb-favs]'), favEmpty = sb.querySelector('[data-sb-favs-empty]');
  function favPaths() {
    return Array.prototype.map.call(favBox.querySelectorAll('[data-sb-star]'), function (b) { return b.getAttribute('data-path'); });
  }
  function paintStars() {
    var favs = favPaths();
    sb.querySelectorAll('.sb-grp [data-sb-star]').forEach(function (b) {
      var on = favs.indexOf(b.getAttribute('data-path')) >= 0;
      b.classList.toggle('is-on', on);
      b.title = on ? 'Remove from favourites' : 'Add to favourites';
    });
    favEmpty.hidden = favs.length > 0;
  }
  function favRow(path, label) {
    var row = document.createElement('div');
    row.className = 'sb-row';
    row.innerHTML = '<a href="' + esc(path) + '" class="sb-item sb-flat"><i data-lucide="star" class="sb-fav-ico"></i><span class="sb-t">' + esc(label) + '</span></a>' +
      '<button type="button" class="sb-star is-on" data-sb-star data-path="' + esc(path) + '" data-label="' + esc(label) + '" title="Remove from favourites" aria-label="Remove ' + esc(label) + ' from favourites"><i data-lucide="star"></i></button>';
    return row;
  }
  sb.addEventListener('click', function (e) {
    var star = e.target.closest('[data-sb-star]');
    if (star) {
      e.preventDefault();
      var path = star.getAttribute('data-path'), label = star.getAttribute('data-label');
      var body = new URLSearchParams({ path: path, label: label, next: path });
      star.disabled = true;
      fetch('/favourites/toggle', { method: 'POST', body: body, credentials: 'same-origin', headers: { 'Content-Type': 'application/x-www-form-urlencoded' } })
        .then(function (r) {
          if (!r.ok) { throw new Error(r.status); }
          var existing = Array.prototype.find.call(favBox.querySelectorAll('[data-sb-star]'), function (b) { return b.getAttribute('data-path') === path; });
          if (existing) { existing.closest('.sb-row').remove(); } else { favBox.appendChild(favRow(path, label)); icons(); }
          paintStars(); renderRecent();
        })
        .catch(function () { if (window.OQC && OQC.toast) { OQC.toast('The favourite could not be saved. Try again.', 'error'); } })
        .then(function () { star.disabled = false; });
      return;
    }
    var gb = e.target.closest('.sb-grp-btn');
    if (gb) {
      var g = gb.closest('[data-sb-grp]');
      if (root.classList.contains('sb-rail')) { setRail(false); setOpen(g, true); }
      else if (!sb.classList.contains('is-searching')) { setOpen(g, !g.classList.contains('is-open')); }
      saveOpen(); syncAllBtn();
      return;
    }
    if (e.target.closest('[data-sb-rail]')) { setRail(!root.classList.contains('sb-rail')); return; }
    if (e.target.closest('[data-sb-all]')) {
      var any = groups.some(function (x) { return x.classList.contains('is-open') && !x.classList.contains('sb-grp-flat'); });
      groups.forEach(function (x) { if (!x.classList.contains('sb-grp-flat')) { setOpen(x, !any); } });
      saveOpen(); syncAllBtn();
    }
  });

  // ------------------------------------------------------------------ recent pages (this area's, this browser)
  var recentWrap = sb.querySelector('[data-sb-recent-wrap]'), recentBox = sb.querySelector('[data-sb-recent]');
  function readRecent() { try { var r = JSON.parse(get(RECENT_KEY) || '[]'); return Array.isArray(r) ? r : []; } catch (e) { return []; } }
  (function remember() {
    var here = labels.find(function (a) { return a.getAttribute('href') === activeUrl; });
    if (!here) { return; }
    var r = readRecent().filter(function (x) { return x.p !== activeUrl; });
    r.unshift({ p: activeUrl, l: here.getAttribute('data-sb-label') });
    set(RECENT_KEY, JSON.stringify(r.slice(0, 12)));
  })();
  function renderRecent() {
    var inArea = {};
    labels.forEach(function (a) { inArea[a.getAttribute('href')] = a.getAttribute('data-sb-label'); });
    var favs = favPaths();
    var rows = readRecent().filter(function (x) { return inArea[x.p] && x.p !== activeUrl && favs.indexOf(x.p.split('?')[0]) < 0; }).slice(0, 4);
    recentBox.innerHTML = rows.map(function (x) {
      return '<div class="sb-row"><a href="' + esc(x.p) + '" class="sb-item sb-flat"><i data-lucide="history"></i><span class="sb-t">' + esc(inArea[x.p]) + '</span></a></div>';
    }).join('');
    recentWrap.hidden = rows.length === 0;
    icons();
  }
  paintStars();
  renderRecent();

  // keep the open page in view
  var act = sb.querySelector('.sb-item.is-active');
  if (act && list) { var top = act.offsetTop - list.clientHeight / 2; if (top > 0) { list.scrollTop = top; } }
})();
