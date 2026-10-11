import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApp } from "../src/app.js";
import { consumeQuota } from "../src/ai/client.js";
import { setPlayApiForTests, type SubscriptionPurchaseV2 } from "../src/billing/googlePlay.js";
import { migrate } from "../src/db/migrate.js";
import { pool, query } from "../src/db/pool.js";
import { seed } from "../src/db/seed.js";
import { storeFiling } from "../src/ingest/run.js";

const app = createApp();
const api = request(app);

async function register(email: string) {
  const res = await api.post("/api/v1/auth/register").send({ email, password: "correct-horse-battery" });
  expect(res.status).toBe(201);
  return { token: res.body.token as string, userId: res.body.user.id as string };
}

const grant = (userId: string, plan: "pro" | "premium") =>
  query("UPDATE users SET granted_plan = $1 WHERE id = $2", [plan, userId]);

beforeAll(async () => {
  await migrate(() => {});
  await seed();
});

afterAll(async () => {
  await pool.end();
});

describe("auth", () => {
  it("registers, logs in, and returns the profile", async () => {
    await register("Ada@Example.com");
    const login = await api.post("/api/v1/auth/login").send({ email: "ada@example.com", password: "correct-horse-battery" });
    expect(login.status).toBe(200);
    const me = await api.get("/api/v1/me").set("Authorization", `Bearer ${login.body.token}`);
    expect(me.body.user).toMatchObject({ email: "ada@example.com", plan: "free" });
    expect(me.body.entitlements).toMatchObject({ plan: "free", delayDays: 3, aiChat: false });
  });

  it("rejects duplicates, bad passwords and bad tokens", async () => {
    expect((await api.post("/api/v1/auth/register").send({ email: "ada@example.com", password: "whatever-123" })).status).toBe(409);
    expect((await api.post("/api/v1/auth/login").send({ email: "ada@example.com", password: "nope-nope-nope" })).status).toBe(401);
    expect((await api.get("/api/v1/me").set("Authorization", "Bearer junk")).body.error.code).toBe("unauthorized");
    expect((await api.post("/api/v1/auth/register").send({ email: "x", password: "short" })).body.error.code).toBe("invalid_request");
  });
});

describe("trades visibility by plan", () => {
  it("hides recently disclosed trades from free users but not from Pro", async () => {
    const fresh = await storeFiling("import", "fresh-1", [
      {
        source: "import",
        filingId: "fresh-1",
        rowIndex: 0,
        member: { firstName: "Avery", lastName: "Lindqvist", chamber: "senate", state: "MN" },
        owner: "Self",
        assetName: "NVIDIA Corporation",
        ticker: "NVDA",
        assetType: "Stock",
        txType: "Purchase",
        txDate: new Date(Date.now() - 5 * 86_400_000).toISOString().slice(0, 10),
        pubDate: new Date(),
        amount: "$1,000,001 - $5,000,000",
        sourceUrl: "https://efdsearch.senate.gov/search/view/ptr/x/",
      },
    ]);
    const id = fresh.inserted[0]!;

    const anon = await api.get("/api/v1/trades").query({ ticker: "NVDA", pageSize: 100 });
    expect(anon.body.meta.delayDays).toBe(3);
    expect(anon.body.data.map((t: { id: string }) => t.id)).not.toContain(id);
    expect((await api.get(`/api/v1/trades/${id}`)).status).toBe(404);

    const { token, userId } = await register("pro@example.com");
    await grant(userId, "pro");
    const pro = await api.get("/api/v1/trades").query({ ticker: "NVDA", pageSize: 100 }).set("Authorization", `Bearer ${token}`);
    expect(pro.body.meta.delayDays).toBe(0);
    const trade = pro.body.data.find((t: { id: string }) => t.id === id);
    expect(trade).toMatchObject({
      txType: "buy",
      owner: "self",
      amountMin: 1000001,
      amountMax: 5000000,
      politician: { id: "sample-lindqvist", party: "D", chamber: "senate" },
      issuer: { ticker: "NVDA", sector: "Information Technology" },
    });
  });

  it("filters and sorts", async () => {
    const res = await api.get("/api/v1/trades").query({ chamber: "house", txType: "sell", sort: "-amount", pageSize: 50 });
    expect(res.status).toBe(200);
    expect(res.body.data.length).toBeGreaterThan(0);
    for (const t of res.body.data) {
      expect(t.politician.chamber).toBe("house");
      expect(t.txType).toBe("sell");
    }
    const mins = res.body.data.map((t: { amountMin: number }) => t.amountMin);
    expect([...mins].sort((a, b) => b - a)).toEqual(mins);
    expect((await api.get("/api/v1/trades").query({ pageSize: 1000 })).status).toBe(400);
  });

  it("serves politicians, issuers, overview and plans", async () => {
    const list = await api.get("/api/v1/politicians").query({ search: "lindqvst" });
    expect(list.body.data[0].id).toBe("sample-lindqvist");
    const detail = await api.get("/api/v1/politicians/sample-lindqvist");
    expect(detail.body.tradeCount).toBe(detail.body.buyCount + detail.body.sellCount + 0);
    expect(detail.body.topIssuers.length).toBeGreaterThan(0);
    const issuers = await api.get("/api/v1/issuers").query({ search: "NVDA" });
    expect(issuers.body.data[0].ticker).toBe("NVDA");
    expect((await api.get(`/api/v1/issuers/${issuers.body.data[0].id}`)).body.ticker).toBe("NVDA");
    const ov = await api.get("/api/v1/stats/overview");
    expect(ov.body.totalPoliticians).toBe(8);
    const plans = await api.get("/api/v1/plans");
    expect(plans.body.plans.map((p: { id: string }) => p.id)).toEqual(["free", "pro", "premium"]);
    expect(plans.body.plans[2].googleProductIds).toEqual(["hillsignal_premium"]);
  });
});

