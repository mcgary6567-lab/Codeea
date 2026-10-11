import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import Anthropic from "@anthropic-ai/sdk";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApp } from "../src/app.js";
import { setAiClientForTests } from "../src/ai/client.js";
import { migrate } from "../src/db/migrate.js";
import { pool, query } from "../src/db/pool.js";
import { seed } from "../src/db/seed.js";

// A fake Messages API: records request bodies and replies from a script.
type Reply = (body: Record<string, unknown>) => Record<string, unknown>;
const requests: Record<string, unknown>[] = [];
let replies: Reply[] = [];
let server: Server;

const message = (content: unknown[], stop_reason = "end_turn") => ({
  id: "msg_test",
  type: "message",
  role: "assistant",
  model: "claude-opus-5-5",
  content,
  stop_reason,
  stop_sequence: null,
  usage: { input_tokens: 10, output_tokens: 10 },
});

const api = request(createApp());

async function premiumUser(email: string) {
  const res = await api.post("/api/v1/auth/register").send({ email, password: "correct-horse-battery" });
  await query("UPDATE users SET granted_plan = 'premium' WHERE id = $1", [res.body.user.id]);
  return { Authorization: `Bearer ${res.body.token}` };
}

beforeAll(async () => {
  await migrate(() => {});
  await seed();
  server = createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", () => {
      const body = JSON.parse(raw) as Record<string, unknown>;
      requests.push(body);
      const next = replies.shift();
      res.setHeader("Content-Type", "application/json");
      if (!next) {
        res.statusCode = 500;
        res.end(JSON.stringify({ type: "error", error: { type: "api_error", message: "no scripted reply" } }));
        return;
      }
      res.end(JSON.stringify(next(body)));
    });
  });
  await new Promise<void>((r) => server.listen(0, r));
  const port = (server.address() as AddressInfo).port;
  setAiClientForTests(new Anthropic({ apiKey: "test", baseURL: `http://127.0.0.1:${port}`, maxRetries: 0 }));
});

afterAll(async () => {
  setAiClientForTests(null);
  server.close();
  await pool.end();
});

describe("AI search", () => {
  it("turns a question into filters and runs them", async () => {
    const auth = await premiumUser("search-ai@example.com");
    requests.length = 0;
    replies = [
      () =>
        message([
          {
            type: "text",
            text: JSON.stringify({
              politicianName: null, chamber: "senate", party: null, state: null, ticker: "nvda", sector: null,
              txType: "buy", from: null, to: null, minAmount: null, sort: "-pubDate",
              explanation: "Senators buying NVIDIA",
            }),
          },
        ]),
    ];
    const res = await api.post("/api/v1/ai/search").set(auth).send({ query: "Which senators bought Nvidia?" });
    expect(res.status).toBe(200);
    expect(res.body.filters).toEqual({ chamber: "senate", ticker: "NVDA", txType: "buy" });
    for (const t of res.body.results.data) {
      expect(t.politician.chamber).toBe("senate");
      expect(t.issuer.ticker).toBe("NVDA");
    }
    expect(res.body.remaining).toBe(499);

    const sent = requests[0]!;
    expect(sent.model).toBe("claude-opus-5-5");
    expect(sent.fallbacks).toBe("default");
    expect(sent).not.toHaveProperty("thinking"); // adaptive by default; disabling is rejected
    expect((sent.output_config as { format: { type: string } }).format.type).toBe("json_schema");
  });

  it("maps a refusal to 422 and refunds quota", async () => {
    const auth = await premiumUser("refusal@example.com");
    replies = [() => message([], "refusal")];
    const res = await api.post("/api/v1/ai/search").set(auth).send({ query: "something odd" });
    expect(res.status).toBe(422);
    expect(res.body.error.code).toBe("ai_declined");
  });
});

describe("AI chat", () => {
  it("runs the tool loop against real data and returns the answer", async () => {
    const auth = await premiumUser("chat@example.com");
    requests.length = 0;
    replies = [
      () =>
        message(
          [
            {
              type: "tool_use",
              id: "toolu_1",
              name: "search_trades",
              input: {
                politicianName: "Lindqvist", ticker: null, chamber: null, party: null, state: null, txType: null,
                from: null, to: null, minAmount: null, sort: "-txDate", limit: 5,
              },
            },
          ],
          "tool_use",
        ),
      (body) => {
        // The tool result for our call must come back in the next request.
        const msgs = body.messages as { role: string; content: unknown }[];
        const last = msgs.at(-1)!;
        const result = (last.content as { type: string; content: string; tool_use_id: string }[])[0]!;
        expect(result.tool_use_id).toBe("toolu_1");
        const parsed = JSON.parse(result.content) as { trades: { politician: string }[] };
        expect(parsed.trades.length).toBeGreaterThan(0);
        expect(parsed.trades[0]!.politician).toContain("Lindqvist");
        return message([{ type: "text", text: "Avery Lindqvist disclosed several trades." }]);
      },
    ];
    const res = await api
      .post("/api/v1/ai/chat")
      .set(auth)
      .send({ messages: [{ role: "user", content: "What has Lindqvist traded lately?" }] });
    expect(res.status).toBe(200);
    expect(res.body.reply).toBe("Avery Lindqvist disclosed several trades.");
    expect(res.body.disclaimer).toMatch(/Not investment advice/);
    expect(requests).toHaveLength(2);
    const firstMsgs = requests[0]!.messages as { role: string }[];
    expect(firstMsgs.map((m) => m.role)).toEqual(["user", "system"]);
  });

  it("rejects conversations that don't end with a user turn", async () => {
    const auth = await premiumUser("chat2@example.com");
    const res = await api
      .post("/api/v1/ai/chat")
      .set(auth)
      .send({ messages: [{ role: "user", content: "hi" }, { role: "assistant", content: "hello" }] });
    expect(res.status).toBe(400);
  });
});

describe("AI insights", () => {
  it("generates once and serves the cached insight after", async () => {
    const auth = await premiumUser("insight@example.com");
    requests.length = 0;
    replies = [
      () =>
        message([
          {
            type: "text",
            text: JSON.stringify({
              headline: "Mostly large-cap tech",
              summary: "Trades concentrate in a few names.",
              keyPoints: ["a", "b", "c"],
              caveats: ["ranges only"],
            }),
          },
        ]),
    ];
    const first = await api.get("/api/v1/ai/politicians/sample-okafor/insight").set(auth);
    expect(first.status).toBe(200);
    expect(first.body.insight).toMatchObject({ headline: "Mostly large-cap tech", disclaimer: expect.stringMatching(/advice/) });
    const second = await api.get("/api/v1/ai/politicians/sample-okafor/insight").set(auth);
    expect(second.body.insight.headline).toBe("Mostly large-cap tech");
    expect(requests).toHaveLength(1);
  });
});
