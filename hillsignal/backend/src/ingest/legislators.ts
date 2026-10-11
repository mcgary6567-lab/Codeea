import type pg from "pg";

// Public-domain member data maintained by the @unitedstates project.
export const LEGISLATORS_URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json";
const photoUrl = (bioguide: string) => `https://unitedstates.github.io/images/congress/225x275/${bioguide}.jpg`;

export interface Legislator {
  id: { bioguide: string };
  name: { first: string; last: string; nickname?: string; official_full?: string };
  terms: { type: "rep" | "sen"; state: string; district?: number; party: string; end: string }[];
}

const PARTY: Record<string, "D" | "R" | "I"> = { Democrat: "D", Republican: "R", Independent: "I" };

export function toPoliticianRow(l: Legislator) {
  const term = l.terms.at(-1);
  if (!term) return null;
  const first = l.name.nickname ?? l.name.first;
  return {
    id: l.id.bioguide,
    bioguideId: l.id.bioguide,
    firstName: first,
    lastName: l.name.last,
    name: l.name.official_full ?? `${first} ${l.name.last}`,
    party: PARTY[term.party] ?? "I",
    chamber: term.type === "sen" ? ("senate" as const) : ("house" as const),
    state: term.state,
    district: term.type === "rep" && term.district !== undefined ? String(term.district) : null,
    photoUrl: photoUrl(l.id.bioguide),
    inOffice: Date.parse(term.end) > Date.now(),
  };
}

/**
 * Upsert members, then fold any placeholder politicians (created from filings
 * before we knew the member) into the matching real record.
 */
export async function syncLegislators(c: pg.PoolClient, legislators: Legislator[]): Promise<number> {
  let n = 0;
  for (const l of legislators) {
    const p = toPoliticianRow(l);
    if (!p) continue;
    await c.query(
      `INSERT INTO politicians (id, bioguide_id, first_name, last_name, name, party, chamber, state, district, photo_url, in_office, updated_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11, now())
       ON CONFLICT (id) DO UPDATE SET first_name = EXCLUDED.first_name, last_name = EXCLUDED.last_name,
         name = EXCLUDED.name, party = EXCLUDED.party, chamber = EXCLUDED.chamber, state = EXCLUDED.state,
         district = EXCLUDED.district, photo_url = EXCLUDED.photo_url, in_office = EXCLUDED.in_office, updated_at = now()`,
      [p.id, p.bioguideId, p.firstName, p.lastName, p.name, p.party, p.chamber, p.state, p.district, p.photoUrl, p.inOffice],
    );
    n++;
    // Placeholders: same chamber + last name (+ state if the placeholder has one), no bioguide id.
    const placeholders = await c.query<{ id: string }>(
      `SELECT id FROM politicians
       WHERE bioguide_id IS NULL AND chamber = $1 AND lower(last_name) = lower($2) AND (state = '' OR state = $3)
         AND (lower(first_name) LIKE lower(left($4, 3)) || '%' OR lower($4) LIKE lower(left(first_name, 3)) || '%')`,
      [p.chamber, p.lastName, p.state, p.firstName],
    );
    for (const ph of placeholders.rows) {
      await c.query("UPDATE trades SET politician_id = $1 WHERE politician_id = $2", [p.id, ph.id]);
      // Drop follows that would collide with an existing follow of the real record.
      await c.query(
        `DELETE FROM watchlist w WHERE kind = 'politician' AND target_id = $2
         AND EXISTS (SELECT 1 FROM watchlist x WHERE x.user_id = w.user_id AND x.kind = 'politician' AND x.target_id = $1)`,
        [p.id, ph.id],
      );
      await c.query("UPDATE watchlist SET target_id = $1 WHERE kind = 'politician' AND target_id = $2", [p.id, ph.id]);
      await c.query("DELETE FROM politicians WHERE id = $1", [ph.id]);
    }
  }
  return n;
}