describe("gated features", () => {
  it("returns 402 with the required plan", async () => {
    const { token } = await register("free@example.com");
    const auth = { Authorization: `Bearer ${token}` };
    const insight = await api.get("/api/v1/ai/politicians/sample-lindqvist/insight").set(auth);
    expect(insight.status).toBe(402);
    expect(insight.body).toMatchObject({ error: { code: "upgrade_required" }, requiredPlan: "pro" });
    expect((await api.post("/api/v1/ai/chat").set(auth).send({ messages: [{ role: "user", content: "hi" }] })).body.requiredPlan).toBe("premium");
    expect((await api.get("/api/v1/trades/export.csv").set(auth)).status).toBe(402);
  });

  it("returns 503 for AI when no API key is configured", async () => {
    const { token } = await register("ai@example.com");
    const res = await api.post("/api/v1/ai/search").set("Authorization", `Bearer ${token}`).send({ query: "who sold nvidia" });
    expect(res.status).toBe(503);
    expect(res.body.error.code).toBe("ai_unavailable");
    // A failed AI call doesn't burn quota.
    const [row] = await query<{ count: number }>("SELECT count FROM ai_usage WHERE kind = 'search'");
    expect(row?.count ?? 0).toBe(0);
  });

  it("enforces the daily AI quota atomically", async () => {
    const { userId } = await register("quota@example.com");
    const results = await Promise.allSettled(Array.from({ length: 5 }, () => consumeQuota(userId, "search", 3)));
    expect(results.filter((r) => r.status === "fulfilled")).toHaveLength(3);
    expect(results.filter((r) => r.status === "rejected")).toHaveLength(2);
  });

  it("exports CSV for premium with formula injection neutralized", async () => {
    const { token, userId } = await register("csv@example.com");
    await grant(userId, "premium");
    const res = await api.get("/api/v1/trades/export.csv").query({ ticker: "AAPL" }).set("Authorization", `Bearer ${token}`);
    expect(res.status).toBe(200);
    expect(res.headers["content-type"]).toContain("text/csv");
    const lines = res.text.split("\n");
    expect(lines[0]).toMatch(/^tx_date,disclosed,politician/);
    expect(lines.length).toBeGreaterThan(1);
  });
});

describe("watchlist", () => {
  it("follows, lists, feeds, and enforces the free limit", async () => {
    const { token } = await register("watch@example.com");
    const auth = { Authorization: `Bearer ${token}` };
    for (const id of ["sample-lindqvist", "sample-okafor", "sample-castellanos"]) {
      expect((await api.post("/api/v1/watchlist").set(auth).send({ kind: "politician", targetId: id })).status).toBe(201);
    }
    // Re-following is idempotent, a 4th is over the free limit.
    expect((await api.post("/api/v1/watchlist").set(auth).send({ kind: "politician", targetId: "sample-okafor" })).status).toBe(201);
    const over = await api.post("/api/v1/watchlist").set(auth).send({ kind: "politician", targetId: "sample-whitfield" });
    expect(over.status).toBe(402);
    expect((await api.post("/api/v1/watchlist").set(auth).send({ kind: "politician", targetId: "nobody" })).status).toBe(404);

    const list = await api.get("/api/v1/watchlist").set(auth);
    expect(list.body.politicians).toHaveLength(3);
    const feed = await api.get("/api/v1/feed").set(auth);
    const watched = new Set(["sample-lindqvist", "sample-okafor", "sample-castellanos"]);
    expect(feed.body.data.length).toBeGreaterThan(0);
    for (const t of feed.body.data) expect(watched.has(t.politician.id)).toBe(true);

    expect((await api.delete("/api/v1/watchlist/politician/sample-okafor").set(auth)).status).toBe(204);
    expect((await api.get("/api/v1/watchlist").set(auth)).body.politicians).toHaveLength(2);

    expect((await api.post("/api/v1/devices").set(auth).send({ expoPushToken: "ExponentPushToken[abc123]", platform: "android" })).status).toBe(201);
    expect((await api.post("/api/v1/devices").set(auth).send({ expoPushToken: "garbage", platform: "android" })).status).toBe(400);
  });
});

