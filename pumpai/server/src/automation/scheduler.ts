import cron from "node-cron";
import { all, get, run, now } from "../db.js";
import { config } from "../config.js";
import { JOBS } from "./jobs.js";

export function ensureAutomations(tenantId: number) {
  for (const j of JOBS) {
    run(
      `INSERT INTO automations (tenant_id,key,name,description,cron,enabled) VALUES (?,?,?,?,?,1)
       ON CONFLICT(tenant_id,key) DO UPDATE SET name=excluded.name, description=excluded.description`,
      tenantId, j.key, j.name, j.description, j.cron,
    );
  }
}

const running = new Set<string>();

export async function runJob(tenantId: number, key: string): Promise<string> {
  const job = JOBS.find((j) => j.key === key);
  if (!job) throw new Error(`Unknown job ${key}`);
  const lock = `${tenantId}:${key}`;
  if (running.has(lock)) return "Already running";
  running.add(lock);
  let result: string;
  try {
    result = await job.run(tenantId);
  } catch (e: any) {
    result = `Error: ${e.message}`;
    console.error(`[automation] ${key} failed`, e);
  } finally {
    running.delete(lock);
  }
  run("UPDATE automations SET last_run_at=?, last_result=? WHERE tenant_id=? AND key=?", now(), result, tenantId, key);
  return result;
}

export function startScheduler() {
  if (!config.schedulerEnabled) return;
  for (const t of all("SELECT id FROM tenants")) ensureAutomations(t.id);
  for (const job of JOBS) {
    cron.schedule(job.cron, async () => {
      for (const t of all("SELECT id FROM tenants")) {
        const a = get("SELECT enabled, cron FROM automations WHERE tenant_id=? AND key=?", t.id, job.key);
        if (a?.enabled) await runJob(t.id, job.key);
      }
    }, { timezone: config.timezone });
  }
  console.log(`[automation] ${JOBS.length} jobs scheduled (${config.timezone})`);
}
