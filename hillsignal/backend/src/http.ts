import type { NextFunction, Request, RequestHandler, Response } from "express";
import { z } from "zod";
import { log } from "./log.js";
import type { Plan } from "./plans.js";

export class HttpError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public extra: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export const notFound = (what = "Resource") => new HttpError(404, "not_found", `${what} not found`);
export const upgradeRequired = (requiredPlan: Plan, feature: string) =>
  new HttpError(402, "upgrade_required", `${feature} requires the ${requiredPlan} plan`, { requiredPlan });

// Express 4 doesn't forward rejected promises to the error handler.
export const handler =
  (fn: (req: Request, res: Response) => Promise<unknown>): RequestHandler =>
  (req, res, next) => {
    fn(req, res).catch(next);
  };

export function parse<S extends z.ZodType>(schema: S, value: unknown): z.infer<S> {
  const result = schema.safeParse(value);
  if (!result.success) {
    throw new HttpError(400, "invalid_request", z.prettifyError(result.error));
  }
  return result.data;
}

export function errorHandler(err: unknown, req: Request, res: Response, _next: NextFunction) {
  if (err instanceof HttpError) {
    res.status(err.status).json({ error: { code: err.code, message: err.message }, ...err.extra });
    return;
  }
  // body-parser errors (bad JSON, too large) carry a status.
  const status = (err as { status?: number }).status;
  if (status && status >= 400 && status < 500) {
    res.status(status).json({ error: { code: "invalid_request", message: (err as Error).message } });
    return;
  }
  log.error({ err, path: req.path }, "unhandled error");
  res.status(500).json({ error: { code: "internal", message: "Something went wrong" } });
}

export const pageQuery = {
  page: z.coerce.number().int().min(1).default(1),
  pageSize: z.coerce.number().int().min(1).max(100).default(25),
};
