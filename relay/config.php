<?php
// ==========================================================================
//  Prometheus relay — configuration  (EDIT THIS FILE, then upload the folder)
//
//  Upload everything in this folder so it serves at your subdomain, e.g.
//    https://hooks.prometheusai.tech/hook.php
//    https://hooks.prometheusai.tech/poll.php
//    https://hooks.prometheusai.tech/admin.php
//
//  Tables are created automatically on first request (no manual SQL import
//  needed) — just create a MySQL database + user in cPanel and fill these in.
// ==========================================================================

// --- MySQL (create a DB + user in cPanel, then fill these) ---
define('DB_HOST', 'localhost');
define('DB_NAME', 'CHANGE_ME_dbname');
define('DB_USER', 'CHANGE_ME_dbuser');
define('DB_PASS', 'CHANGE_ME_dbpass');

// --- Secrets (make these long + random) ---
// TradingView's webhook URL must include ?key=SELLER_KEY so ONLY your account
// can inject signals:  https://hooks.prometheusai.tech/hook.php?key=SELLER_KEY
define('SELLER_KEY', 'CHANGE_ME_long_random_seller_key');

// Used by admin.php to create / revoke customer licence tokens.
define('ADMIN_KEY', 'CHANGE_ME_long_random_admin_key');

// Delete signals older than this many seconds (keeps the table small).
define('SIGNAL_TTL', 86400);   // 24h
