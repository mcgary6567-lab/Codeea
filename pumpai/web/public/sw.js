// PumpAI service worker: keeps the app shell available so the POS opens without internet.
// API calls are never cached here (the POS keeps its own copy of today's prices and queues sales).
const CACHE = "pumpai-shell-v3";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  // pages made by the server (customer khata, bills, receipts, reports, slips, legal) are never stored as the app shell
  const SERVER_PAGES = /^\/(api|webhooks|bill|w|k|r|day|portal|board|slip|privacy|terms|branding|manifest|version)(\/|\.|$)/;
  if (req.method !== "GET" || url.origin !== location.origin || SERVER_PAGES.test(url.pathname)) return;
  if (req.mode === "navigate") {
    // network first so updates arrive; fall back to the cached shell when offline
    e.respondWith(fetch(req).then((res) => { const copy = res.clone(); caches.open(CACHE).then((c) => c.put("/index.html", copy)); return res; })
      .catch(() => caches.match("/index.html")));
    return;
  }
  // hashed build assets: cache first
  e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
    return res;
  })));
});

// push notifications from the server (price change, cash short, licence expiry…)
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: e.data ? e.data.text() : "PumpAI" }; }
  e.waitUntil(self.registration.showNotification(d.title || "PumpAI", { body: d.body || "", tag: d.tag, data: { url: d.url || "/" } }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((ws) => {
    for (const w of ws) if ("focus" in w) { w.navigate(url); return w.focus(); }
    return self.clients.openWindow(url);
  }));
});
