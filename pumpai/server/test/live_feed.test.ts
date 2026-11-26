/** The live data feed: GET /live streams a `changed` event after every successful write on the tenant, nothing on a failed one, and needs a token. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import type { Server } from "node:http";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pumpai-live-"));
process.env.DB_PATH = path.join(dir, "test.db");
process.env.NODE_ENV = "test";
process.env.ANTHROPIC_API_KEY = "";
process.env.WA_TOKEN = "";

let server: Server, base = "", tok = "", media = "";
async function call(method: string, url: string, body?: unknown, headers: Record<string, string> = {}) {
  const res = await fetch(base + url, { method, headers: { "content-type": "application/json", ...(tok ? { authorization: `Bearer ${tok}` } : {}), ...headers }, body: body ? JSON.stringify(body) : undefined });
  return { status: res.status, data: (await res.json().catch(() => null)) as any };
}

/** Open the SSE stream and collect `data:` lines for `ms` milliseconds. */
async function listen(ms: number, run: () => Promise<void>, query = `?token=${media}`) {
  const ac = new AbortController();
  const res = await fetch(`${base}/api/live${query}`, { signal: ac.signal });
  assert.equal(res.status, 200, "stream opens");
  assert.match(res.headers.get("content-type") ?? "", /text\/event-stream/);
  const events: any[] = [];
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  const pump = (async () => {
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i: number;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          for (const line of chunk.split("\n")) if (line.startsWith("data: ")) events.push(JSON.parse(line.slice(6)));
        }
      }
    } catch { /* aborted */ }
  })();
  await new Promise((r) => setTimeout(r, 150)); // let the subscription attach
  await run();
  await new Promise((r) => setTimeout(r, ms));
  ac.abort(); await pump;
  return events;
}

before(async () => {
  const { app } = await import("../src/index.js");
  await new Promise<void>((r) => { server = app.listen(0, () => r()); });
  base = `http://127.0.0.1:${(server.address() as any).port}`;
  tok = (await call("POST", "/api/auth/login", { email: "admin@pumpai.pk", password: "demo1234" })).data.token;
  const me = (await call("GET", "/api/me")).data;
  media = me.media_token ?? me.user?.media_token;
  assert.ok(media, "media token issued");
});
after(() => { server.close(); fs.rmSync(dir, { recursive: true, force: true }); });

test("a successful write is announced on /live with method, path and the writer's client id", async () => {
  const events = await listen(400, async () => {
    const r = await call("POST", "/api/other-entries", { kind: "expense", amount: 50, method: "cash", party_type: "other", party_name: "Live test", reason: "feed test" }, { "x-client-id": "tab-A" });
    assert.equal(r.status, 200, JSON.stringify(r.data));
  });
  const ev = events.find((e) => e.path === "/other-entries");
  assert.ok(ev, `changed event expected, got ${JSON.stringify(events)}`);
  assert.equal(ev.type, "changed"); assert.equal(ev.method, "POST"); assert.equal(ev.client, "tab-A"); assert.equal(ev.tenant_id, 1);
  assert.ok(typeof ev.at === "number");
});

test("a failed write (400) and plain reads announce nothing", async () => {
  const events = await listen(400, async () => {
    assert.equal((await call("POST", "/api/other-entries", { kind: "expense", amount: 50 })).status, 400);
    assert.equal((await call("GET", "/api/other-entries")).status, 200);
    assert.equal((await call("GET", "/api/books/check")).status, 200);
  });
  assert.deepEqual(events, []);
});

test("the feed needs a token — a full session token in the URL or no token is refused", async () => {
  assert.equal((await fetch(`${base}/api/live`)).status, 401);
  assert.equal((await fetch(`${base}/api/live?token=${tok}`)).status, 401, "a long-lived session token must not live in a URL");
});
