import { config } from "./config.js";
import { query } from "./db/pool.js";
import { buildAuthContext } from "./auth.js";

interface Watcher {
  user_id: string;
  push_token: string;
  trade_id: string;
  politician_name: string;
  tx_type: string;
  asset: string;
  amount_label: string;
}

/**
 * Push an alert to every device whose owner follows the politician or issuer of a
 * newly ingested trade and whose plan includes alerts.
 */
export async function notifyNewTrades(tradeIds: string[], log: (m: string) => void = () => {}): Promise<number> {
  if (tradeIds.length === 0) return 0;
  const rows = await query<Watcher>(
    `SELECT DISTINCT w.user_id, d.push_token, t.id AS trade_id, p.name AS politician_name, t.tx_type,
            COALESCE(i.ticker, t.asset_name) AS asset, t.amount_label
     FROM trades t
     JOIN politicians p ON p.id = t.politician_id
     LEFT JOIN issuers i ON i.id = t.issuer_id
     JOIN watchlist w ON (w.kind = 'politician' AND w.target_id = t.politician_id)
                      OR (w.kind = 'issuer' AND w.target_id = t.issuer_id::text)
     JOIN devices d ON d.user_id = w.user_id
     WHERE t.id = ANY($1)`,
    [tradeIds],
  );
  if (rows.length === 0) return 0;

  const allowed = new Map<string, boolean>();
  for (const userId of new Set(rows.map((r) => r.user_id))) {
    allowed.set(userId, (await buildAuthContext(userId)).entitlements.alerts);
  }
  const verb: Record<string, string> = { buy: "bought", sell: "sold", exchange: "exchanged" };
  const messages = rows
    .filter((r) => allowed.get(r.user_id))
    .map((r) => ({
      to: r.push_token,
      title: `${r.politician_name} ${verb[r.tx_type] ?? "traded"} ${r.asset}`,
      body: `${r.amount_label} · tap for details`,
      data: { type: "trade", tradeId: r.trade_id },
      channelId: "trades",
    }));

  // Expo accepts up to 100 messages per request.
  for (let i = 0; i < messages.length; i += 100) {
    const res = await fetch("https://exp.host/--/api/v2/push/send", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(config.EXPO_ACCESS_TOKEN ? { Authorization: `Bearer ${config.EXPO_ACCESS_TOKEN}` } : {}),
      },
      body: JSON.stringify(messages.slice(i, i + 100)),
    });
    if (!res.ok) log(`expo push failed: HTTP ${res.status}`);
  }
  return messages.length;
}
