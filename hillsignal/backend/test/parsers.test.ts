import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { cleanFilters } from "../src/ai/search.js";
import { decodeRtdn } from "../src/billing/googlePlay.js";
import { parseHouseIndex, parsePtrText } from "../src/ingest/house.js";
import { normalizeOwner, normalizeTxType, parseAmount, parseUsDate } from "../src/ingest/normalize.js";
import { parsePtrHtml, parseSearchRows } from "../src/ingest/senate.js";

const fixture = (name: string) => readFileSync(new URL(`./fixtures/${name}`, import.meta.url), "utf8");

describe("normalize", () => {
  it("parses disclosure amount ranges", () => {
    expect(parseAmount("$1,001 - $15,000")).toEqual({ min: 1001, max: 15000, label: "$1,001 - $15,000" });
    expect(parseAmount("Over $50,000,000")).toEqual({ min: 50000001, max: null, label: "Over $50,000,000" });
    expect(parseAmount("Spouse/DC Over $1,000,000").max).toBeNull();
    expect(parseAmount("--")).toEqual({ min: null, max: null, label: "--" });
  });

  it("maps House codes and Senate words to the same transaction types", () => {
    expect(["P", "Purchase", "S", "S (partial)", "Sale (Full)", "Sale (Partial)", "E", "Exchange", "??"].map(normalizeTxType)).toEqual([
      "buy", "buy", "sell", "sell", "sell", "sell", "exchange", "exchange", null,
    ]);
    expect(["SP", "Spouse", "JT", "DC", "Child", "Self", null].map(normalizeOwner)).toEqual([
      "spouse", "spouse", "joint", "dependent", "dependent", "self", null,
    ]);
  });

  it("parses US dates strictly", () => {
    expect(parseUsDate("1/5/2025")).toBe("2025-01-05");
    expect(parseUsDate("2025-01-05")).toBeNull();
  });
});

describe("House Clerk", () => {
  it("keeps only PTR filings from the yearly index", () => {
    const filings = parseHouseIndex(fixture("house-index.xml"), 2025);
    expect(filings).toEqual([
      { docId: "20026545", year: 2025, firstName: "Pat", lastName: "Example", state: "CA", district: "11", filingDate: "2025-01-15" },
      { docId: "20026600", year: 2025, firstName: "Lee", lastName: "Atlarge", state: "AK", district: "0", filingDate: "2025-02-03" },
    ]);
  });

  it("extracts rows from PTR text, including wrapped names and amounts", () => {
    const rows = parsePtrText(fixture("house-ptr.txt"));
    expect(rows.map((r) => [r.owner, r.ticker, r.assetType, r.txType, r.txDate, r.amount])).toEqual([
      ["SP", "AAPL", "ST", "P", "2025-01-14", "$1,001 - $15,000"],
      ["self", "MSFT", "ST", "S (partial)", "2025-01-10", "$15,001 - $50,000"],
      ["JT", "NVDA", "OP", "P", "2024-12-20", "$250,001 - $500,000"],
      ["DC", null, "GS", "E", "2025-01-02", "Over $50,000,000"],
    ]);
    expect(rows[0]!.assetName).toBe("Apple Inc. (AAPL)");
    expect(rows[1]!.assetName).toBe("Microsoft Corporation - Common Stock (MSFT)");
    expect(rows[3]!.assetName).toBe("US Treasury Bill");
  });

  it("returns nothing for scanned filings without a text layer", () => {
    expect(parsePtrText("\n\n  \n")).toEqual([]);
  });
});

describe("Senate eFD", () => {
  it("reads search rows and skips non-PTR links", () => {
    const rows = parseSearchRows([
      ["Jane", "Doe, Jr.", "Doe, Jane (Senator)", '<a href="/search/view/ptr/0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0/" target="_blank">Periodic Transaction Report</a>', "03/10/2025"],
      ["Paper", "Filer", "x", '<a href="/search/view/paper/AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE/">Paper</a>', "03/11/2025"],
      ["No", "Link", "x", "nothing", "03/11/2025"],
    ]);
    expect(rows).toEqual([
      { reportId: "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0", kind: "ptr", firstName: "Jane", lastName: "Doe", filedDate: "2025-03-10" },
      { reportId: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", kind: "paper", firstName: "Paper", lastName: "Filer", filedDate: "2025-03-11" },
    ]);
  });

  it("parses the PTR transactions table", () => {
    const rows = parsePtrHtml(fixture("senate-ptr.html"));
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ owner: "Spouse", ticker: "AAPL", assetName: "Apple Inc. & Co", txType: "Purchase", txDate: "2025-03-04" });
    expect(rows[1]).toMatchObject({ ticker: null, txType: "Sale (Full)", amount: "$50,001 - $100,000" });
  });
});

describe("AI search filter cleaning", () => {
  it("drops nulls and malformed values from model output", () => {
    expect(
      cleanFilters({
        politicianName: " Pelosi ",
        chamber: null,
        party: "D",
        state: "california",
        ticker: "nvda",
        sector: null,
        txType: "sell",
        from: "2025-01-01",
        to: "last week",
        minAmount: -5,
      }),
    ).toEqual({ politicianName: "Pelosi", party: "D", ticker: "NVDA", txType: "sell", from: "2025-01-01" });
  });
});

describe("Google RTDN", () => {
  const encode = (o: object) => ({ message: { data: Buffer.from(JSON.stringify(o)).toString("base64") } });

  it("decodes subscription notifications for our package only", () => {
    const n = { packageName: "com.hillsignal.app", subscriptionNotification: { purchaseToken: "tok", subscriptionId: "hillsignal_pro" } };
    expect(decodeRtdn(encode(n))).toEqual({ purchaseToken: "tok", productId: "hillsignal_pro" });
    expect(decodeRtdn(encode({ ...n, packageName: "com.other" }))).toBeNull();
    expect(decodeRtdn({ message: { data: "not-base64-json" } })).toBeNull();
  });
});
