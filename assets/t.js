/* goldscalpers.com first-party visit counter - no cookies, no third parties.
 * Sends one page view, the clicks that matter for sales (buy buttons, checkout,
 * MQL5, download) and the time spent on the page to /t.php. The id below lives
 * in sessionStorage only, so it is gone when the tab closes. Read the numbers
 * in the panel: /panel/stats.php. */
(function () {
  "use strict";
  if (navigator.webdriver) return;                      // headless / automated browsers
  var END = "/t.php";
  var sid = "";
  try {
    sid = sessionStorage.getItem("gs_sid") || "";
    if (!sid) { sid = Math.random().toString(36).slice(2, 12); sessionStorage.setItem("gs_sid", sid); }
  } catch (e) { sid = "nosess"; }

  function send(o) {
    o.sid = sid; o.p = location.pathname;
    var body = JSON.stringify(o);
    try {
      if (navigator.sendBeacon && navigator.sendBeacon(END, new Blob([body], { type: "text/plain" }))) return;
    } catch (e) {}
    try { fetch(END, { method: "POST", body: body, keepalive: true, headers: { "Content-Type": "text/plain" } }); } catch (e) {}
  }

  var q = new URLSearchParams(location.search);
  send({ e: "pv", r: document.referrer || "", us: q.get("utm_source") || q.get("ref") || "",
         uc: q.get("utm_campaign") || "", w: screen.width || 0 });

  // Which clicks count, by where the link goes.
  function kind(href) {
    if (!href) return "";
    if (/gumroad\.com/i.test(href)) return "buy_gumroad";
    if (/paypal\.(com|me)/i.test(href)) return "buy_paypal";
    if (/nowpayments|crypto/i.test(href)) return "buy_crypto";
    if (/mql5\.com\/.*product\/191087/i.test(href)) return "mql5_pro";
    if (/mql5\.com\/.*product\/184515/i.test(href)) return "mql5_free";
    if (/\/downloads\//i.test(href)) return "download_zip";
    if (/^(https?:\/\/goldscalpers\.com)?\/checkout(\.html)?([?#].*)?$/i.test(href)) return "go_checkout";
    if (/^mailto:/i.test(href)) return "email";
    return "";
  }
  document.addEventListener("click", function (ev) {
    var el = ev.target && ev.target.closest ? ev.target.closest("a,button") : null;
    if (!el) return;
    var k = el.getAttribute("data-track") || "";
    if (!k && el.tagName === "A") k = kind(el.getAttribute("href") || "");
    if (!k && el.tagName === "BUTTON" && /copyAddr/.test(el.getAttribute("onclick") || "")) k = "crypto_copy_address";
    if (k) send({ e: k, h: (el.getAttribute("href") || "").slice(0, 160) });
  }, true);

  // Time actually spent looking at the page, and how far down it was read.
  var shown = document.visibilityState === "visible" ? Date.now() : 0, spent = 0, depth = 0, done = false;
  function scrollDepth() {
    var h = document.documentElement.scrollHeight - innerHeight;
    var d = h > 0 ? Math.round(100 * scrollY / h) : 100;
    if (d > depth) depth = Math.min(100, d);
  }
  addEventListener("scroll", scrollDepth, { passive: true });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") shown = Date.now();
    else { if (shown) spent += Date.now() - shown; shown = 0; leave(); }
  });
  addEventListener("pagehide", leave);
  function leave() {
    if (done) return;
    if (shown) { spent += Date.now() - shown; shown = Date.now(); }
    scrollDepth();
    done = true;
    send({ e: "leave", ms: spent, sd: depth });
  }
})();
