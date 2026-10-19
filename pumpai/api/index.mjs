// Vercel serverless entry: the whole PumpAI API (Express) as one function.
// Vercel has no permanent disk, so this is a DEMO deployment: each instance copies the demo database
// (made at build time) to /tmp, and changes are lost when the instance is recycled.
// For a real pump use deploy/install.sh or Docker (see INSTALL.md).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
process.env.DB_PATH ??= "/tmp/pumpai.db";
process.env.BACKUP_DIR ??= "/tmp/pumpai-backups";
process.env.SCHEDULER = "off";
const demo = path.join(here, "..", "data-demo", "pumpai.db");
if (!fs.existsSync(process.env.DB_PATH) && fs.existsSync(demo)) fs.copyFileSync(demo, process.env.DB_PATH);

const { app } = await import("../server/dist/index.js");
export default app;
