<?php
// ==========================================================================
//  Prometheus relay — configuration
//
//  Upload everything in this folder so it serves at your subdomain, e.g.
//    https://hooks.prometheusai.tech/hook.php
//    https://hooks.prometheusai.tech/poll.php
//    https://hooks.prometheusai.tech/panel.php
//
//  Tables are created automatically on first request (no manual SQL import
//  needed) — just create a MySQL database + user in cPanel and fill these in.
//
//  ── IMPORTANT: where to put your REAL credentials ──────────────────────────
//  Put your live DB password + secret keys in a file called  config.local.php
//  (copy config.local.sample.php). That file is git-ignored and is NOT part of
//  this repo, so RE-UPLOADING the folder can never overwrite your secrets and
//  knock the relay offline. Values set there win; the defaults below are only a
//  fallback/template so a fresh checkout doesn't fatal.
// ==========================================================================

// Load server-local overrides first. Whatever it define()s wins, because the
// lines below only define a constant if it isn't already set.
if (is_file(__DIR__ . '/config.local.php')) {
    require __DIR__ . '/config.local.php';
}

// --- MySQL (create a DB + user in cPanel; real values go in config.local.php) ---
defined('DB_HOST') || define('DB_HOST', 'localhost');
defined('DB_NAME') || define('DB_NAME', 'CHANGE_ME_dbname');
defined('DB_USER') || define('DB_USER', 'CHANGE_ME_dbuser');
defined('DB_PASS') || define('DB_PASS', 'CHANGE_ME_dbpass');

// --- Secrets (make these long + random; real values go in config.local.php) ---
// TradingView's webhook URL must include ?key=SELLER_KEY so ONLY your account
// can inject signals:  https://hooks.prometheusai.tech/hook.php?key=SELLER_KEY
defined('SELLER_KEY') || define('SELLER_KEY', 'CHANGE_ME_long_random_seller_key');

// Used by admin.php / panel.php to create / revoke customer licence tokens.
defined('ADMIN_KEY') || define('ADMIN_KEY', 'CHANGE_ME_long_random_admin_key');

// Delete signals older than this many seconds (keeps the table small).
defined('SIGNAL_TTL') || define('SIGNAL_TTL', 86400);   // 24h
