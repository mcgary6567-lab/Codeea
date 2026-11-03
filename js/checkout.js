/* Checkout page — copy wallet addresses (front-end only) */
(function () {
  "use strict";

  function flash(btn, label) {
    if (btn.getAttribute("data-html") === null) btn.setAttribute("data-html", btn.innerHTML);
    btn.textContent = label;
    setTimeout(function () { btn.innerHTML = btn.getAttribute("data-html"); }, 1500);
  }

  function copyText(text, btn) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { flash(btn, "✓ Copied!"); },
        function () { legacyCopy(text, btn); }
      );
    } else {
      legacyCopy(text, btn);
    }
  }

  function legacyCopy(text, btn) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); flash(btn, "✓ Copied!"); }
    catch (e) { flash(btn, "Copy failed"); }
    document.body.removeChild(ta);
  }

  document.querySelectorAll(".wallet__copy").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var target = document.getElementById(btn.getAttribute("data-copy"));
      if (target) copyText(target.textContent.trim(), btn);
    });
  });

  /* Crypto-savings popup — shows once per session after a short delay */
  (function () {
    var overlay = document.getElementById("cryptoPop");
    if (!overlay) return;
    var closeBtn = document.getElementById("cpopClose");
    var cta = document.getElementById("cpopCta");
    var KEY = "cryptoPopShownAt";
    var DAY = 24 * 60 * 60 * 1000;
    var opened = false, armed = true;

    // Frequency cap: at most once per 24 hours per device
    try {
      var last = parseInt(localStorage.getItem(KEY), 10);
      if (last && (Date.now() - last) < DAY) armed = false;
    } catch (e) {}

    function openPop() {
      if (opened || !armed) return;
      opened = true; armed = false;
      try { localStorage.setItem(KEY, String(Date.now())); } catch (e) {}
      overlay.hidden = false;
      requestAnimationFrame(function () { overlay.classList.add("is-open"); });
    }
    function closePop() {
      overlay.classList.remove("is-open");
      setTimeout(function () { overlay.hidden = true; }, 300);
    }

    // Desktop: exit-intent only — never interrupt an active buyer
    document.addEventListener("mouseout", function (e) {
      if (!e.relatedTarget && e.clientY <= 0) openPop();
    });
    // Touch devices: trigger when scrolling back up near the top (a leaving signal)
    var touch = ("ontouchstart" in window) || (window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
    if (touch) {
      var lastY = window.scrollY;
      window.addEventListener("scroll", function () {
        var y = window.scrollY;
        if (y < lastY - 40 && y < 220) openPop();
        lastY = y;
      }, { passive: true });
    }

    if (closeBtn) closeBtn.addEventListener("click", closePop);
    if (cta) cta.addEventListener("click", closePop);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) closePop(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && overlay.classList.contains("is-open")) closePop();
    });
  })();

})();
