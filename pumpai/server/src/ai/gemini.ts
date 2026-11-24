/**
 * Google Gemini adapter — a free-tier alternative to Claude for installs that don't want a paid key.
 * Talks to the Generative Language REST API directly (no SDK, no extra dependency) and mirrors the
 * three things the app needs: an agent loop with tool/function calling, structured extraction
 * (photos + voice), and one-shot text. Selected automatically when a Gemini key is set and no
 * Claude key is. Every caller still falls back to the rule engine if a call throws.
 */
import { config } from "../config.js";
import type { ToolCtx } from "./tools.js";

// runTool is passed in (not imported) so this module stays free of the heavy tools.js import graph — avoids a require cycle.
type RunTool = (tools: any, name: string, ctx: ToolCtx, input: unknown) => Promise<{ content: string; is_error?: boolean }>;

export const geminiActive = () => Boolean(config.geminiKey) && !config.anthropicKey;

type ToolSchema = { name: string; description?: string; input_schema: any };
type Part =
  | { text: string }
  | { inlineData: { mimeType: string; data: string } }
  | { functionCall: { name: string; args: Record<string, unknown> } }
  | { functionResponse: { name: string; response: Record<string, unknown> } };
export type GContent = { role: "user" | "model"; parts: Part[] };

/** Convert a JSON-Schema fragment to Gemini's OpenAPI subset (UPPERCASE types, no unsupported keys). */
function gSchema(s: any): any {
  if (!s || typeof s !== "object") return s;
  const out: any = {};
  if (s.type) out.type = String(s.type).toUpperCase();
  if (s.description) out.description = s.description;
  if (s.enum) out.enum = s.enum;
  if (s.format) out.format = s.format;
  if (s.nullable) out.nullable = s.nullable;
  if (s.properties) {
    out.properties = {};
    for (const [k, v] of Object.entries(s.properties)) out.properties[k] = gSchema(v);
  }
  if (Array.isArray(s.required) && s.required.length) out.required = s.required;
  if (s.items) out.items = gSchema(s.items);
  return out;
}

async function call(body: Record<string, unknown>): Promise<any> {
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${config.geminiModel}:generateContent?key=${config.geminiKey}`;
  let lastErr: Error | null = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 60_000);
    try {
      const res = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal: ctrl.signal });
      clearTimeout(timer);
      if (res.status === 429 || res.status >= 500) { lastErr = new Error(`Gemini HTTP ${res.status}`); await new Promise((r) => setTimeout(r, 400 * (attempt + 1))); continue; }
      if (!res.ok) throw new Error(`Gemini HTTP ${res.status}: ${(await res.text()).slice(0, 300)}`);
      return await res.json();
    } catch (e: any) {
      clearTimeout(timer);
      lastErr = e;
      if (e?.name === "AbortError") break;
    }
  }
  throw lastErr ?? new Error("Gemini call failed");
}

const textOf = (data: any): string =>
  (data?.candidates?.[0]?.content?.parts ?? []).filter((p: any) => typeof p.text === "string").map((p: any) => p.text).join("").trim();

/** One-shot text generation (campaign writer, free-form owner/customer answers). */
export async function geminiText(system: string, user: string): Promise<string | null> {
  const data = await call({
    systemInstruction: { parts: [{ text: system }] },
    contents: [{ role: "user", parts: [{ text: user }] }],
    generationConfig: { maxOutputTokens: 2048, temperature: 0.7 },
  });
  return textOf(data) || null;
}

/** Structured extraction: force a JSON object matching `schema` (photos, voice parsers). */
export async function geminiRecord<T>(parts: Part[], schema: Record<string, unknown>): Promise<T | null> {
  const data = await call({
    contents: [{ role: "user", parts }],
    generationConfig: { responseMimeType: "application/json", responseSchema: gSchema({ type: "object", ...schema }), maxOutputTokens: 2048 },
  });
  const raw = textOf(data);
  if (!raw) return null;
  try { return JSON.parse(raw) as T; } catch { return null; }
}

/** Agent loop with function calling: returns the final text, or null (so the caller uses rules). */
export async function geminiAgentLoop(system: string, tools: ToolSchema[], contents: GContent[], ctx: ToolCtx, runTool: RunTool, maxSteps = 6): Promise<string | null> {
  const functionDeclarations = tools.map((t) => ({ name: t.name, description: t.description, parameters: gSchema(t.input_schema) }));
  const history = [...contents];
  for (let step = 0; step < maxSteps; step++) {
    const data = await call({
      systemInstruction: { parts: [{ text: system }] },
      contents: history,
      tools: [{ functionDeclarations }],
      toolConfig: { functionCallingConfig: { mode: "AUTO" } },
      generationConfig: { maxOutputTokens: 4096 },
    });
    const parts: Part[] = data?.candidates?.[0]?.content?.parts ?? [];
    const calls = parts.filter((p): p is Extract<Part, { functionCall: any }> => "functionCall" in p && !!p.functionCall);
    if (calls.length === 0) return textOf(data) || null;
    history.push({ role: "model", parts });
    const responses: Part[] = [];
    for (const c of calls) {
      const r = await runTool(tools as any, c.functionCall.name, ctx, c.functionCall.args ?? {});
      let payload: Record<string, unknown>;
      try { payload = { result: JSON.parse(r.content) }; } catch { payload = { result: r.content }; }
      if (r.is_error) payload = { error: r.content };
      responses.push({ functionResponse: { name: c.functionCall.name, response: payload } });
    }
    history.push({ role: "user", parts: responses });
  }
  return null;
}

/** Build a Gemini `contents` list from text-only {role,content} history (role "assistant" → "model"). */
export const toGContents = (msgs: { role: "user" | "assistant"; content: string }[]): GContent[] =>
  msgs.map((m) => ({ role: m.role === "assistant" ? "model" : "user", parts: [{ text: m.content }] }));
