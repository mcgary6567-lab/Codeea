/* Trevolto service worker — PWA + web push */
self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data.json(); } catch (x) { d = { body: e.data ? e.data.text() : "" }; }
  const title = d.title || "Trevolto";
  e.waitUntil(self.registration.showNotification(title, {
    body: d.body || "", icon: "/static/icon-192.png", badge: "/static/icon-192.png",
    tag: "trevolto-alert", renotify: true
  }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then(cl => {
    for (const c of cl) { if ("focus" in c) return c.focus(); }
    if (clients.openWindow) return clients.openWindow("/");
  }));
});