describe("Google Play billing", () => {
  const future = new Date(Date.now() + 30 * 86_400_000).toISOString();
  const subs = new Map<string, SubscriptionPurchaseV2>();
  const acked: string[] = [];

  beforeAll(() => {
    setPlayApiForTests({
      getSubscription: async (token) => {
        const s = subs.get(token);
        if (!s) throw new Error("token not found");
        return s;
      },
      acknowledge: async (_productId, token) => {
        acked.push(token);
      },
    });
  });
  afterAll(() => setPlayApiForTests(null));

  it("verifies a purchase, upgrades the user, and acknowledges it", async () => {
    const { token, userId } = await register("buyer@example.com");
    subs.set("token-premium-1", {
      subscriptionState: "SUBSCRIPTION_STATE_ACTIVE",
      acknowledgementState: "ACKNOWLEDGEMENT_STATE_PENDING",
      externalAccountIdentifiers: { obfuscatedExternalAccountId: userId },
      lineItems: [{ productId: "hillsignal_premium", expiryTime: future, autoRenewingPlan: { autoRenewEnabled: true } }],
    });
    const res = await api
      .post("/api/v1/billing/google/verify")
      .set("Authorization", `Bearer ${token}`)
      .send({ productId: "hillsignal_premium", purchaseToken: "token-premium-1" });
    expect(res.status).toBe(200);
    expect(res.body.user.plan).toBe("premium");
    expect(res.body.entitlements.aiChat).toBe(true);
    expect(acked).toContain("token-premium-1");

    // Someone else can't claim the same token.
    const other = await register("thief@example.com");
    const stolen = await api
      .post("/api/v1/billing/google/verify")
      .set("Authorization", `Bearer ${other.token}`)
      .send({ productId: "hillsignal_premium", purchaseToken: "token-premium-1" });
    expect(stolen.status).toBe(409);
  });

  it("downgrades on an RTDN once the subscription expires", async () => {
    const { token, userId } = await register("expiring@example.com");
    subs.set("token-pro-2", {
      subscriptionState: "SUBSCRIPTION_STATE_ACTIVE",
      acknowledgementState: "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
      lineItems: [{ productId: "hillsignal_pro", expiryTime: future }],
    });
    await api
      .post("/api/v1/billing/google/verify")
      .set("Authorization", `Bearer ${token}`)
      .send({ productId: "hillsignal_pro", purchaseToken: "token-pro-2" });
    expect((await api.get("/api/v1/me").set("Authorization", `Bearer ${token}`)).body.user.plan).toBe("pro");

    subs.set("token-pro-2", {
      subscriptionState: "SUBSCRIPTION_STATE_EXPIRED",
      lineItems: [{ productId: "hillsignal_pro", expiryTime: new Date(Date.now() - 1000).toISOString() }],
    });
    const body = {
      message: {
        data: Buffer.from(
          JSON.stringify({ packageName: "com.hillsignal.app", subscriptionNotification: { purchaseToken: "token-pro-2", subscriptionId: "hillsignal_pro", notificationType: 13 } }),
        ).toString("base64"),
      },
    };
    expect((await api.post("/api/v1/billing/google/rtdn").send(body)).status).toBe(401);
    expect((await api.post("/api/v1/billing/google/rtdn").query({ secret: "test-rtdn-secret" }).send(body)).status).toBe(204);
    const me = await api.get("/api/v1/me").set("Authorization", `Bearer ${token}`);
    expect(me.body.user.plan).toBe("free");
    expect(userId).toBeTruthy();
  });
});

describe("account deletion", () => {
  it("deletes the account and invalidates the token", async () => {
    const { token } = await register("leaver@example.com");
    expect((await api.delete("/api/v1/me").set("Authorization", `Bearer ${token}`)).status).toBe(204);
    expect((await api.get("/api/v1/me").set("Authorization", `Bearer ${token}`)).status).toBe(401);
  });
});
