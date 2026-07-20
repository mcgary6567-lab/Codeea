/* Gold Scalpers — cookie consent banner (self-contained, no dependencies) */
(function () {
  var KEY = 'gsCookieConsent';
  var styleInjected = false;
  var bar = null;

  function injectStyle() {
    if (styleInjected) return;
    styleInjected = true;
    var css = ''
      + '.gs-cookie{position:fixed;left:0;right:0;bottom:0;z-index:10000;background:#0F1422;border-top:1px solid #1F2937;box-shadow:0 -8px 30px rgba(0,0,0,0.35);transform:translateY(115%);transition:transform .32s cubic-bezier(.4,0,.2,1);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}'
      + '.gs-cookie.gs-in{transform:translateY(0)}'
      + '.gs-cookie.gs-out{transform:translateY(115%)}'
      + '.gs-cookie-inner{max-width:1100px;margin:0 auto;display:flex;align-items:center;gap:1.2rem;padding:1rem 1.5rem}'
      + '.gs-cookie-ico{flex:0 0 auto;width:42px;height:42px;border-radius:11px;background:rgba(245,158,11,0.12);border:1px solid #3a2e12;display:flex;align-items:center;justify-content:center;font-size:1.35rem}'
      + '.gs-cookie-txt{flex:1 1 auto;min-width:0}'
      + '.gs-cookie-txt h4{margin:0 0 .15rem;font-size:.95rem;font-weight:700;color:#F4F6FA}'
      + '.gs-cookie-txt p{margin:0;font-size:.84rem;color:#9CA3AF;line-height:1.5}'
      + '.gs-cookie-txt a{color:#F59E0B;text-decoration:none}'
      + '.gs-cookie-txt a:hover{text-decoration:underline}'
      + '.gs-cookie-btns{flex:0 0 auto;display:flex;gap:.6rem}'
      + '.gs-cookie-btn{font-family:inherit;font-size:.88rem;font-weight:700;padding:.62rem 1.25rem;border-radius:9px;cursor:pointer;border:1px solid transparent;white-space:nowrap;transition:all .18s}'
      + '.gs-cookie-btn.ghost{background:#1A2230;color:#E6E9EF;border-color:#2A3443}'
      + '.gs-cookie-btn.ghost:hover{background:#222c3d}'
      + '.gs-cookie-btn.accept{background:linear-gradient(135deg,#F59E0B,#D97706);color:#1a1206}'
      + '.gs-cookie-btn.accept:hover{filter:brightness(1.06);transform:translateY(-1px)}'
      + '@media(max-width:640px){.gs-cookie-inner{flex-wrap:wrap;gap:.8rem;padding:1rem 1.1rem}.gs-cookie-btns{width:100%}.gs-cookie-btn{flex:1 1 auto;text-align:center}}';
    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }

  function choose(value) {
    try { localStorage.setItem(KEY, value); } catch (e) {}
    var b = bar;
    if (!b) return;
    b.classList.remove('gs-in');
    b.classList.add('gs-out');
    bar = null;
    setTimeout(function () { if (b && b.parentNode) b.parentNode.removeChild(b); }, 320);
    // When analytics are added, load them here only if value === 'all'.
  }

  function openBanner() {
    injectStyle();
    if (bar && bar.parentNode) {           // already open — just keep it visible
      bar.classList.remove('gs-out');
      bar.classList.add('gs-in');
      return;
    }
    bar = document.createElement('div');
    bar.className = 'gs-cookie';
    bar.setAttribute('role', 'dialog');
    bar.setAttribute('aria-label', 'Cookie consent');
    bar.innerHTML =
        '<div class="gs-cookie-inner">'
      +   '<div class="gs-cookie-ico" aria-hidden="true">🍪</div>'
      +   '<div class="gs-cookie-txt">'
      +     '<h4>We use cookies</h4>'
      +     '<p>Essential cookies keep the site working. Optional analytics cookies help us improve. Pick your preference — you can change it anytime in our <a href="/privacy-policy">Privacy Policy</a>.</p>'
      +   '</div>'
      +   '<div class="gs-cookie-btns">'
      +     '<button class="gs-cookie-btn ghost" type="button" data-gs="essential">Essential only</button>'
      +     '<button class="gs-cookie-btn accept" type="button" data-gs="all">Accept all</button>'
      +   '</div>'
      + '</div>';
    document.body.appendChild(bar);
    bar.querySelector('[data-gs="all"]').addEventListener('click', function () { choose('all'); });
    bar.querySelector('[data-gs="essential"]').addEventListener('click', function () { choose('essential'); });
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { if (bar) bar.classList.add('gs-in'); });
    });
  }

  // Show automatically on first visit (no choice stored yet).
  var consent = null;
  try { consent = localStorage.getItem(KEY); } catch (e) {}
  if (!consent) openBanner();
})();
