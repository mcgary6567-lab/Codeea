/* Phone order dock: a slim bar pinned to the bottom of the screen on phones.
   Solo-ads pages: "Choose clicks" opens a sheet with one click-volume selector and one pay button
   that goes straight to that package's Gumroad checkout.
   Lead Agent page: "Buy now" goes straight to the Lead Agent checkout.
   Hidden on wider screens, at the top of the page, while a pricing section or the footer is on screen,
   and while the menu is open.
   Prices and clicks live in PKGS below: update them whenever the packages change. */
(function () {
  'use strict';
  if (window.__orderDock) return; window.__orderDock = 1;
  var G = 'https://goldempire.gumroad.com/l/';
  var PKGS = [
    { name: 'Starter', clicks: 300, label: '300', delivered: '330', price: 100, per: '$0.33', slug: 'solo-ads-starter',
      fit: 'Test a new offer or page', gets: ['Free email swipe copy', 'Squeeze page review', 'Real-time click tracking'] },
    { name: 'Standard', clicks: 600, label: '600', delivered: '660', price: 200, per: '$0.33', slug: 'solo-ads-standard',
      fit: 'Most popular, the one clients re-order', gets: ['Pro copy and funnel review', 'Priority queue placement', 'Geo and device click breakdown'] },
    { name: 'Premium', clicks: 1250, label: '1,250', delivered: '1,375', price: 300, per: '$0.24', slug: 'solo-ads-premium',
      fit: 'Lowest price per click, for a proven funnel', gets: ['Premium copy and sales page audit', 'Dedicated account manager', 'Post-campaign optimisation call'] }
  ];
  var DEFAULT = 1;
  var isLA = /\/lead-agent(\.html)?\/?$/.test(location.pathname);
  var tick = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 5 5L20 7"/></svg>';

  var css = '' +
    '.od-bar,.od-sheet,.od-back,.od-inline{display:none}' +
    '@media (max-width:768px){' +
    'body{padding-bottom:calc(76px + env(safe-area-inset-bottom))}' +
    '.od-bar{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:900;align-items:center;gap:.75rem;padding:.6rem 1rem calc(.6rem + env(safe-area-inset-bottom));background:rgba(255,255,255,.97);-webkit-backdrop-filter:saturate(1.6) blur(12px);backdrop-filter:saturate(1.6) blur(12px);border-top:1px solid #E2E8F0;transform:translateY(110%);transition:transform .28s ease}' +
    '.od-bar.on{transform:none}' +
    '.od-txt{flex:1;min-width:0;line-height:1.3}' +
    '.od-txt b{display:block;font:700 .98rem/1.3 var(--font-display,inherit);color:#0F172A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.od-txt span{display:block;font-size:.8rem;color:#475569;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.od-go{flex:0 0 auto;display:inline-flex;align-items:center;min-height:48px;padding:0 1.3rem;border:0;border-radius:12px;background:#4F46E5;color:#fff;font:700 .98rem/1 var(--font-display,inherit);text-decoration:none;cursor:pointer;-webkit-tap-highlight-color:transparent}' +
    '.od-go:active{background:#4338CA}' +
    '.od-bar .od-go:focus-visible,.od-sheet :focus-visible{outline:3px solid #F97316;outline-offset:2px}' +
    '.od-back{display:block;position:fixed;inset:0;z-index:1000;background:rgba(15,23,42,.5);opacity:0;pointer-events:none;transition:opacity .25s ease}' +
    '.od-back.on{opacity:1;pointer-events:auto}' +
    '.od-sheet{display:block;position:fixed;left:0;right:0;bottom:0;z-index:1001;max-height:92vh;overflow-y:auto;overscroll-behavior:contain;background:#fff;border-radius:20px 20px 0 0;padding:.55rem 1.25rem calc(1rem + env(safe-area-inset-bottom));transform:translateY(105%);transition:transform .32s cubic-bezier(.2,.8,.2,1);color:#0F172A;font-family:var(--font-sans,inherit)}' +
    '.od-sheet.on{transform:none}' +
    '.od-grab{width:40px;height:5px;border-radius:9px;background:#E2E8F0;margin:0 auto .9rem}' +
    '.od-head{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem}' +
    '.od-head h2{font:800 1.4rem/1.2 var(--font-display,inherit);letter-spacing:-.01em;margin:0}' +
    '.od-head p{margin:.35rem 0 0;font-size:.9rem;line-height:1.45;color:#475569;max-width:30ch}' +
    '.od-x{flex:0 0 44px;width:44px;height:44px;margin:-.2rem -.4rem 0 0;border:0;border-radius:50%;background:transparent;color:#475569;font-size:1.6rem;line-height:1;cursor:pointer}' +
    '.od-pick{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin:1.25rem 0 0;padding:4px;border:0;border-radius:14px;background:#EEF2FF}' +
    '.od-pick legend{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}' +
    '.od-opt{position:relative}' +
    '.od-opt input{position:absolute;opacity:0;inset:0;margin:0;cursor:pointer}' +
    '.od-opt span{display:block;padding:.7rem .25rem .6rem;border-radius:11px;text-align:center;cursor:pointer;transition:background .18s ease,color .18s ease}' +
    '.od-opt b{display:block;font:800 1.35rem/1.1 var(--font-display,inherit);font-variant-numeric:tabular-nums;letter-spacing:-.02em}' +
    '.od-opt i{display:block;font-style:normal;font-size:.8rem;color:#475569;margin-top:.15rem}' +
    '.od-opt input:checked+span{background:#4F46E5;color:#fff;box-shadow:0 6px 16px -8px rgba(79,70,229,.9)}' +
    '.od-opt input:checked+span i{color:#E0E7FF}' +
    '.od-opt input:focus-visible+span{outline:3px solid #F97316;outline-offset:1px}' +
    '.od-vol{height:6px;margin:.85rem 2px 0;border-radius:6px;background:#E2E8F0;overflow:hidden}' +
    '.od-vol div{height:100%;width:48%;border-radius:6px;background:#4F46E5;transition:width .35s cubic-bezier(.2,.8,.2,1)}' +
    '.od-read{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;margin-top:1.1rem}' +
    '.od-read strong{font:800 1.15rem/1.3 var(--font-display,inherit)}' +
    '.od-read em{font:600 .95rem/1.3 var(--font-display,inherit);font-style:normal;font-variant-numeric:tabular-nums;color:#4F46E5;white-space:nowrap}' +
    '.od-fit{margin:.2rem 0 0;font-size:.88rem;color:#475569}' +
    '.od-gets{list-style:none;margin:1rem 0 0;padding:.9rem 0 0;border-top:1px solid #E2E8F0;display:grid;gap:.55rem}' +
    '.od-gets li{display:flex;gap:.55rem;align-items:flex-start;font-size:.92rem;line-height:1.4}' +
    '.od-gets svg{flex:0 0 16px;margin-top:.15rem;color:#16A34A}' +
    '.od-pay{display:flex;align-items:center;justify-content:center;width:100%;min-height:56px;margin-top:1.25rem;border-radius:14px;background:#F97316;color:#fff;font:800 1.08rem/1 var(--font-display,inherit);text-decoration:none;box-shadow:0 10px 24px -12px rgba(249,115,22,.9);-webkit-tap-highlight-color:transparent}' +
    '.od-pay:active{background:#EA580C}' +
    '.od-note{margin:.6rem 0 0;text-align:center;font-size:.8rem;color:#475569}' +
    '.od-more{display:block;margin-top:.35rem;padding:.75rem;text-align:center;font-weight:600;font-size:.9rem;color:#4F46E5;text-decoration:none}' +
    'html.od-lock,html.od-lock body{overflow:hidden}' +
    '.od-inline{display:block;max-width:480px;margin:0 auto;padding:1.25rem 1.25rem 1rem;background:#fff;border:1px solid #E2E8F0;border-radius:18px;color:#0F172A;text-align:left}' +
    '.od-inline .od-q{margin:0;color:#0F172A;font:800 1.25rem/1.25 var(--font-display,inherit)}' +
    '.od-inline .od-pick{margin-top:.9rem}' +
    '.od-cmp{width:100%;border:0;background:none;cursor:pointer;font-family:inherit}' +
    '.od-has-inline .pricing-grid{display:none}' +
    '.od-has-inline.od-show-grid .pricing-grid{display:grid;margin-top:1.5rem}' +
    '}' +
    '@media (prefers-reduced-motion:reduce){.od-bar,.od-sheet,.od-back,.od-vol div,.od-opt span{transition:none}}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  var bar = document.createElement('div');
  bar.className = 'od-bar'; bar.setAttribute('role', 'region'); bar.setAttribute('aria-label', 'Quick order');
  if (isLA) {
    bar.innerHTML = '<div class="od-txt"><b>Lead Agent for $299</b><span>One payment, lifetime access</span></div>' +
      '<a class="od-go" href="' + G + 'lead-agent?wanted=true" rel="noopener">Buy now</a>';
  } else {
    bar.innerHTML = '<div class="od-txt"><b>Solo ads from $100</b><span>90% Tier-1 human clicks</span></div>' +
      '<button type="button" class="od-go" aria-haspopup="dialog" aria-controls="od-sheet">Choose clicks</button>';
  }
  document.body.appendChild(bar);

  // one click-volume picker; used in the phone sheet and inline in the pricing sections
  var max = PKGS[PKGS.length - 1].clicks;
  function pickerHTML(name, more) {
    var opts = PKGS.map(function (p, i) {
      return '<label class="od-opt"><input type="radio" name="' + name + '" value="' + i + '"' + (i === DEFAULT ? ' checked' : '') + '>' +
        '<span><b>' + p.label + '</b><i>$' + p.price + '</i></span></label>';
    }).join('');
    return '<fieldset class="od-pick"><legend>Number of clicks</legend>' + opts + '</fieldset>' +
      '<div class="od-vol" aria-hidden="true"><div></div></div>' +
      '<div class="od-read"><strong class="od-name"></strong><em class="od-price"></em></div>' +
      '<p class="od-fit"></p>' +
      '<ul class="od-gets"></ul>' +
      '<a class="od-pay" rel="noopener"></a>' +
      '<p class="od-note">Checkout opens on Gumroad, a secure payment page.</p>' + more;
  }
  function wirePicker(root) {
    function render(i) {
      var p = PKGS[i];
      root.querySelector('.od-vol div').style.width = Math.max(14, Math.round(p.clicks / max * 100)) + '%';
      root.querySelector('.od-name').textContent = p.name;
      root.querySelector('.od-price').textContent = p.per + ' a click';
      root.querySelector('.od-fit').textContent = 'About ' + p.delivered + ' clicks delivered. ' + p.fit + '.';
      root.querySelector('.od-gets').innerHTML = ['90% from the US, UK, Canada and Australia'].concat(p.gets, ['30%+ opt-in guarantee'])
        .map(function (g) { return '<li>' + tick + '<span>' + g + '</span></li>'; }).join('');
      var pay = root.querySelector('.od-pay');
      pay.href = G + p.slug + '?wanted=true';
      pay.textContent = 'Pay $' + p.price + ' for ' + p.label + ' clicks';
    }
    root.querySelectorAll('.od-opt input').forEach(function (r) { r.addEventListener('change', function () { render(+r.value); }); });
    render(DEFAULT);
  }

  // inline picker in the solo-ads pricing section (home + services), phones only; the cards stay one tap away
  var grid = !isLA && document.querySelector('#pricing .pricing-grid');
  if (grid && grid.querySelector('a[href*="gumroad.com/l/solo-ads-"]')) {
    var inl = document.createElement('div');
    inl.className = 'od-inline'; inl.id = 'od-inline';
    inl.innerHTML = '<p class="od-q" id="od-q">How many clicks?</p>' +
      pickerHTML('od-pk-inline', '<button type="button" class="od-more od-cmp" aria-expanded="false" aria-controls="od-grid">Compare all three packages</button>');
    inl.querySelector('.od-pick').setAttribute('aria-labelledby', 'od-q');
    grid.id = grid.id || 'od-grid';
    grid.parentNode.insertBefore(inl, grid);
    grid.parentNode.classList.add('od-has-inline');
    wirePicker(inl);
    var cmp = inl.querySelector('.od-cmp');
    cmp.addEventListener('click', function () {
      var open = grid.parentNode.classList.toggle('od-show-grid');
      cmp.setAttribute('aria-expanded', open);
      cmp.textContent = open ? 'Hide the package comparison' : 'Compare all three packages';
      if (open) grid.querySelectorAll('.reveal').forEach(function (e) { e.classList.add('visible'); });
    });
  }

  var sheet, back, lastFocus;
  if (!isLA) {
    back = document.createElement('div'); back.className = 'od-back';
    sheet = document.createElement('div');
    sheet.className = 'od-sheet'; sheet.id = 'od-sheet';
    sheet.setAttribute('role', 'dialog'); sheet.setAttribute('aria-modal', 'true'); sheet.setAttribute('aria-labelledby', 'od-title');
    sheet.setAttribute('aria-hidden', 'true');
    sheet.innerHTML = '<div class="od-grab" aria-hidden="true"></div>' +
      '<div class="od-head"><div><h2 id="od-title">How many clicks?</h2>' +
      '<p>Tier-1 subscribers clicking through to your page, plus 10% extra on every run.</p></div>' +
      '<button type="button" class="od-x" aria-label="Close">&times;</button></div>' +
      pickerHTML('od-pk', '<a class="od-more" href="' + (grid ? '#pricing' : '/services#pricing') + '">See everything in each package</a>');
    document.body.appendChild(back); document.body.appendChild(sheet);
    wirePicker(sheet);
  }

  function focusables() { return [].slice.call(sheet.querySelectorAll('button, a[href], input:checked')); }
  function openSheet() {
    if (!sheet) return;
    lastFocus = document.activeElement;
    back.classList.add('on'); sheet.classList.add('on'); sheet.setAttribute('aria-hidden', 'false');
    document.documentElement.classList.add('od-lock');
    var c = sheet.querySelector('input:checked'); if (c) c.focus({ preventScroll: true });
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
    sheet.querySelector('.od-more').addEventListener('click', function () {
      closeSheet();
      var c = document.querySelector('.od-cmp');  // on home/services, open the comparison under the picker
      if (c && c.getAttribute('aria-expanded') !== 'true') c.click();
    });
    document.addEventListener('keydown', function (e) {
      if (!sheet.classList.contains('on')) return;
      if (e.key === 'Escape') return closeSheet();
      if (e.key === 'Tab') {  // keep keyboard focus inside the sheet
        var f = focusables(), first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
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
    var seen = new Set();
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) seen.add(e.target); else seen.delete(e.target); });
      inView = seen.size; update();
    }, { rootMargin: '0px 0px -25% 0px' });
    document.querySelectorAll('#pricing, #buy, .pricing-grid, .od-inline, footer').forEach(function (el) { io.observe(el); });
  }
  if (menu && 'MutationObserver' in window) new MutationObserver(update).observe(menu, { attributes: true, attributeFilter: ['class'] });
  window.addEventListener('scroll', update, { passive: true });
  update();
})();
