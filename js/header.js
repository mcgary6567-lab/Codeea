/* Shared site header behaviour: mobile menu, homepage section links, "Talk to Jono" button. */
(function () {
  var h = document.getElementById("pm-header"); if (!h) return;
  var mb = h.querySelector(".pmh__menu");
  function close() { h.classList.remove("open"); if (mb) mb.setAttribute("aria-expanded", "false"); }
  if (mb) mb.addEventListener("click", function () { var o = h.classList.toggle("open"); mb.setAttribute("aria-expanded", o ? "true" : "false"); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });

  // Highlight the current page in the nav.
  var path = location.pathname.replace(/\.html$/, "").replace(/\/$/, "") || "/";
  h.querySelectorAll(".pmh__nav a, .pmh__drop a").forEach(function (a) {
    var p = a.getAttribute("href"); if (p && p.charAt(0) === "/" && p.replace(/\/$/, "") === path && path !== "/") a.setAttribute("aria-current", "page");
  });

  // Section links (How it works / Reviews / FAQ): scroll on the homepage, otherwise open /#section.
  var HASH = { how: "how-it-works", reviews: "reviews", faq: "faq" };
  function target(key) {
    return [].slice.call(document.querySelectorAll('[data-pm-anchor="' + key + '"]')).filter(function (x) { return x.offsetParent !== null; })[0];
  }
  // Smooth-scroll, then re-check a few times: lazy images above the section can
  // finish loading mid-scroll and push it down, so correct until it settles.
  function go(t) {
    var want = function () { return t.getBoundingClientRect().top + window.pageYOffset - h.offsetHeight - 8; };
    window.scrollTo({ top: want(), behavior: "smooth" });
    [700, 1300, 2000, 3000].forEach(function (ms) {
      setTimeout(function () { var d = t.getBoundingClientRect().top - h.offsetHeight - 8; if (Math.abs(d) > 6) window.scrollTo({ top: want(), behavior: "auto" }); }, ms);
    });
  }
  h.querySelectorAll("[data-pm-go]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      var t = target(a.getAttribute("data-pm-go")); close();
      if (t) { e.preventDefault(); go(t); if (history.replaceState) history.replaceState(null, "", "#" + HASH[a.getAttribute("data-pm-go")]); }
    });
  });
  var fromHash = { "#how-it-works": "how", "#reviews": "reviews", "#faq": "faq" }[location.hash];
  if (fromHash) window.addEventListener("load", function () { var t = target(fromHash); if (t) setTimeout(function () { go(t); }, 150); });

  // "Talk to Jono": open the popup when it is on the page, otherwise WhatsApp (the link's own href).
  h.querySelectorAll("[data-pm-jono]").forEach(function (a) {
    a.addEventListener("click", function (e) { if (typeof window.ttjOpen === "function") { e.preventDefault(); close(); window.ttjOpen("header"); } });
  });
})();
