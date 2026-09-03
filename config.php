<?php
/**
 * READY TO EDIT. app_key is already generated.
 * Fill in the four db values, then MOVE this file to
 *   /home/uXXXXXXXXX/domains/app.goldscalpers.com/.gs-app-config.php
 * (one level ABOVE public_html) and delete it from the web root.
 *
 * IMPORTANT (learned the hard way on this host): the Hostinger deploy WIPES
 * untracked files inside public_html. Put the real config.php ONE LEVEL ABOVE
 * the web root and let bootstrap.php find it there. Never commit config.php.
 */
return [
    // --- database -------------------------------------------------
    'db' => [
        'host' => 'localhost',
        'name' => 'goldscalpers_app',
        'user' => 'CHANGEME',
        'pass' => 'CHANGEME',
    ],

    // --- crypto ---------------------------------------------------
    // 32 random bytes, base64. Generate:  openssl rand -base64 32
    // Rotating this invalidates every stored broker password.
    'app_key' => 'CHANGEME-run: openssl rand -base64 32',

    // --- MetaApi (execution) --------------------------------------
    // https://app.metaapi.cloud  -> API access token
    'metaapi' => [
        'token'    => 'CHANGEME',
        'region'   => 'new-york',            // provisioning region
        'base'     => 'https://mt-client-api-v1.new-york.agiliumtrade.ai',
        'prov'     => 'https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai',
        'enabled'  => false,                 // master switch for real order flow
    ],

    // --- Firebase Cloud Messaging (push) --------------------------
    'fcm' => [
        'project_id'       => '',
        'service_account'  => '',            // absolute path to the JSON key
        'enabled'          => false,
    ],

    // --- engine ---------------------------------------------------
    'engine' => [
        // Hard ceiling regardless of what any config says. Belt and braces.
        'max_lot_per_trade'      => 0.50,
        'max_open_per_account'   => 5,
        'max_accounts_per_run'   => 50,
        'bar_history'            => 400,     // bars kept for indicator warm-up
        'lock_ttl_sec'           => 55,
        // Refuse to place a live order unless the account is explicitly
        // approved AND this is true. Demo is always allowed.
        'allow_live'             => false,
    ],

    // --- misc -----------------------------------------------------
    'app_url'    => 'https://app.goldscalpers.com',
    'token_ttl'  => 60 * 60 * 24 * 30,       // 30 days
    'debug'      => false,
];
