/**
 * HTTP safety for a real install: browser security headers and simple per-IP rate limits on the doors anyone can knock on
 * (sign-in, PIN, setup, customer portal). In-memory is enough: one process serves one pump.
 */
import type { Request, Response, NextFunction, RequestHandler } from "express";
import { isProduction } from "./config.js";

const CSP = [
  "default-src 'self'",
  "img-src 'self' data: blob: https://www.google.com https://*.gstatic.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' data: https://fonts.gstatic.com",
  "script-src 'self' 'unsafe-inline'",
  "connect-src 'self'",
  "frame-src 'self' https://www.google.com https://maps.google.com",
  "media-src 'self' blob:",
  "worker-src 'self'",
  "frame-ancestors 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

export function securityHeaders(req: Request, res: Response, next: NextFunction) {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "SAMEORIGIN");
  res.setHeader("Referrer-Policy", "same-origin");
  res.setHeader("Permissions-Policy", "camera=(self), microphone=(self), geolocation=(self), payment=()");
  res.setHeader("Content-Security-Policy", CSP);
  if (isProduction && req.secure) res.setHeader("Strict-Transport-Security", "max-age=31536000");
  next();
}

/** At most `max` requests per `windowMs` from one IP (per route group). */
export function rateLimit(name: string, max: number, windowMs: number): RequestHandler {
  const hits = new Map<string, { n: number; until: number }>();
  setInterval(() => { const now = Date.now(); for (const [k, v] of hits) if (v.until < now) hits.delete(k); }, windowMs).unref();
  return (req, res, next) => {
    if (process.env.NODE_ENV === "test") return next();
    const key = `${name}:${req.ip}`;
    const now = Date.now();
    const h = hits.get(key);
    if (!h || h.until < now) { hits.set(key, { n: 1, until: now + windowMs }); return next(); }
    if (++h.n > max) {
      res.setHeader("Retry-After", String(Math.ceil((h.until - now) / 1000)));
      return res.status(429).json({ error: "Too many tries from this device. Wait a few minutes and try again." });
    }
    next();
  };
}
