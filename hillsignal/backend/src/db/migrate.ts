import { pathToFileURL } from "node:url";
import { pool, withTransaction } from "./pool.js";
import { migrations } from "./migrations.js";

export async function migrate(log: (msg: string) => void = console.log): Promise<void> {
  await pool.query(
    "CREATE TABLE IF NOT EXISTS schema_migrations (id TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())",
  );
  const done = new Set(
    (await pool.query<{ id: string }>("SELECT id FROM schema_migrations")).rows.map((r) => r.id),
  );
  for (const m of migrations) {
    if (done.has(m.id)) continue;
    await withTransaction(async (c) => {
      await c.query(m.sql);
      await c.query("INSERT INTO schema_migrations (id) VALUES ($1)", [m.id]);
    });
    log(`applied ${m.id}`);
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  migrate()
    .then(() => pool.end())
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
}
