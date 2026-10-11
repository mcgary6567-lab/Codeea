import { createApp } from "./app.js";
import { config } from "./config.js";
import { migrate } from "./db/migrate.js";
import { pool } from "./db/pool.js";
import { runIngest } from "./ingest/run.js";
import { log } from "./log.js";

await migrate((m) => log.info(m));

const server = createApp().listen(config.PORT, () => {
  log.info(`${config.APP_NAME} API listening on :${config.PORT}`);
});

let timer: NodeJS.Timeout | undefined;
if (config.INGEST_INTERVAL_MINUTES > 0) {
  let running = false;
  const tick = async () => {
    if (running) return;
    running = true;
    try {
      await runIngest((m) => log.info({ ingest: true }, m));
    } catch (err) {
      log.error({ err }, "ingest failed");
    } finally {
      running = false;
    }
  };
  timer = setInterval(tick, config.INGEST_INTERVAL_MINUTES * 60_000);
  void tick();
}

const shutdown = () => {
  clearInterval(timer);
  server.close(() => void pool.end().then(() => process.exit(0)));
};
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
