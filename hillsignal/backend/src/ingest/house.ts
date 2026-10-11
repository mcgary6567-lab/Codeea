import { unzipSync, strFromU8 } from "fflate";
import { XMLParser } from "fast-xml-parser";
import { extractText, getDocumentProxy } from "unpdf";
import { politeFetch } from "./fetch.js";
import { parseUsDate, type RawTrade } from "./normalize.js";

// U.S. House Clerk financial disclosures (https://disclosures-clerk.house.gov).
const BASE = "https://disclosures-clerk.house.gov/public_disc";
export const houseIndexUrl = (year: number) => `${BASE}/financial-pdfs/${year}FD.zip`;
export const housePtrUrl = (year: number, docId: string) => `${BASE}/ptr-pdfs/${year}/${docId}.pdf`;

export interface HouseFiling {
  docId: string;
  year: number;
  firstName: string;
  lastName: string;
  state: string;
  district: string | null;
  filingDate: string; // YYYY-MM-DD
}

/** Parse {year}FD.xml and keep Periodic Transaction Reports (FilingType "P"). */
export function parseHouseIndex(xml: string, year: number): HouseFiling[] {
  const doc = new XMLParser({ parseTagValue: false }).parse(xml) as {
    FinancialDisclosure?: { Member?: Record<string, string> | Record<string, string>[] };
  };
  const raw = doc.FinancialDisclosure?.Member ?? [];
  const members = Array.isArray(raw) ? raw : [raw];
  const out: HouseFiling[] = [];
  for (const m of members) {
    if (String(m.FilingType ?? "").trim() !== "P") continue;
    const stateDst = String(m.StateDst ?? "").trim(); // e.g. "CA11", "TX07", "AKAL"
    const filingDate = parseUsDate(String(m.FilingDate ?? ""));
    const docId = String(m.DocID ?? "").trim();
    if (!docId || !filingDate || stateDst.length < 2) continue;
    const dst = stateDst.slice(2);
    out.push({
      docId,
      year,
      firstName: String(m.First ?? "").trim(),
      lastName: String(m.Last ?? "").trim(),
      state: stateDst.slice(0, 2).toUpperCase(),
      district: dst && dst !== "AL" ? String(Number(dst)) : dst === "AL" ? "0" : null,
      filingDate,
    });
  }
  return out;
}

export async function fetchHouseIndex(year: number): Promise<HouseFiling[]> {
  const res = await politeFetch(houseIndexUrl(year));
  if (!res.ok) throw new Error(`House index ${year}: HTTP ${res.status}`);
  const files = unzipSync(new Uint8Array(await res.arrayBuffer()));
  const xmlName = Object.keys(files).find((n) => n.toLowerCase().endsWith(".xml"));
  if (!xmlName) throw new Error(`House index ${year}: no XML in zip`);
  return parseHouseIndex(strFromU8(files[xmlName]!), year);
}

// Lines in electronic PTR PDFs that are layout, not data.
const NOISE = [
  /^Filing ID #?\d+/i,
  /^\* For the complete list of asset type abbreviations/i,
  /^(ID|Owner|Asset|Transaction|Type|Date|Notification|Amount|Cap\.|Gains >|\$200\?)$/i,
  /^ID Owner Asset/i,
  /^Clerk of the House/i,
  /^Periodic Transaction Report/i,
  /^P\s?T\s?R/i,
];

// Per-row detail fields printed under a transaction: Filing Status, Subholding Of, Description, Comments.
const DETAIL = /^(F\s?S|S\s?O|D|C|L)\s*:/;

// The owner code (SP/JT/DC, blank = self) is the start of the asset text; split it off after matching.
const ROW = new RegExp(
  String.raw`(.+?)\s*\[([A-Z]{2})\]\s*` + // owner + asset name + [type code]
    String.raw`(P|S\s?\(partial\)|S|E)\s+` + // transaction type
    String.raw`(\d{2}\/\d{2}\/\d{4})\s+(\d{2}\/\d{2}\/\d{4})\s+` + // tx date, notification date
    String.raw`(Over \$[\d,]+|\$[\d,]+\s*-\s*\$[\d,]+|\$[\d,]+)`, // amount
  // ("Cap. Gains > $200?" checkbox, when present, prints after the amount and is ignored.)
  "g",
);

/**
 * Pull transaction rows out of the text of an electronically filed PTR.
 * Scanned paper filings have no text layer and yield [] (caller marks them no_text).
 */
export function parsePtrText(text: string): Omit<RawTrade, "source" | "filingId" | "member" | "pubDate" | "sourceUrl">[] {
  let lines = text
    .replace(/\u0000/g, "")
    .split(/\r?\n/)
    .map((l) => l.replace(/\s+/g, " ").trim());
  // Everything before the table header (filer name, status, district) is not row data.
  const header = lines.findIndex((l) => /^\$200\?$/.test(l) || /Gains > \$200\?$/.test(l));
  if (header >= 0) lines = lines.slice(header + 1);
  lines = lines.filter((l) => l && !NOISE.some((re) => re.test(l)) && !DETAIL.test(l));
  // Rows wrap across lines; amounts like "$1,001 -\n$15,000" too.
  const flat = lines.join(" ").replace(/\$\s+/g, "$").replace(/-\s+\$/g, "- $");

  const rows: Omit<RawTrade, "source" | "filingId" | "member" | "pubDate" | "sourceUrl">[] = [];
  for (const m of flat.matchAll(ROW)) {
    const [, assetRaw, typeCode, tx, txDate] = m;
    const amount = m[6]!;
    // Text since the previous row can start with that row's "Cap. Gains" Yes/No marker.
    let asset = assetRaw!.trim().replace(/^(?:(?:Yes|No)\s+)?/, "");
    const ownerMatch = /^(SP|JT|DC)\s+/.exec(asset);
    const owner = ownerMatch?.[1] ?? null;
    if (ownerMatch) asset = asset.slice(ownerMatch[0].length).trim();
    const iso = parseUsDate(txDate!);
    if (!iso || !asset) continue;
    const ticker = /\(([A-Z][A-Z0-9.\-]{0,9})\)\s*$/.exec(asset)?.[1] ?? null;
    rows.push({
      rowIndex: rows.length,
      owner: owner ?? "self",
      assetName: asset,
      ticker,
      assetType: typeCode!,
      txType: tx!.replace(/\s+/g, " "),
      txDate: iso,
      amount,
    });
  }
  return rows;
}

export async function fetchHousePtr(filing: HouseFiling): Promise<{ rows: RawTrade[]; hasText: boolean }> {
  const url = housePtrUrl(filing.year, filing.docId);
  const res = await politeFetch(url);
  if (!res.ok) throw new Error(`PTR ${filing.docId}: HTTP ${res.status}`);
  const pdf = await getDocumentProxy(new Uint8Array(await res.arrayBuffer()));
  const { text } = await extractText(pdf, { mergePages: true });
  const hasText = text.replace(/\s/g, "").length > 50;
  const parsed = parsePtrText(text);
  const pubDate = new Date(`${filing.filingDate}T12:00:00Z`);
  return {
    hasText,
    rows: parsed.map((r) => ({
      ...r,
      source: "house" as const,
      filingId: filing.docId,
      member: {
        firstName: filing.firstName,
        lastName: filing.lastName,
        chamber: "house" as const,
        state: filing.state,
        district: filing.district,
      },
      pubDate,
      sourceUrl: url,
    })),
  };
}
