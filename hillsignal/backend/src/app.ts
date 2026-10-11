import cors from "cors";
import express from "express";
import helmet from "helmet";
import { authenticate } from "./auth.js";
import { config } from "./config.js";
import { pool } from "./db/pool.js";
import { errorHandler, HttpError } from "./http.js";
import { aiRouter } from "./routes/ai.js";
import { authRouter } from "./routes/auth.js";
import { billingRouter } from "./routes/billing.js";
import { dataRouter } from "./routes/data.js";
import { watchlistRouter } from "./routes/watchlist.js";

export function createApp() {
  const app = express();
  app.disable("x-powered-by");
  app.set("trust proxy", 1); // behind one load balancer (Render/Fly/Cloud Run)
  app.use(helmet());
  app.use(cors({ origin: config.CORS_ORIGINS === "*" ? true : config.CORS_ORIGINS.split(",") }));
  app.use(express.json({ limit: "100kb" }));

  app.get("/health", async (_req, res) => {
    await pool.query("SELECT 1");
    res.json({ ok: true, name: config.APP_NAME });
  });

  const v1 = express.Router();
  v1.use(authenticate);
  v1.use(authRouter, dataRouter, watchlistRouter, aiRouter, billingRouter);
  v1.use((_req, _res, next) => next(new HttpError(404, "not_found", "No such endpoint")));
  app.use("/api/v1", v1);

  app.use(errorHandler);
  return app;
}
