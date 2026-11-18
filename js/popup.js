/* Lead Agent promo popup (home page).
   Desktop: opens when the mouse leaves through the top of the window (exit intent).
   Phones: opens when the visitor has read half the page and scrolls back up.
   Shows at most once every 7 days per visitor. Close: X, "Maybe later", Esc, click outside. */
(function () {
  'use strict';
  var ov = document.getElementById('lp-promo');
  if (!ov) return;
  var KEY = 'la_promo_seen';
  var DAYS = 7;
  var MIN_MS = 6000; // never in the first 6 seconds
  var start = Date.now();
  var shown = false;
  var lastFocus = null;

  function seenRecently() {
    try {
      var t = parseInt(localStorage.getItem(KEY) || '0', 10);
      return t && Date.now() - t < DAYS * 864e5;
    } catch (e) { return false; }
  }
  function remember() { try { localStorage.setItem(KEY, String(Date.now())); } catch (e) {} }
  if (seenRecently() || /[?&]nopromo\b/.test(location.search)) return;

  function menuOpen() { var m = document.getElementById('nav-menu'); return m && m.classList.contains('active'); }

  function open() {
    if (shown || Date.now() - start < MIN_MS || menuOpen()) return;
    shown = true;
    remember();
    ov.querySelectorAll('img[data-src]').forEach(function (img) { img.src = img.getAttribute('data-src'); img.removeAttribute('data-src'); });
    lastFocus = document.activeElement;
    ov.hidden = false;
    document.documentElement.classList.add('lp-lock');
    requestAnimationFrame(function () { requestAnimationFrame(function () { ov.classList.add('is-open'); }); });
    var x = ov.querySelector('.lp-x');
    if (x) setTimeout(function () { x.focus({ preventScroll: true }); }, 60);
    detach();
  }
  function close() {
    ov.classList.remove('is-open');
    document.documentElement.classList.remove('lp-lock');
    setTimeout(function () { ov.hidden = true; }, 280);
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }

  ov.addEventListener('click', function (e) {
    if (e.target === ov || e.target.closest('[data-lp-close]')) { e.preventDefault(); close(); }
  });
  document.addEventListener('keydown', function (e) {
    if (ov.hidden) return;
    if (e.key === 'Escape') { close(); return; }
    if (e.key === 'Tab') { // keep focus inside the popup
      var f = ov.querySelectorAll('a[href],button');
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  // Desktop: exit intent
  function onLeave(e) { if (!e.relatedTarget && e.clientY <= 8) open(); }
  // Phones: read at least half the page, then scroll back up quickly
  var maxY = 0, peak = 0, lastY = window.scrollY;
  function onScroll() {
    var y = window.scrollY;
    if (y > maxY) maxY = y;
    if (y >= lastY) peak = y; // going down: the turn-around point moves with it
    var readEnough = (maxY + window.innerHeight) / Math.max(1, document.documentElement.scrollHeight) >= 0.5;
    if (readEnough && peak - y > 250) open(); // scrolled back up about a screen-third
    lastY = y;
  }
  var fine = window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  function detach() {
    document.removeEventListener('mouseout', onLeave);
    window.removeEventListener('scroll', onScroll);
  }
  if (fine) document.addEventListener('mouseout', onLeave);
  else window.addEventListener('scroll', onScroll, { passive: true });
  // Manual test hook: #lead-agent-promo opens it at once
  if (location.hash === '#lead-agent-promo') { start = 0; setTimeout(open, 300); }
})();
