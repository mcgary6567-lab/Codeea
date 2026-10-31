/*
 * "Talk directly to Jono Armstrong" popup — Prometheus AI
 *
 * Centered spotlight card over a dimmed page, with a click-to-play video,
 * Jono's photo and WhatsApp / Telegram buttons. Clicking the backdrop,
 * the X or Escape closes it.
 *
 * Behaviour (conversion settings):
 *  - Shows on the visitor's first scroll, or 30 s after load if they never
 *    scroll — but never within the first 5 s of the page loading.
 *  - Auto-opens once per visit (browser session). No launcher icon.
 *  - Hides the purchase-notification card while open so they never overlap.
 *  - Loads nothing (no CSS, no thumbnail) until it is actually shown.
 *  - Fires Google Ads / gtag events: jono_popup_open, jono_video_play,
 *    jono_whatsapp_click, jono_telegram_click, jono_popup_close.
 *  - Escape closes; dialog is labelled for screen readers.
 *
 * Config via data-attributes on the <script> tag:
 *   data-video  YouTube id            (default ADvR1rBXF_E)
 *   data-wa     WhatsApp number, digits only incl. country code
 *   data-tg     Telegram URL
 */
(function () {
  "use strict";
  var me = document.currentScript || (function () { var s = document.getElementsByTagName("script"); return s[s.length - 1]; })();
  var VIDEO = (me && me.getAttribute("data-video")) || "ADvR1rBXF_E";
  var WA = (me && me.getAttribute("data-wa")) || "13106293398";
  var TG = (me && me.getAttribute("data-tg")) || "https://t.me/prometheusaibot";
  var WA_TEXT = "Hi Jono, I'm looking at Prometheus AI and have a question.";
  var WA_URL = "https://wa.me/" + WA + "?text=" + encodeURIComponent(WA_TEXT);
  // Poster is Jono's own photo, not the YouTube thumbnail (which carries an earnings claim).

  var MIN_DELAY = 5000;      // never before 5 s
  var MAX_DELAY = 30000;     // auto-open at 30 s if no scroll
  var SCROLL_PX = 80;        // "started scrolling"

  var SS = { shown: "ttj_shown" };
  function ssGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function ssSet(k, v) { try { sessionStorage.setItem(k, v); } catch (e) {} }
  function track(name, extra) {
    try {
      if (typeof window.gtag === "function") window.gtag("event", name, extra || {});
      else if (window.dataLayer) window.dataLayer.push({ event: name });
    } catch (e) {}
  }

  var ICON_WA = '<svg viewBox="0 0 24 24" fill="#fff" aria-hidden="true"><path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2zm0 18.2c-1.5 0-3-.4-4.3-1.2l-.3-.2-3 .8.8-2.9-.2-.3A8.2 8.2 0 1 1 12 20.2zm4.5-6.1c-.2-.1-1.5-.7-1.7-.8s-.4-.1-.6.1-.6.8-.8 1-.3.2-.5.1a6.7 6.7 0 0 1-3.3-2.9c-.3-.4.3-.4.7-1.3.1-.2 0-.3 0-.4l-.8-1.8c-.2-.5-.4-.4-.6-.4h-.5a1 1 0 0 0-.7.3 3 3 0 0 0-.9 2.2 5.2 5.2 0 0 0 1.1 2.8 12 12 0 0 0 4.6 4c1.7.7 2.1.6 2.8.5a2.4 2.4 0 0 0 1.6-1.1c.2-.6.2-1 .1-1.1l-.5-.2z"/></svg>';
  var ICON_TG = '<svg viewBox="0 0 24 24" fill="#fff" aria-hidden="true"><path d="M9.8 15.5l-.4 5c.5 0 .8-.2 1.1-.5l2.6-2.5 5.3 3.9c1 .5 1.7.3 1.9-.9l3.5-16.2c.3-1.4-.5-2-1.5-1.6L1.6 10.4c-1.4.5-1.4 1.3-.2 1.6l5.2 1.6 12-7.6c.6-.4 1.1-.2.7.2L9.8 15.5z"/></svg>';
  var ICON_PLAY = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 68 48'%3E%3Cpath d='M66.5 7.7c-.8-2.9-3-5.2-5.9-6C55.4 0 34 0 34 0S12.6 0 7.4 1.7c-2.9.8-5.1 3.1-5.9 6C0 13 0 24 0 24s0 11 1.5 16.3c.8 2.9 3 5.2 5.9 6C12.6 48 34 48 34 48s21.4 0 26.6-1.7c2.9-.8 5.1-3.1 5.9-6C68 35 68 24 68 24s0-11-1.5-16.3z' fill='%23ff0000'/%3E%3Cpath d='M45 24L27 14v20' fill='%23fff'/%3E%3C/svg%3E";

  var CSS =
    ".ttj{position:fixed;left:50%;top:50%;width:400px;max-width:calc(100vw - 24px);max-height:calc(100vh - 24px);overflow:auto;z-index:2147483002;background:#0f172a;color:#fff;" +
    "border:1px solid rgba(25,211,211,.4);border-radius:20px;box-shadow:0 30px 90px rgba(0,0,0,.7);" +
    "font-family:Poppins,Montserrat,Helvetica,Arial,sans-serif;transform:translate(-50%,-50%) scale(.94);opacity:0;transition:transform .35s ease,opacity .35s ease}" +
    ".ttj-bd{position:fixed;inset:0;z-index:2147483001;background:rgba(0,0,0,.62);backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);opacity:0;transition:opacity .35s ease}" +
    ".ttj-bd.on{opacity:1}" +
".ttj.on{transform:translate(-50%,-50%) scale(1);opacity:1}" +
    ".ttj *{box-sizing:border-box}" +
    ".ttj-x{position:absolute;top:10px;right:12px;width:32px;height:32px;border-radius:50%;border:0;background:rgba(0,0,0,.55);color:#fff;font-size:18px;line-height:1;cursor:pointer;z-index:3;display:flex;align-items:center;justify-content:center}" +
    ".ttj-vid{position:relative;display:block;width:100%;aspect-ratio:16/9;background:#000 linear-gradient(135deg,#0e1a2a,#1b2a3d);overflow:hidden;cursor:pointer}" +
    ".ttj-vid img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}" +
    ".ttj-vid iframe{position:absolute;inset:0;width:100%;height:100%;border:0}" +
    ".ttj-vid .ttj-play{position:absolute;left:50%;top:74%;width:76px;height:52px;margin:-26px 0 0 -38px;background:url(\"" + ICON_PLAY + "\") center/contain no-repeat;filter:drop-shadow(0 4px 12px rgba(0,0,0,.6));transition:transform .15s}" +
    ".ttj-vid:hover .ttj-play{transform:scale(1.08)}" +
    ".ttj-cap{position:absolute;left:0;right:0;bottom:0;padding:28px 14px 9px;background:linear-gradient(transparent,rgba(0,0,0,.85));font-size:12px;color:#dbe4ee;display:flex;justify-content:space-between;pointer-events:none}" +
    ".ttj-body{padding:18px 22px 20px}" +
    ".ttj-who{display:flex;align-items:center;gap:10px}" +
    ".ttj-av{width:54px;height:54px;border-radius:50%;flex:none;position:relative;border:2px solid #19d3d3;background:#0e1a2a url(/img/jono.webp) center/cover no-repeat}" +
    ".ttj-av::after{content:'';position:absolute;right:0;bottom:0;width:14px;height:14px;border-radius:50%;background:#22c55e;border:2px solid #0f172a}" +
    ".ttj-who b{display:block;font-size:15px;line-height:1.2}.ttj-who span{font-size:12px;color:#9fb3c8}.ttj-who em{font-style:normal;color:#22c55e}" +
    ".ttj h3{margin:14px 0 6px;font-size:20px;font-weight:800;line-height:1.25}" +
    ".ttj p{margin:0 0 14px;font-size:13.5px;line-height:1.55;color:#9fb3c8}" +
    ".ttj-btn{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;padding:14px 16px;border-radius:12px;color:#fff!important;text-decoration:none!important;font-weight:700;font-size:15.5px;line-height:1.2}" +
    ".ttj-btn svg{width:20px;height:20px;flex:none}" +
    ".ttj-btn+.ttj-btn{margin-top:10px}.ttj-wa{background:#25D366}.ttj-tg{background:#229ED9}.ttj-btn:hover{filter:brightness(1.06)}" +
    ".ttj-foot{margin-top:12px;font-size:11.5px;color:#6b7a8c;text-align:center}" +
    "body.ttj-open .sp2{display:none!important}" +
    "@media (max-width:640px){.ttj{width:calc(100vw - 24px);border-radius:18px}.ttj-body{padding:16px 18px 18px}.ttj h3{font-size:18px}" +
    "}";

  var card = null, bd = null, cssDone = false, opened = false;

  function ensureCss() {
    if (cssDone) return; cssDone = true;
    var st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st);
  }

  function build() {
    card = document.createElement("div");
    card.className = "ttj"; card.setAttribute("role", "dialog"); card.setAttribute("aria-label", "Talk directly to Jono Armstrong");
    card.innerHTML =
      '<button class="ttj-x" type="button" aria-label="Close">&times;</button>' +
      '<a class="ttj-vid" href="https://www.youtube.com/watch?v=' + VIDEO + '" aria-label="Play: a message from Jono Armstrong">' +
        '<img src="/img/jono-poster.webp" width="800" height="450" alt="Jono Armstrong — video message" decoding="async">' +
        '<span class="ttj-play"></span><span class="ttj-cap"><span>&#9654; A short message from Jono</span><span>Watch</span></span></a>' +
      '<div class="ttj-body">' +
        '<div class="ttj-who"><div class="ttj-av" aria-hidden="true"></div><div><b>Jono Armstrong</b><span>Founder, Prometheus AI &middot; <em>&#9679; online</em></span></div></div>' +
        '<h3>Want to talk to me directly?</h3>' +
        '<p>Not sure if Prometheus is right for you? Message me &mdash; I answer personally, no sales team.</p>' +
        '<a class="ttj-btn ttj-wa" href="' + WA_URL + '" target="_blank" rel="noopener">' + ICON_WA + 'Chat on WhatsApp</a>' +
        '<a class="ttj-btn ttj-tg" href="' + TG + '" target="_blank" rel="noopener">' + ICON_TG + 'Message on Telegram</a>' +
        '<div class="ttj-foot">Usually replies within minutes &middot; no obligation</div>' +
      '</div>';
    bd = document.createElement("div"); bd.className = "ttj-bd"; bd.addEventListener("click", function () { close(true); });
    document.body.appendChild(bd);
    document.body.appendChild(card);

    card.querySelector(".ttj-x").addEventListener("click", function () { close(true); });
    card.querySelector(".ttj-vid").addEventListener("click", function (e) {
      e.preventDefault();
      var box = this;
      var f = document.createElement("iframe");
      f.src = "https://www.youtube-nocookie.com/embed/" + VIDEO + "?autoplay=1&rel=0&playsinline=1";
      f.title = "A message from Jono Armstrong";
      f.allow = "accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture";
      f.allowFullscreen = true;
      box.innerHTML = ""; box.appendChild(f); box.style.cursor = "default";
      track("jono_video_play");
    });
    card.querySelector(".ttj-wa").addEventListener("click", function () { track("jono_whatsapp_click"); });
    card.querySelector(".ttj-tg").addEventListener("click", function () { track("jono_telegram_click"); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && card && card.classList.contains("on")) close(true); });
  }

  function open(auto) {
    ensureCss();
    if (!card) build();
    document.body.classList.add("ttj-open");
    card.style.display = ""; bd.style.display = "";
    requestAnimationFrame(function () { card.classList.add("on"); bd.classList.add("on"); });
    ssSet(SS.shown, "1"); opened = true;
    track("jono_popup_open", { trigger: auto || "manual" });
  }

  function close(byUser) {
    if (!card) return;
    card.classList.remove("on"); if (bd) bd.classList.remove("on");
    document.body.classList.remove("ttj-open");
    setTimeout(function () { card.style.display = "none"; if (bd) bd.style.display = "none"; }, 350);
    if (byUser) track("jono_popup_close");
  }


  // ---- scheduling -------------------------------------------------------
  if (ssGet(SS.shown) === "1") return; // already shown during this visit

  var armed = false, fired = false, t0 = Date.now();
  function fire(why) {
    if (fired) return; fired = true;
    window.removeEventListener("scroll", onScroll);
    open(why);
  }
  function onScroll() {
    if ((window.pageYOffset || document.documentElement.scrollTop) < SCROLL_PX) return;
    if (!armed) { // scrolled before 5 s: wait for the minimum delay, then show
      setTimeout(function () { fire("scroll"); }, Math.max(0, MIN_DELAY - (Date.now() - t0)));
      window.removeEventListener("scroll", onScroll);
      return;
    }
    fire("scroll");
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  setTimeout(function () { armed = true; }, MIN_DELAY);
  setTimeout(function () { fire("timer"); }, MAX_DELAY);
})();
