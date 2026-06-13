<?php
// ==========================================================================
//  Trevolto relay — LOCAL secrets  (copy this file to  config.local.php)
//
//  HOW TO USE
//    1. Rename / copy this file to  config.local.php  on the server.
//    2. Fill in your real MySQL credentials + long random keys below.
//    3. Upload ONLY config.local.php to the relay folder (e.g. via cPanel).
//
//  WHY
//    config.local.php is git-ignored and lives only on the server, so when you
//    re-upload the relay folder for an update it will NOT overwrite your live
//    credentials and take the relay offline (HTTP 500). config.php loads this
//    file first; anything defined here wins over the template defaults.
// ==========================================================================

// --- MySQL (create a DB + user in cPanel, then fill these) ---
define('DB_HOST', 'localhost');
define('DB_NAME', 'your_db_name');
define('DB_USER', 'your_db_user');
define('DB_PASS', 'your_db_password');

// --- Secrets (make these long + random) ---
define('SELLER_KEY', 'put_a_long_random_seller_key_here');
define('ADMIN_KEY',  'put_a_long_random_admin_key_here');

// Optional: override how long signals are kept (seconds).
// define('SIGNAL_TTL', 86400);
