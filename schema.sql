-- Gold Scalpers App — MySQL 8 schema
-- Import once:  mysql -u USER -p DBNAME < schema.sql

SET NAMES utf8mb4;

-- ---------------------------------------------------------------
-- Admin operators (admin panel logins)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admins (
  id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  email         VARCHAR(190) NOT NULL UNIQUE,
  pass_hash     VARCHAR(255) NOT NULL,
  name          VARCHAR(120) NOT NULL DEFAULT '',
  role          ENUM('owner','support','readonly') NOT NULL DEFAULT 'support',
  totp_secret   VARCHAR(64) NULL,
  last_login    DATETIME NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- App users (customers)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  email         VARCHAR(190) NOT NULL UNIQUE,
  pass_hash     VARCHAR(255) NOT NULL,
  name          VARCHAR(120) NOT NULL DEFAULT '',
  status        ENUM('active','suspended','pending') NOT NULL DEFAULT 'pending',
  plan          ENUM('trial','monthly','lifetime') NOT NULL DEFAULT 'trial',
  plan_expires  DATETIME NULL,
  licence_key   VARCHAR(64) NULL UNIQUE,
  -- server-side master switch for this user, independent of their own toggle
  trading_enabled TINYINT(1) NOT NULL DEFAULT 0,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen     DATETIME NULL,
  INDEX idx_status (status),
  INDEX idx_plan (plan, plan_expires)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- Auth tokens issued to the app (opaque bearer, hashed at rest)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auth_tokens (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  user_id       INT UNSIGNED NOT NULL,
  token_hash    CHAR(64) NOT NULL UNIQUE,
  device_id     BIGINT UNSIGNED NULL,
  expires_at    DATETIME NOT NULL,
  revoked_at    DATETIME NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_user (user_id),
  CONSTRAINT fk_tok_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- Devices (one row per install; carries the FCM push token)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS devices (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  user_id       INT UNSIGNED NOT NULL,
  install_id    VARCHAR(64) NOT NULL,
  fcm_token     VARCHAR(255) NULL,
  platform      VARCHAR(20) NOT NULL DEFAULT 'android',
  app_version   VARCHAR(20) NOT NULL DEFAULT '',
  os_version    VARCHAR(20) NOT NULL DEFAULT '',
  model         VARCHAR(80) NOT NULL DEFAULT '',
  last_seen     DATETIME NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_install (user_id, install_id),
  CONSTRAINT fk_dev_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- Linked broker accounts (executed through MetaApi)
-- Credentials are AES-256-GCM encrypted with APP_KEY; never logged.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS broker_accounts (
  id                BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  user_id           INT UNSIGNED NOT NULL,
  label             VARCHAR(80) NOT NULL DEFAULT '',
  provider          ENUM('metaapi') NOT NULL DEFAULT 'metaapi',
  platform          ENUM('mt4','mt5') NOT NULL DEFAULT 'mt5',
  metaapi_account_id VARCHAR(64) NULL,
  broker_login      VARCHAR(64) NOT NULL,
  broker_server     VARCHAR(120) NOT NULL,
  enc_password      TEXT NULL,
  symbol            VARCHAR(24) NOT NULL DEFAULT 'XAUUSD',
  currency          VARCHAR(8) NOT NULL DEFAULT 'USD',
  is_demo           TINYINT(1) NOT NULL DEFAULT 1,
  -- 'live' execution requires an explicit, separately-audited flip
  live_approved     TINYINT(1) NOT NULL DEFAULT 0,
  status            ENUM('pending','deploying','connected','error','disabled')
                      NOT NULL DEFAULT 'pending',
  status_detail     VARCHAR(255) NOT NULL DEFAULT '',
  balance           DECIMAL(18,2) NOT NULL DEFAULT 0,
  equity            DECIMAL(18,2) NOT NULL DEFAULT 0,
  day_start_equity  DECIMAL(18,2) NOT NULL DEFAULT 0,
  day_start_balance DECIMAL(18,2) NOT NULL DEFAULT 0,
  equity_peak       DECIMAL(18,2) NOT NULL DEFAULT 0,
  day_key           VARCHAR(10) NOT NULL DEFAULT '',
  day_trades        INT NOT NULL DEFAULT 0,
  loss_streak       INT NOT NULL DEFAULT 0,
  cooldown_until    DATETIME NULL,
  halted            TINYINT(1) NOT NULL DEFAULT 0,
  halt_reason       VARCHAR(190) NOT NULL DEFAULT '',
  last_sync         DATETIME NULL,
  last_bar_ts       BIGINT NULL,
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_user (user_id),
  INDEX idx_status (status, halted),
  CONSTRAINT fk_acc_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- Strategy config. Layered: global < plan < user < account.
-- The app NEVER decides these; it only renders and requests changes.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_configs (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  scope         ENUM('global','plan','user','account') NOT NULL,
  ref_id        VARCHAR(64) NOT NULL DEFAULT '',
  payload       JSON NOT NULL,
  version       INT UNSIGNED NOT NULL DEFAULT 1,
  updated_by    INT UNSIGNED NULL,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_scope (scope, ref_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- Cached OHLC bars for the signal engine (one symbol/tf per row)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bars (
  symbol      VARCHAR(24) NOT NULL,
  timeframe   VARCHAR(8)  NOT NULL,
  ts          BIGINT      NOT NULL,
  o           DECIMAL(18,5) NOT NULL,
  h           DECIMAL(18,5) NOT NULL,
  l           DECIMAL(18,5) NOT NULL,
  c           DECIMAL(18,5) NOT NULL,
  v           BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (symbol, timeframe, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- Signals produced by the engine (one row per evaluated closed bar
-- that fired; `direction` 0 rows are kept only when debug is on)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  symbol        VARCHAR(24) NOT NULL,
  timeframe     VARCHAR(8) NOT NULL,
  bar_ts        BIGINT NOT NULL,
  direction     TINYINT NOT NULL,          -- +1 buy, -1 sell, 0 none
  close_price   DECIMAL(18,5) NOT NULL,
  slope         DECIMAL(18,5) NOT NULL,
  ema           DECIMAL(18,5) NOT NULL,
  macd          DECIMAL(18,8) NOT NULL,
  trigger_type  VARCHAR(24) NOT NULL DEFAULT '',
  blocked_by    VARCHAR(48) NOT NULL DEFAULT '',
  config_hash   CHAR(12) NOT NULL DEFAULT '',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_bar (symbol, timeframe, bar_ts, config_hash),
  INDEX idx_recent (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- Orders actually placed per account
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  user_id       INT UNSIGNED NOT NULL,
  account_id    BIGINT UNSIGNED NOT NULL,
  signal_id     BIGINT UNSIGNED NULL,
  broker_ticket VARCHAR(48) NOT NULL DEFAULT '',
  symbol        VARCHAR(24) NOT NULL,
  side          ENUM('buy','sell') NOT NULL,
  lot           DECIMAL(10,2) NOT NULL,
  entry_price   DECIMAL(18,5) NOT NULL DEFAULT 0,
  sl            DECIMAL(18,5) NOT NULL DEFAULT 0,
  tp            DECIMAL(18,5) NOT NULL DEFAULT 0,
  exit_price    DECIMAL(18,5) NOT NULL DEFAULT 0,
  profit        DECIMAL(18,2) NOT NULL DEFAULT 0,
  status        ENUM('sending','open','closed','rejected') NOT NULL DEFAULT 'sending',
  reject_reason VARCHAR(190) NOT NULL DEFAULT '',
  is_addon      TINYINT(1) NOT NULL DEFAULT 0,
  opened_at     DATETIME NULL,
  closed_at     DATETIME NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_acc (account_id, status),
  INDEX idx_user (user_id, created_at),
  CONSTRAINT fk_tr_acc FOREIGN KEY (account_id) REFERENCES broker_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- Server -> app commands. The app polls /v1/sync and applies these.
-- target_user NULL = broadcast to everyone.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_commands (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  target_user   INT UNSIGNED NULL,
  type          ENUM('kill','pause','resume','reload_config','message','force_logout','force_update')
                  NOT NULL,
  payload       JSON NULL,
  created_by    INT UNSIGNED NULL,
  expires_at    DATETIME NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_target (target_user, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS command_receipts (
  command_id    BIGINT UNSIGNED NOT NULL,
  device_id     BIGINT UNSIGNED NOT NULL,
  applied_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (command_id, device_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- Audit trail — every admin action and every live-trading flip
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  actor_type    ENUM('admin','user','system') NOT NULL,
  actor_id      INT UNSIGNED NULL,
  action        VARCHAR(64) NOT NULL,
  detail        TEXT NULL,
  ip            VARCHAR(45) NOT NULL DEFAULT '',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_action (action, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- Engine heartbeat / lock so overlapping cron runs cannot double-fire
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS engine_state (
  k             VARCHAR(64) PRIMARY KEY,
  v             TEXT NULL,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------
-- Seed: the global strategy config mirrors the EA's input defaults
-- ---------------------------------------------------------------
INSERT INTO strategy_configs (scope, ref_id, payload) VALUES
('global','', JSON_OBJECT(
  'symbol','XAUUSD','timeframe','M5',
  'allow_buy',TRUE,'allow_sell',TRUE,'trading_enabled',FALSE,
  'entry_mode','slope_ema_cross',
  'slope_period',12,'slope_method',2,'slope_price',0,
  'ema_period',50,'macd_fast',12,'macd_slow',24,'macd_signal',9,
  'use_macd_filter',TRUE,'only_with_trend_ema',TRUE,
  'confirm_candles',1,'min_body_ratio',0.25,
  'use_chop_filter',TRUE,'min_ema_gap_boxes',0.8,
  'fixed_lot',0.01,'trades_per_signal',1,
  'use_scale_in',FALSE,'additional_trades',2,'pips_interval',300.0,
  'addon_lot',0.01,
  'swing_sl_buffer_pips',20.0,'take_profit_pips',500.0,
  'use_step_stop',TRUE,'step_stop_distance_pips',300.0,
  'use_break_even',TRUE,'break_even_pips',300.0,'break_even_lock_pips',200.0,
  'slippage_pips',20.0,
  'daily_profit_target',200.0,'daily_loss_limit',90.0,
  'max_drawdown_pct',12.0,'max_trades_per_day',14,'max_open_positions',3,
  'max_spread_pips',30.0,
  'prop_firm_mode',FALSE,'prop_daily_loss_pct',4.0,
  'prop_max_drawdown_pct',8.0,'prop_trailing_dd',FALSE,
  'use_loss_cooldown',TRUE,'cooldown_loss_streak',3,'cooldown_minutes',30,
  'only_trade_sessions',TRUE,
  'sess_sydney',FALSE,'sess_tokyo',FALSE,'sess_london',TRUE,'sess_newyork',TRUE,
  'no_overnight',TRUE,'flat_time','23:50',
  'news_filter',TRUE,'news_before_sec',3600,'news_after_sec',3600
))
ON DUPLICATE KEY UPDATE id = id;
