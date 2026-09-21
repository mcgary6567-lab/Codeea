/* Sale social-proof popup — self-contained, no dependencies.
 * Shows a bottom-left "<Name> from <Country> just purchased!" card that rotates.
 *
 * Data source (in order):
 *   1) Live real sales from the app's public endpoint (data-endpoint), if reachable.
 *   2) The built-in starter list below, so the popup always shows something.
 *
 * Usage (add before </body>):
 *   <script src="js/social-proof.js"
 *           data-endpoint="https://trevolto-bot.fly.dev/api/social_proof"
 *           data-logo="assets/img-01.png"></script>
 */
(function () {
  "use strict";
  var cur = document.currentScript || (function () {
    var s = document.getElementsByTagName("script"); return s[s.length - 1];
  })();
  var ENDPOINT = (cur && cur.getAttribute("data-endpoint")) || "";
  var LOGO = (cur && cur.getAttribute("data-logo")) || "assets/favicon.png";

  // --- starter list (used until real sales load / if endpoint is unreachable) ---
  var SEED = [
    { name: "Richard", country: "United States", ago: 442800 },
    { name: "Sophie", country: "United Kingdom", ago: 8400 },
    { name: "Daniel", country: "Canada", ago: 133200 },
    { name: "Mateo", country: "Spain", ago: 2700 },
    { name: "Aisha", country: "United Arab Emirates", ago: 61200 },
    { name: "Lukas", country: "Germany", ago: 349200 },
    { name: "Olivia", country: "Australia", ago: 19800 },
    { name: "James", country: "United States", ago: 705600 },
    { name: "Priya", country: "India", ago: 5400 },
    { name: "Noah", country: "Netherlands", ago: 226800 },
    { name: "Emma", country: "France", ago: 46800 },
    { name: "Carlos", country: "Mexico", ago: 111600 },
    { name: "Liam", country: "Ireland", ago: 900 },
    { name: "Chloe", country: "New Zealand", ago: 518400 }
  ];

  var css =
    ".sp2{position:fixed;left:20px;bottom:20px;z-index:2147483000;display:flex;align-items:center;gap:12px;" +
    "max-width:330px;background:#fff;color:#0f1622;border:1px solid #e6e9ee;border-radius:12px;padding:12px 14px;" +
    "box-shadow:0 14px 38px rgba(20,30,50,.18);font-family:inherit;transform:translateY(170%);opacity:0;" +
    "transition:transform .5s cubic-bezier(.2,.8,.2,1),opacity .5s;pointer-events:none}" +
    ".sp2.in{transform:translateY(0);opacity:1;pointer-events:auto}" +
    ".sp2-logo{width:42px;height:42px;border-radius:9px;object-fit:cover;flex:none;background:#f2f4f7}" +
    ".sp2-b{min-width:0}" +
    ".sp2-l{font-size:14px;font-weight:700;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
    ".sp2-l b{font-weight:800}" +
    ".sp2-s{font-size:12px;color:#6b7688;margin-top:2px;display:flex;align-items:center;gap:5px}" +
    ".sp2-tick{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;border-radius:50%;" +
    "background:#16a34a;color:#fff;font-size:9px;font-weight:900}" +
    ".sp2-x{position:absolute;top:7px;right:9px;border:none;background:none;color:#9aa4b2;font-size:16px;line-height:1;cursor:pointer;padding:2px}" +
    ".sp2-x:hover{color:#0f1622}" +
    ".sp2-ver{margin-left:auto;font-size:10px;color:#aab2bf;white-space:nowrap}" +
    "@media(max-width:520px){.sp2{left:12px;right:12px;bottom:12px;max-width:none}}";
  var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  var box = document.createElement("div");
  box.className = "sp2"; box.setAttribute("aria-live", "polite");
  var data = SEED.slice(), idx = 0, timer = null, dismissed = false, started = false;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function ago(sec) {
    sec = Math.max(0, sec | 0);
    if (sec < 120) return "just now";
    if (sec < 3600) return Math.floor(sec / 60) + " minutes ago";
    if (sec < 86400) { var h = Math.floor(sec / 3600); return h + (h === 1 ? " hour ago" : " hours ago"); }
    var d = Math.floor(sec / 86400); return d + (d === 1 ? " day ago" : " days ago");
  }
  function render() {
    if (dismissed || !data.length) return;
    var s = data[idx % data.length]; idx++;
    var loc = s.country ? " from " + esc(s.country) : "";
    box.innerHTML =
      '<img class="sp2-logo" src="' + esc(LOGO) + '" alt=""/>' +
      '<div class="sp2-b"><div class="sp2-l"><b>' + esc(s.name) + "</b>" + loc + "</div>" +
      '<div class="sp2-s"><span class="sp2-tick">✔</span> just purchased! · ' + esc(ago(s.ago)) + "</div></div>" +
      '<button class="sp2-x" aria-label="Close">×</button>';
    box.querySelector(".sp2-x").onclick = function () { dismissed = true; hide(); };
    requestAnimationFrame(function () { box.classList.add("in"); });
    timer = setTimeout(function () {
      box.classList.remove("in");                                   // visible ~6.5s, slide out
      timer = setTimeout(render, 16000 + Math.floor(Math.random() * 8000)); // 16–24s gap
    }, 6500);
  }
  function hide() { box.classList.remove("in"); if (timer) { clearTimeout(timer); timer = null; } }
  function start() { if (started) return; started = true; setTimeout(render, 2500); }

  function boot() {
    document.body.appendChild(box);
    if (ENDPOINT) {
      try {
        fetch(ENDPOINT, { mode: "cors" }).then(function (r) { return r.json(); }).then(function (d) {
          if (d && d.enabled && d.sales && d.sales.length) { data = d.sales; idx = 0; }
          start();
        }).catch(start);
      } catch (e) { start(); }
    } else { start(); }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
