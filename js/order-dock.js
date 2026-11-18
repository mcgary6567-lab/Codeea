/* Phone order dock: a slim bar pinned to the bottom of the screen on phones.
   Solo-ads pages: "Order now" opens a sheet with the three packages, each a direct Gumroad checkout.
   Lead Agent page: "Buy now" goes straight to the Lead Agent checkout.
   Hidden on wider screens, at the top of the page, while a pricing section or the footer is on screen,
   and while the menu is open. */
(function () {
  'use strict';
  if (window.__orderDock) return; window.__orderDock = 1;
  var G = 'https://goldempire.gumroad.com/l/';
  var PKGS = [
    { name: 'Starter', clicks: '300', bonus: '≈330', price: '$100', per: '$0.33', slug: 'solo-ads-starter', note: 'Test a new offer' },
    { name: 'Standard', clicks: '600', bonus: '≈660', price: '$200', per: '$0.33', slug: 'solo-ads-standard', note: 'Most popular', pop: true },
    { name: 'Premium', clicks: '1,250', bonus: '≈1,375', price: '$300', per: '$0.24', slug: 'solo-ads-premium', note: 'Best price per click' }
  ];
  var isLA = /\/lead-agent(\.html)?\/?$/.test(location.pathname);
  var arrow = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>';
  var lock = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>';

  var css = '' +
    '.od-bar,.od-sheet,.od-back{display:none}' +
    '@media (max-width:768px){' +
    'body{padding-bottom:calc(78px + env(safe-area-inset-bottom))}' +
    '.od-bar{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:900;align-items:center;gap:.75rem;padding:.65rem 1rem calc(.65rem + env(safe-area-inset-bottom));background:rgba(255,255,255,.97);-webkit-backdrop-filter:saturate(1.6) blur(12px);backdrop-filter:saturate(1.6) blur(12px);border-top:1px solid #E5E7EB;box-shadow:0 -10px 30px -12px rgba(15,23,42,.25);transform:translateY(110%);transition:transform .28s ease;font-family:inherit}' +
    '.od-bar.on{transform:none}' +
    '.od-txt{flex:1;min-width:0;line-height:1.25}' +
    '.od-txt b{display:block;font-size:.98rem;color:#0F172A;font-family:var(--font-display,inherit);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.od-txt span{display:block;font-size:.78rem;color:#4B5563;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.od-go{flex:0 0 auto;display:inline-flex;align-items:center;gap:.4rem;min-height:48px;padding:0 1.25rem;border:0;border-radius:999px;background:linear-gradient(135deg,#F97316,#F59E0B);color:#fff;font:700 1rem/1 var(--font-display,inherit);text-decoration:none;box-shadow:0 8px 20px -8px rgba(249,115,22,.7);cursor:pointer;-webkit-tap-highlight-color:transparent}' +
    '.od-go:active{transform:scale(.97)}' +
    '.od-back{display:block;position:fixed;inset:0;z-index:1000;background:rgba(15,23,42,.55);opacity:0;pointer-events:none;transition:opacity .25s ease}' +
    '.od-back.on{opacity:1;pointer-events:auto}' +
    '.od-sheet{display:block;position:fixed;left:0;right:0;bottom:0;z-index:1001;max-height:88vh;overflow-y:auto;overscroll-behavior:contain;background:#fff;border-radius:22px 22px 0 0;padding:.6rem 1rem calc(1rem + env(safe-area-inset-bottom));transform:translateY(105%);transition:transform .3s cubic-bezier(.2,.8,.2,1);box-shadow:0 -20px 50px -20px rgba(15,23,42,.5)}' +
    '.od-sheet.on{transform:none}' +
    '.od-grab{width:42px;height:5px;border-radius:9px;background:#E5E7EB;margin:0 auto .7rem}' +
    '.od-head{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;margin-bottom:.9rem}' +
    '.od-head h2{font-size:1.25rem;line-height:1.25;margin:0;color:#0F172A}' +
    '.od-head p{margin:.25rem 0 0;font-size:.85rem;color:#4B5563}' +
    '.od-x{flex:0 0 44px;width:44px;height:44px;border:0;border-radius:50%;background:#F3F4F6;color:#111827;font-size:1.4rem;line-height:1;cursor:pointer}' +
    '.od-pk{position:relative;display:flex;align-items:center;gap:.8rem;padding:.95rem 1rem;margin-bottom:.65rem;border:1.5px solid #E5E7EB;border-radius:16px;color:inherit;text-decoration:none;-webkit-tap-highlight-color:transparent}' +
    '.od-pk:active{background:#F9FAFB}' +
    '.od-pk.pop{border-color:#4F46E5;box-shadow:0 10px 26px -16px rgba(79,70,229,.8)}' +
    '.od-tag{position:absolute;top:-.6rem;left:1rem;padding:.15rem .55rem;border-radius:999px;background:#4F46E5;color:#fff;font-size:.68rem;font-weight:800;letter-spacing:.04em;text-transform:uppercase}' +
    '.od-info{flex:1;min-width:0}' +
    '.od-info b{display:block;font-size:1.02rem;color:#0F172A}' +
    '.od-info span{display:block;font-size:.8rem;color:#4B5563;margin-top:.15rem}' +
    '.od-buy{flex:0 0 auto;text-align:right}' +
    '.od-buy b{display:block;font-size:1.3rem;color:#0F172A;font-family:var(--font-display,inherit)}' +
    '.od-buy span{display:inline-flex;align-items:center;gap:.25rem;margin-top:.2rem;padding:.4rem .75rem;border-radius:999px;background:#4F46E5;color:#fff;font-size:.82rem;font-weight:700}' +
    '.od-pk:not(.pop) .od-buy span{background:#EEF2FF;color:#4338CA}' +
    '.od-foot{display:flex;align-items:center;justify-content:center;gap:.35rem;margin:.4rem 0 .2rem;font-size:.8rem;color:#6B7280}' +
    '.od-more{display:block;text-align:center;padding:.8rem;font-weight:700;color:#4F46E5;text-decoration:none}' +
    'html.od-lock,html.od-lock body{overflow:hidden}' +
    '}' +
    '@media (prefers-reduced-motion:reduce){.od-bar,.od-sheet,.od-back{transition:none}}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  var bar = document.createElement('div');
  bar.className = 'od-bar'; bar.setAttribute('role', 'region'); bar.setAttribute('aria-label', 'Quick order');
  if (isLA) {
    bar.innerHTML = '<div class="od-txt"><b>Lead Agent</b><span>$299 once · lifetime access</span></div>' +
      '<a class="od-go" href="' + G + 'lead-agent?wanted=true" rel="noopener">Buy now ' + arrow + '</a>';
  } else {
    bar.innerHTML = '<div class="od-txt"><b>Solo ads from $100</b><span>90% Tier-1 human clicks</span></div>' +
      '<button type="button" class="od-go" aria-haspopup="dialog" aria-controls="od-sheet">Order now ' + arrow + '</button>';
  }
  document.body.appendChild(bar);

  var sheet, back, lastFocus;
  if (!isLA) {
    back = document.createElement('div'); back.className = 'od-back';
    sheet = document.createElement('div');
    sheet.className = 'od-sheet'; sheet.id = 'od-sheet';
    sheet.setAttribute('role', 'dialog'); sheet.setAttribute('aria-modal', 'true'); sheet.setAttribute('aria-labelledby', 'od-title');
    sheet.setAttribute('aria-hidden', 'true');
    var rows = PKGS.map(function (p) {
      return '<a class="od-pk' + (p.pop ? ' pop' : '') + '" href="' + G + p.slug + '?wanted=true" rel="noopener">' +
        (p.pop ? '<span class="od-tag">Most popular</span>' : '') +
        '<span class="od-info"><b>' + p.name + ' · ' + p.clicks + ' clicks</b><span>' + p.bonus + ' delivered · ' + p.per + '/click</span></span>' +
        '<span class="od-buy"><b>' + p.price + '</b><span>Order ' + arrow + '</span></span></a>';
    }).join('');
    sheet.innerHTML = '<div class="od-grab" aria-hidden="true"></div>' +
      '<div class="od-head"><div><h2 id="od-title">Choose your package</h2><p>Tier-1 human traffic · +10% bonus clicks</p></div>' +
      '<button type="button" class="od-x" aria-label="Close">&times;</button></div>' + rows +
      '<div class="od-foot">' + lock + ' Secure checkout by Gumroad</div>' +
      '<a class="od-more" href="/services#pricing">Compare what each package includes</a>';
    document.body.appendChild(back); document.body.appendChild(sheet);
  }

  function openSheet() {
    if (!sheet) return;
    lastFocus = document.activeElement;
    back.classList.add('on'); sheet.classList.add('on'); sheet.setAttribute('aria-hidden', 'false');
    document.documentElement.classList.add('od-lock');
    var f = sheet.querySelector('.od-pk'); if (f) f.focus({ preventScroll: true });
  }
  function closeSheet() {
    if (!sheet || !sheet.classList.contains('on')) return;
    back.classList.remove('on'); sheet.classList.remove('on'); sheet.setAttribute('aria-hidden', 'true');
    document.documentElement.classList.remove('od-lock');
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }
  if (sheet) {
    bar.querySelector('.od-go').addEventListener('click', openSheet);
    back.addEventListener('click', closeSheet);
    sheet.querySelector('.od-x').addEventListener('click', closeSheet);
    sheet.querySelector('.od-more').addEventListener('click', closeSheet);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeSheet(); });
    // drag the sheet down to close it
    var y0 = null;
    sheet.addEventListener('touchstart', function (e) { y0 = sheet.scrollTop <= 0 ? e.touches[0].clientY : null; }, { passive: true });
    sheet.addEventListener('touchmove', function (e) {
      if (y0 === null) return; var d = e.touches[0].clientY - y0;
      if (d > 0) { sheet.style.transition = 'none'; sheet.style.transform = 'translateY(' + d + 'px)'; }
    }, { passive: true });
    sheet.addEventListener('touchend', function (e) {
      if (y0 === null) return; var d = e.changedTouches[0].clientY - y0; y0 = null;
      sheet.style.transition = ''; sheet.style.transform = '';
      if (d > 90) closeSheet();
    });
  }

  // when to show the bar
  var inView = 0, menu = document.getElementById('nav-menu');
  function update() {
    var show = window.scrollY > 260 && inView === 0 && !(menu && menu.classList.contains('active'));
    bar.classList.toggle('on', show);
  }
  if ('IntersectionObserver' in window) {
    var watch = document.querySelectorAll('#pricing, #buy, .pricing-grid, footer');
    var seen = new Set();
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) seen.add(e.target); else seen.delete(e.target); });
      inView = seen.size; update();
    }, { rootMargin: '0px 0px -25% 0px' });
    watch.forEach(function (el) { io.observe(el); });
  }
  if (menu && 'MutationObserver' in window) new MutationObserver(update).observe(menu, { attributes: true, attributeFilter: ['class'] });
  window.addEventListener('scroll', update, { passive: true });
  update();
})();
