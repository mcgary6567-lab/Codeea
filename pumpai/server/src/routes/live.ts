/**
 * Live data feed (SSE): every successful write on this tenant's API (any POST / PATCH / PUT / DELETE) is announced here as a
 * `changed` event, so every open screen — this tab, another tab, the owner's phone — refreshes its lists and totals at once
 * without anyone pressing reload. The event carries only "something changed" (method, path, who), never the data itself.
 */
import { Router, type Request, type Response, type NextFunction } from "express";
import { bus } from "../whatsapp/cloud.js";
import { tid } from "../auth.js";

export const live = Router();

/** Announce a finished write. Mounted after requireAuth so req.user / tenant are known. */
export function announceWrites(req: Request, res: Response, next: NextFunction) {
  if (req.method !== "GET" && req.method !== "HEAD" && req.method !== "OPTIONS") {
    res.on("finish", () => {
      if (res.statusCode >= 400 || !req.user) return;
      try { bus.emit("event", { tenant_id: tid(req), type: "changed", method: req.method, path: req.path, client: req.get("x-client-id") ?? null, at: Date.now() }); } catch { /* never break a write */ }
    });
  }
  next();
}

live.get("/live", (req, res) => {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
  res.write("retry: 3000\n\n");
  const t = tid(req);
  const onEvent = (e: { tenant_id: number; type?: string }) => { if (e.tenant_id === t && e.type === "changed") res.write(`data: ${JSON.stringify(e)}\n\n`); };
  bus.on("event", onEvent);
  const ping = setInterval(() => res.write(": ping\n\n"), 25_000);
  req.on("close", () => { bus.off("event", onEvent); clearInterval(ping); });
});
