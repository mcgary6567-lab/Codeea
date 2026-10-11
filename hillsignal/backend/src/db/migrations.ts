// Ordered, append-only migrations. Never edit one that has shipped; add a new entry.
export const migrations: { id: string; sql: string }[] = [
  {
    id: "001_init",
    sql: `
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE politicians (
  id            TEXT PRIMARY KEY,             -- bioguide id when known, else a slug
  bioguide_id   TEXT UNIQUE,
  first_name    TEXT NOT NULL,
  last_name     TEXT NOT NULL,
  name          TEXT NOT NULL,
  party         TEXT CHECK (party IN ('D','R','I')),       -- null until matched to a member record
  chamber       TEXT NOT NULL CHECK (chamber IN ('house','senate')),
  state         TEXT NOT NULL,
  district      TEXT,
  photo_url     TEXT,
  in_office     BOOLEAN NOT NULL DEFAULT true,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX politicians_name_trgm ON politicians USING gin (name gin_trgm_ops);
CREATE INDEX politicians_lookup ON politicians (chamber, state, lower(last_name));

CREATE TABLE issuers (
  id          BIGSERIAL PRIMARY KEY,
  ticker      TEXT UNIQUE,
  name        TEXT NOT NULL,
  sector      TEXT,
  name_key    TEXT UNIQUE                      -- normalized name for ticker-less assets
);
CREATE INDEX issuers_name_trgm ON issuers USING gin (name gin_trgm_ops);

CREATE TABLE trades (
  id            TEXT PRIMARY KEY,             -- deterministic: source + filing + row hash
  politician_id TEXT NOT NULL REFERENCES politicians(id),
  issuer_id     BIGINT REFERENCES issuers(id),
  asset_name    TEXT NOT NULL,
  asset_type    TEXT,
  tx_type       TEXT NOT NULL CHECK (tx_type IN ('buy','sell','exchange')),
  tx_date       DATE NOT NULL,
  pub_date      TIMESTAMPTZ NOT NULL,
  owner         TEXT CHECK (owner IN ('self','spouse','joint','dependent')),
  amount_min    NUMERIC,
  amount_max    NUMERIC,
  amount_label  TEXT NOT NULL,
  source        TEXT NOT NULL,
  source_url    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX trades_pub_date ON trades (pub_date DESC);
CREATE INDEX trades_tx_date ON trades (tx_date DESC);
CREATE INDEX trades_politician ON trades (politician_id, tx_date DESC);
CREATE INDEX trades_issuer ON trades (issuer_id, tx_date DESC);

-- Filings already processed, so re-runs skip them.
CREATE TABLE filings (
  source      TEXT NOT NULL,
  filing_id   TEXT NOT NULL,
  status      TEXT NOT NULL,                  -- parsed | no_text | error
  detail      TEXT,
  trade_count INT NOT NULL DEFAULT 0,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source, filing_id)
);

CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email           TEXT NOT NULL UNIQUE,
  password_hash   TEXT NOT NULL,
  name            TEXT,
  granted_plan    TEXT NOT NULL DEFAULT 'free' CHECK (granted_plan IN ('free','pro','premium')),
  granted_until   TIMESTAMPTZ,                -- for manual/promo grants
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE subscriptions (
  purchase_token  TEXT PRIMARY KEY,
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  store           TEXT NOT NULL DEFAULT 'google_play',
  product_id      TEXT NOT NULL,
  plan            TEXT NOT NULL CHECK (plan IN ('pro','premium')),
  state           TEXT NOT NULL,
  expires_at      TIMESTAMPTZ,
  auto_renewing   BOOLEAN NOT NULL DEFAULT false,
  linked_token    TEXT,
  raw             JSONB NOT NULL,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX subscriptions_user ON subscriptions (user_id);

CREATE TABLE watchlist (
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('politician','issuer')),
  target_id  TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, kind, target_id)
);
CREATE INDEX watchlist_target ON watchlist (kind, target_id);

CREATE TABLE devices (
  push_token TEXT PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  platform   TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ai_usage (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day     DATE NOT NULL,
  kind    TEXT NOT NULL,
  count   INT NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day, kind)
);

CREATE TABLE ai_cache (
  key        TEXT PRIMARY KEY,
  value      JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
`,
  },
];
