import { politeFetch, sleep } from "./fetch.js";
import { parseUsDate, type RawTrade } from "./normalize.js";

// U.S. Senate Electronic Financial Disclosures (https://efdsearch.senate.gov).
const BASE = "https://efdsearch.senate.gov";

export interface SenateFiling {
  reportId: string; // uuid from /search/view/ptr/<uuid>/
  kind: "ptr" | "paper";
  firstName: string;
  lastName: string;
  filedDate: string; // YYYY-MM-DD
}

const decode = (s: string) =>
  s
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&#x27;/g, "'")
    .replace(/\s+/g, " ")
    .trim();

/** Rows from /search/report/data/: [first, last, office, linkHtml, dateReceived]. */
export function parseSearchRows(rows: string[][]): SenateFiling[] {
  const out: SenateFiling[] = [];
  for (const r of rows) {
    const [first = "", last = "", , link = "", received = ""] = r;
    const m = /\/search\/view\/(ptr|paper)\/([0-9a-f-]{36})\//i.exec(link);
    const filedDate = parseUsDate(received);
    if (!m || !filedDate) continue;
    out.push({
      reportId: m[2]!.toLowerCase(),
      kind: m[1]!.toLowerCase() as "ptr" | "paper",
      firstName: decode(first),
      lastName: decode(last).replace(/,.*$/, ""), // "Smith, Jr." → "Smith"
      filedDate,
    });
  }
  return out;
}

/**
 * Parse the transactions table of an electronic PTR page. Columns:
 * # | Transaction Date | Owner | Ticker | Asset Name | Asset Type | Type | Amount | Comment
 */
export function parsePtrHtml(html: string): Omit<RawTrade, "source" | "filingId" | "member" | "pubDate" | "sourceUrl">[] {
  const tbody = /<tbody[^>]*>([\s\S]*?)<\/tbody>/i.exec(html)?.[1] ?? "";
  const rows: Omit<RawTrade, "source" | "filingId" | "member" | "pubDate" | "sourceUrl">[] = [];
  for (const tr of tbody.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)) {
    const cells = [...tr[1]!.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)].map((c) => decode(c[1]!));
    if (cells.length < 8) continue;
    const [, date, owner, ticker, asset, assetType, type, amount] = cells;
    const iso = parseUsDate(date!);
    if (!iso) continue;
    rows.push({
      rowIndex: rows.length,
      owner: owner!,
      assetName: asset!,
      ticker: ticker && ticker !== "--" ? ticker : null,
      assetType: assetType!,
      txType: type!,
      txDate: iso,
      amount: amount!,
    });
  }
  return rows;
}

/** eFD requires accepting the usage agreement and carrying the session + CSRF cookies. */
class EfdSession {
  private cookies = new Map<string, string>();

  private cookieHeader() {
    return [...this.cookies].map(([k, v]) => `${k}=${v}`).join("; ");
  }

  private store(res: Response) {
    for (const c of res.headers.getSetCookie()) {
      const [pair] = c.split(";");
      const idx = pair!.indexOf("=");
      if (idx > 0) this.cookies.set(pair!.slice(0, idx).trim(), pair!.slice(idx + 1).trim());
    }
  }

  async request(path: string, init: RequestInit = {}): Promise<Response> {
    const res = await politeFetch(`${BASE}${path}`, {
      ...init,
      redirect: "manual",
      headers: { Cookie: this.cookieHeader(), Referer: `${BASE}/search/`, ...(init.headers ?? {}) },
    });
    this.store(res);
    return res;
  }

  get csrf() {
    return this.cookies.get("csrftoken") ?? "";
  }

  async open() {
    const home = await this.request("/search/home/");
    const html = await home.text();
    const token = /name="csrfmiddlewaretoken" value="([^"]+)"/.exec(html)?.[1];
    if (!token) throw new Error("eFD: could not find CSRF token on agreement page");
    await this.request("/search/home/", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ prohibition_agreement: "1", csrfmiddlewaretoken: token }).toString(),
    });
  }
}

const usDate = (iso: string) => {
  const [y, m, d] = iso.split("-");
  return `${m}/${d}/${y}`;
};

export async function fetchSenateFilings(fromIso: string, toIso: string): Promise<{ session: EfdSession; filings: SenateFiling[] }> {
  const s = new EfdSession();
  await s.open();
  const filings: SenateFiling[] = [];
  const pageSize = 100;
  for (let start = 0; ; start += pageSize) {
    const res = await s.request("/search/report/data/", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": s.csrf },
      body: new URLSearchParams({
        start: String(start),
        length: String(pageSize),
        report_types: "[11]", // Periodic Transaction Report
        filer_types: "[]",
        submitted_start_date: `${usDate(fromIso)} 00:00:00`,
        submitted_end_date: `${usDate(toIso)} 23:59:59`,
        candidate_state: "",
        senator_state: "",
        office_id: "",
        first_name: "",
        last_name: "",
      }).toString(),
    });
    if (!res.ok) throw new Error(`eFD search: HTTP ${res.status}`);
    const body = (await res.json()) as { data: string[][]; recordsFiltered?: number; recordsTotal?: number };
    filings.push(...parseSearchRows(body.data));
    const total = body.recordsFiltered ?? body.recordsTotal ?? 0;
    if (start + pageSize >= total || body.data.length === 0) break;
    await sleep(1000);
  }
  return { session: s, filings };
}

export async function fetchSenatePtr(session: EfdSession, filing: SenateFiling): Promise<RawTrade[]> {
  const path = `/search/view/ptr/${filing.reportId}/`;
  const res = await session.request(path);
  if (!res.ok) throw new Error(`eFD PTR ${filing.reportId}: HTTP ${res.status}`);
  const pubDate = new Date(`${filing.filedDate}T12:00:00Z`);
  return parsePtrHtml(await res.text()).map((r) => ({
    ...r,
    source: "senate" as const,
    filingId: filing.reportId,
    member: { firstName: filing.firstName, lastName: filing.lastName, chamber: "senate" as const },
    pubDate,
    sourceUrl: `${BASE}${path}`,
  }));
}
