import { GoogleAuth, OAuth2Client } from "google-auth-library";
import { config } from "../config.js";
import { query } from "../db/pool.js";
import { HttpError } from "../http.js";
import { GOOGLE_PRODUCTS } from "../plans.js";

// Subset of purchases.subscriptionsv2 we rely on.
// https://developer.android.com/google/play/billing/rtdn-reference / androidpublisher v3
export interface SubscriptionPurchaseV2 {
  subscriptionState: string;
  linkedPurchaseToken?: string;
  acknowledgementState?: "ACKNOWLEDGEMENT_STATE_PENDING" | "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED";
  externalAccountIdentifiers?: { obfuscatedExternalAccountId?: string };
  testPurchase?: object;
  lineItems?: { productId: string; expiryTime?: string; autoRenewingPlan?: { autoRenewEnabled?: boolean } }[];
}

export interface PlayApi {
  getSubscription(purchaseToken: string): Promise<SubscriptionPurchaseV2>;
  acknowledge(productId: string, purchaseToken: string): Promise<void>;
}

const API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications";

export function googlePlayApi(): PlayApi {
  if (!config.GOOGLE_SERVICE_ACCOUNT_JSON) {
    throw new HttpError(503, "billing_unavailable", "Google Play billing is not configured on the server");
  }
  const auth = new GoogleAuth({
    credentials: JSON.parse(config.GOOGLE_SERVICE_ACCOUNT_JSON),
    scopes: ["https://www.googleapis.com/auth/androidpublisher"],
  });
  const pkg = encodeURIComponent(config.GOOGLE_PLAY_PACKAGE_NAME);
  return {
    async getSubscription(token) {
      const client = await auth.getClient();
      const res = await client.request<SubscriptionPurchaseV2>({
        url: `${API}/${pkg}/purchases/subscriptionsv2/tokens/${encodeURIComponent(token)}`,
      });
      return res.data;
    },
    async acknowledge(productId, token) {
      const client = await auth.getClient();
      await client.request({
        method: "POST",
        url: `${API}/${pkg}/purchases/subscriptions/${encodeURIComponent(productId)}/tokens/${encodeURIComponent(token)}:acknowledge`,
        data: {},
      });
    },
  };
}

let override: PlayApi | null = null;
/** Tests inject a fake Play API. */
export function setPlayApiForTests(api: PlayApi | null) {
  override = api;
}
const play = () => override ?? googlePlayApi();

/**
 * Verify a purchase token with Google, store the subscription for the user, and
 * acknowledge it (unacknowledged purchases are refunded by Google after 3 days).
 */
export async function verifyAndStore(userId: string | null, productId: string, purchaseToken: string) {
  const sub = await play().getSubscription(purchaseToken);
  const item = sub.lineItems?.find((l) => GOOGLE_PRODUCTS[l.productId]) ?? sub.lineItems?.[0];
  const plan = item ? GOOGLE_PRODUCTS[item.productId] : undefined;
  if (!item || !plan) throw new HttpError(400, "invalid_request", `Unknown product ${productId}`);

  const [existing] = await query<{ user_id: string }>(
    "SELECT user_id FROM subscriptions WHERE purchase_token = $1",
    [purchaseToken],
  );
  // RTDN calls have no user: only update tokens we already know about.
  const ownerId = userId ?? existing?.user_id;
  if (!ownerId) return null;
  if (existing && existing.user_id !== ownerId) {
    throw new HttpError(409, "conflict", "This purchase belongs to a different account");
  }
  // The app passes obfuscatedAccountIdAndroid = user id when purchasing.
  const boundTo = sub.externalAccountIdentifiers?.obfuscatedExternalAccountId;
  if (userId && boundTo && boundTo !== userId) {
    throw new HttpError(409, "conflict", "This purchase belongs to a different account");
  }

  await query(
    `INSERT INTO subscriptions (purchase_token, user_id, product_id, plan, state, expires_at, auto_renewing, linked_token, raw, updated_at)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, now())
     ON CONFLICT (purchase_token) DO UPDATE SET product_id = EXCLUDED.product_id, plan = EXCLUDED.plan,
       state = EXCLUDED.state, expires_at = EXCLUDED.expires_at, auto_renewing = EXCLUDED.auto_renewing,
       linked_token = EXCLUDED.linked_token, raw = EXCLUDED.raw, updated_at = now()`,
    [
      purchaseToken,
      ownerId,
      item.productId,
      plan,
      sub.subscriptionState,
      item.expiryTime ?? null,
      item.autoRenewingPlan?.autoRenewEnabled ?? false,
      sub.linkedPurchaseToken ?? null,
      JSON.stringify(sub),
    ],
  );
  // An upgrade/downgrade/resubscribe issues a new token; the old one is superseded.
  if (sub.linkedPurchaseToken) {
    await query(
      "UPDATE subscriptions SET state = 'SUPERSEDED', updated_at = now() WHERE purchase_token = $1 AND user_id = $2",
      [sub.linkedPurchaseToken, ownerId],
    );
  }
  if (sub.acknowledgementState === "ACKNOWLEDGEMENT_STATE_PENDING" && sub.subscriptionState === "SUBSCRIPTION_STATE_ACTIVE") {
    await play().acknowledge(item.productId, purchaseToken);
  }
  return ownerId;
}

const oidc = new OAuth2Client();

/** Authenticate a Pub/Sub push: OIDC token (preferred) or shared secret in the URL. */
export async function verifyPubSubPush(authHeader: string | undefined, secretParam: unknown): Promise<boolean> {
  if (config.RTDN_PUSH_AUDIENCE) {
    if (!authHeader?.startsWith("Bearer ")) return false;
    try {
      const ticket = await oidc.verifyIdToken({ idToken: authHeader.slice(7), audience: config.RTDN_PUSH_AUDIENCE });
      return ticket.getPayload()?.email_verified === true;
    } catch {
      return false;
    }
  }
  return !!config.RTDN_SHARED_SECRET && secretParam === config.RTDN_SHARED_SECRET;
}

/** Decode a Real-time Developer Notification from a Pub/Sub push body. */
export function decodeRtdn(body: unknown): { purchaseToken: string; productId: string } | null {
  const data = (body as { message?: { data?: string } })?.message?.data;
  if (!data) return null;
  try {
    const n = JSON.parse(Buffer.from(data, "base64").toString("utf8")) as {
      packageName?: string;
      subscriptionNotification?: { purchaseToken?: string; subscriptionId?: string };
    };
    const s = n.subscriptionNotification;
    if (n.packageName !== config.GOOGLE_PLAY_PACKAGE_NAME || !s?.purchaseToken) return null;
    return { purchaseToken: s.purchaseToken, productId: s.subscriptionId ?? "" };
  } catch {
    return null;
  }
}
