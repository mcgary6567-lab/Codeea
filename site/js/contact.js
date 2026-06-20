/* Contact page — front-end only (no real message delivery) */
(function () {
  "use strict";
  var form = document.getElementById("contactForm");
  var success = document.getElementById("contactSuccess");
  if (!form) return;
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var name = form.cname.value.trim();
    var email = form.cemail.value.trim();
    var msg = form.cmsg.value.trim();
    if (!name || !msg || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      alert("Please fill in your name, a valid email, and a message.");
      return;
    }
    // TODO: connect to a real form handler (Formspree, your backend, email API, etc.)
    form.hidden = true;
    var aside = form.parentNode.querySelector(".checkout__summary");
    if (aside) aside.hidden = true;
    if (success) {
      success.hidden = false;
      success.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });
})();
