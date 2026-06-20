/* Trevolto clone — interactivity */
(function () {
  "use strict";

  /* ---- Footer year ---- */
  var yearEl = document.getElementById("year");
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  /* ---- Countdown timer (resets to 24h, persists via localStorage) ---- */
  var DURATION = 24 * 60 * 60 * 1000; // 24 hours
  var key = "trevolto_countdown_end";
  var end = parseInt(localStorage.getItem(key), 10);
  var now = Date.now();
  if (!end || isNaN(end) || end < now) {
    end = now + DURATION;
    localStorage.setItem(key, String(end));
  }

  var hEl = document.getElementById("cd-h");
  var mEl = document.getElementById("cd-m");
  var sEl = document.getElementById("cd-s");
  // Second countdown (Get Trevolto Now buy box) — kept in sync
  var h2El = document.getElementById("cd2-h");
  var m2El = document.getElementById("cd2-m");
  var s2El = document.getElementById("cd2-s");

  function pad(n) { return n < 10 ? "0" + n : "" + n; }

  function set(el, val) { if (el) el.textContent = pad(val); }

  function tick() {
    var diff = end - Date.now();
    if (diff <= 0) {
      // restart cycle
      end = Date.now() + DURATION;
      localStorage.setItem(key, String(end));
      diff = DURATION;
    }
    var totalSec = Math.floor(diff / 1000);
    var h = Math.floor(totalSec / 3600);
    var m = Math.floor((totalSec % 3600) / 60);
    var s = totalSec % 60;
    set(hEl, h); set(mEl, m); set(sEl, s);
    set(h2El, h); set(m2El, m); set(s2El, s);
  }
  tick();
  setInterval(tick, 1000);

  /* ---- Reveal-on-scroll animation ---- */
  var revealTargets = document.querySelectorAll(
    ".card, .feature, .bonus, .pricing-card, .testimonial, .guarantee-badge, .section__title"
  );
  revealTargets.forEach(function (el) { el.classList.add("reveal"); });

  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });
    revealTargets.forEach(function (el) { io.observe(el); });
  } else {
    revealTargets.forEach(function (el) { el.classList.add("is-visible"); });
  }

  /* ---- YouTube facade: load embed on click ---- */
  document.querySelectorAll(".yt-facade").forEach(function (facade) {
    function loadVideo() {
      var id = facade.getAttribute("data-yt");
      if (!id) return;
      var iframe = document.createElement("iframe");
      iframe.src = "https://www.youtube-nocookie.com/embed/" + id + "?autoplay=1&rel=0";
      iframe.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture";
      iframe.allowFullscreen = true;
      iframe.title = "Trevolto platform video";
      facade.innerHTML = "";
      facade.appendChild(iframe);
    }
    facade.addEventListener("click", loadVideo);
    facade.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); loadVideo(); }
    });
  });

  /* ---- Promo popup (fires 20s after load, once per session) ---- */
  (function () {
    var overlay = document.getElementById("promoOverlay");
    if (!overlay) return;
    var closeBtn = document.getElementById("promoClose");
    var cta = document.getElementById("promoCta");
    var KEY = "promoShownAt";
    var DAY = 24 * 60 * 60 * 1000;
    var opened = false, armed = true;

    // Frequency cap: at most once per 24 hours per device
    try {
      var last = parseInt(localStorage.getItem(KEY), 10);
      if (last && (Date.now() - last) < DAY) armed = false;
    } catch (e) {}

    function openPromo() {
      if (opened || !armed) return;
      opened = true; armed = false;
      try { localStorage.setItem(KEY, String(Date.now())); } catch (e) {}
      overlay.hidden = false;
      requestAnimationFrame(function () { overlay.classList.add("is-open"); });
      document.body.style.overflow = "hidden";
    }

    function closePromo() {
      overlay.classList.remove("is-open");
      document.body.style.overflow = "";
      setTimeout(function () { overlay.hidden = true; }, 300);
    }

    // Desktop: exit-intent (mouse leaves toward the top of the viewport)
    document.addEventListener("mouseout", function (e) {
      if (!e.relatedTarget && e.clientY <= 0) openPromo();
    });
    // Touch devices (no mouse): fallback after 25s
    var touch = ("ontouchstart" in window) || (window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
    if (touch) setTimeout(openPromo, 25000);

    if (closeBtn) closeBtn.addEventListener("click", closePromo);
    if (cta) cta.addEventListener("click", closePromo);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) closePromo(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && overlay.classList.contains("is-open")) closePromo();
    });
  })();

  /* ---- Smooth-scroll for in-page anchors (fallback for older browsers) ---- */
  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener("click", function (e) {
      var id = this.getAttribute("href");
      if (id.length > 1) {
        var target = document.querySelector(id);
        if (target) {
          e.preventDefault();
          target.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      }
    });
  });
})();
