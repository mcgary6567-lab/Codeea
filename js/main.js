/* Trevolto clone — interactivity */
(function () {
  "use strict";

  /* ---- Footer year ---- */
  var yearEl = document.getElementById("year");
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  /* ---- Countdown timer (resets to 24h, persists via localStorage) ---- */
  var DURATION = ((3 * 24) + 11) * 60 * 60 * 1000; // 3 days 11 hours
  var key = "trevolto_countdown_end_v2";
  var end = parseInt(localStorage.getItem(key), 10);
  var now = Date.now();
  if (!end || isNaN(end) || end < now || end - now > DURATION) {
    end = now + DURATION;
    localStorage.setItem(key, String(end));
  }

  var dEl = document.getElementById("cd-d");
  var hEl = document.getElementById("cd-h");
  var mEl = document.getElementById("cd-m");
  var sEl = document.getElementById("cd-s");
  // Second countdown (Get Trevolto Now buy box) — kept in sync
  var d2El = document.getElementById("cd2-d");
  var h2El = document.getElementById("cd2-h");
  var m2El = document.getElementById("cd2-m");
  var s2El = document.getElementById("cd2-s");

  function pad(n) { return n < 10 ? "0" + n : "" + n; }

  function set(el, val) { if (el) el.textContent = pad(val); }
  function setRaw(el, val) { if (el) el.textContent = "" + val; }

  function tick() {
    var diff = end - Date.now();
    if (diff <= 0) {
      // restart cycle
      end = Date.now() + DURATION;
      localStorage.setItem(key, String(end));
      diff = DURATION;
    }
    var totalSec = Math.floor(diff / 1000);
    var d = Math.floor(totalSec / 86400);
    var h = Math.floor((totalSec % 86400) / 3600);
    var m = Math.floor((totalSec % 3600) / 60);
    var s = totalSec % 60;
    setRaw(dEl, d); set(hEl, h); set(mEl, m); set(sEl, s);
    setRaw(d2El, d); set(h2El, h); set(m2El, m); set(s2El, s);
  }
  tick();
  setInterval(tick, 1000);

  /* ---- Reveal-on-scroll animation ---- */
  var revealTargets = document.querySelectorAll(
    ".card, .feature, .pricing-card, .testimonial, .guarantee-badge, .section__title"
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

  /* ---- Lightbox: click an image to expand ---- */
  (function () {
    var box = document.getElementById("lightbox");
    if (!box) return;
    var img = document.getElementById("lightboxImg");
    document.querySelectorAll(".lightbox-img").forEach(function (el) {
      el.addEventListener("click", function () {
        img.src = el.getAttribute("data-full") || el.src;
        box.hidden = false;
        document.body.style.overflow = "hidden";
      });
    });
    function close() { box.hidden = true; img.src = ""; document.body.style.overflow = ""; }
    box.addEventListener("click", close);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !box.hidden) close(); });
  })();

  /* ---- Demo download: email gate + OS chooser popup ---- */
  (function () {
    var pop = document.getElementById("demoPop");
    if (!pop) return;
    var closeBtn = document.getElementById("demoPopClose");
    var gate = document.getElementById("demoGate");
    var emailInput = document.getElementById("demoEmail");
    var gateErr = document.getElementById("demoGateErr");
    var downloads = document.getElementById("demoDownloads");

    /* ===== EMAIL SETUP =====
       Create a free form at https://formspree.io (or Brevo/Kit) and paste its
       endpoint below, replacing YOUR_FORM_ID. Until then the gate still works —
       it just won't store the email yet. */
    var FORMSPREE_ENDPOINT = "https://formspree.io/f/xbdekwrb";
    var EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
    var GIVEN_KEY = "demoEmailGiven";

    function showDownloads() {
      if (gate) gate.hidden = true;
      if (downloads) downloads.hidden = false;
    }

    function open() {
      // Returning visitors who already gave their email skip straight to downloads
      try { if (localStorage.getItem(GIVEN_KEY) === "1") showDownloads(); } catch (e) {}
      pop.hidden = false;
      requestAnimationFrame(function () { pop.classList.add("is-open"); });
      document.body.style.overflow = "hidden";
      if (gate && !gate.hidden && emailInput) setTimeout(function () { emailInput.focus(); }, 60);
    }
    function close() {
      pop.classList.remove("is-open");
      document.body.style.overflow = "";
      setTimeout(function () { pop.hidden = true; }, 250);
    }

    // Any element with .js-demo-download opens the popup instead of navigating
    document.querySelectorAll(".js-demo-download").forEach(function (trigger) {
      trigger.addEventListener("click", function (e) {
        e.preventDefault();
        open();
      });
    });

    // Email gate → reveal downloads + send the lead (fire-and-forget; never blocks)
    if (gate) {
      gate.addEventListener("submit", function (e) {
        e.preventDefault();
        var email = (emailInput.value || "").trim();
        if (!EMAIL_RE.test(email)) {
          if (gateErr) gateErr.hidden = false;
          emailInput.focus();
          return;
        }
        if (gateErr) gateErr.hidden = true;
        try { localStorage.setItem(GIVEN_KEY, "1"); } catch (e2) {}

        if (FORMSPREE_ENDPOINT.indexOf("YOUR_FORM_ID") === -1 && window.fetch) {
          try {
            fetch(FORMSPREE_ENDPOINT, {
              method: "POST",
              headers: { "Accept": "application/json", "Content-Type": "application/json" },
              body: JSON.stringify({ email: email, source: "demo-download", _subject: "New Trevolto demo download" })
            });
          } catch (e3) {}
        }
        showDownloads();
      });
    }

    if (closeBtn) closeBtn.addEventListener("click", close);
    pop.addEventListener("click", function (e) { if (e.target === pop) close(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && pop.classList.contains("is-open")) close();
    });
  })();

  /* ---- Sticky desktop buy-bar: show after hero, hide near footer ---- */
  (function () {
    var bar = document.getElementById("buybar");
    if (!bar) return;
    var hero = document.querySelector(".hero");
    var footer = document.querySelector(".site-footer");
    function onScroll() {
      var past = window.scrollY > (hero ? hero.offsetHeight * 0.9 : 600);
      var nearFooter = footer && (footer.getBoundingClientRect().top < window.innerHeight + 40);
      if (past && !nearFooter) {
        bar.classList.add("is-visible");
        bar.setAttribute("aria-hidden", "false");
      } else {
        bar.classList.remove("is-visible");
        bar.setAttribute("aria-hidden", "true");
      }
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    onScroll();
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
