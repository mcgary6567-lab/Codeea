// Build step on Vercel: make the demo pump database once, so cold starts only copy a file.
import fs from "node:fs";
import path from "node:path";
const out = path.resolve("data-demo/pumpai.db");
fs.mkdirSync(path.dirname(out), { recursive: true });
for (const f of [out, `${out}-wal`, `${out}-shm`]) fs.rmSync(f, { force: true });
process.env.DB_PATH = out;
process.env.NODE_ENV = "test"; // load modules without starting the server
const { seed } = await import("../server/dist/seed.js");
seed();
const { db } = await import("../server/dist/db.js");
db.exec("PRAGMA wal_checkpoint(TRUNCATE); PRAGMA journal_mode = DELETE;");
db.close();
console.log(`[vercel] demo database ready: ${(fs.statSync(out).size / 1e6).toFixed(1)} MB`);
