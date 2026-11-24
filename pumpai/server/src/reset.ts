/** `npm run seed`: wipe the database and reload demo data. */
import fs from "node:fs";
import { config } from "./config.js";

for (const f of [config.dbPath, config.dbPath + "-wal", config.dbPath + "-shm"]) fs.rmSync(f, { force: true });
const { seed } = await import("./seed.js");
seed();
