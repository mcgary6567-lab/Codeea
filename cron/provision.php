<?php
/**
 * Deploys newly-linked broker accounts to MetaApi and promotes them to
 * `connected` once the broker connection is actually up.
 *
 *   */5 * * * * /usr/bin/php /home/USER/domains/app.goldscalpers.com/public_html/cron/provision.php >> /home/USER/gs-provision.log 2>&1
 *
 * Kept out of engine.php on purpose: provisioning is slow and occasionally
 * hangs, and it must never delay a trading tick.
 *
 * Lifecycle:  pending -> deploying -> connected
 *             disabled  (customer switch off; MetaApi side is undeployed, not
 *                        deleted, so re-enabling never needs the password again)
 *             error     (provisioning failed; customer toggles off/on to retry)
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') { http_response_code(403); exit("cli only\n"); }

require_once __DIR__ . '/../lib/bootstrap.php';
require_once __DIR__ . '/../lib/crypto.php';
require_once __DIR__ . '/../lib/metaapi.php';
require_once __DIR__ . '/../lib/push.php';

function plog(string $m): void
{
    $line = '[' . gmdate('Y-m-d H:i:s') . '] ' . $m;
    fwrite(STDOUT, $line . "\n");
    gs_log_append('provision', $line);
}

// Heartbeat for the admin dashboard, recorded even when we exit early below.
q("INSERT INTO engine_state (k, v) VALUES ('provision_last_run', ?)
   ON DUPLICATE KEY UPDATE v = VALUES(v)", [gs_now()]);

/* --- sanity: "connected" without a MetaApi account is impossible ---- */
// (An old resume path could mark accounts connected by hand.)
q("UPDATE broker_accounts
      SET status = 'pending', status_detail = 'queued'
    WHERE status = 'connected' AND metaapi_account_id IS NULL");

/* --- 0. nothing can happen until execution is configured ---------- */
$tok = (string)(ma_cfg()['token'] ?? '');
if ($tok === '' || $tok === 'CHANGEME') {
    // Leave accounts in `pending` with a detail the app turns into a plain
    // sentence, and retry automatically once the token exists.
    q("UPDATE broker_accounts
          SET status = 'pending', status_detail = 'execution_not_configured'
        WHERE status IN ('pending', 'deploying')
           OR (status = 'error' AND status_detail IN ('metaapi_token_missing', 'execution_not_configured'))");
    plog('MetaApi token not set - accounts left pending');
    exit(0);
}

/* --- 1. brand-new (or retried) accounts -> create on MetaApi ------- */
foreach (qall("SELECT * FROM broker_accounts
                WHERE status = 'pending' AND metaapi_account_id IS NULL
                ORDER BY id LIMIT 10") as $acc) {

    $pass = gs_decrypt($acc['enc_password']);
    if ($pass === null) {
        q("UPDATE broker_accounts
              SET status = 'error', status_detail = 'credential_decrypt_failed'
            WHERE id = ?", [$acc['id']]);
        plog("acc#{$acc['id']} credential decrypt failed (app_key rotated?)");
        continue;
    }

    $res = ma_provision($acc, $pass);
    if (empty($res['ok'])) {
        q("UPDATE broker_accounts SET status = 'error', status_detail = ? WHERE id = ?",
          [substr((string)$res['error'], 0, 240), $acc['id']]);
        plog("acc#{$acc['id']} provision failed: " . $res['error']);
        continue;
    }

    q("UPDATE broker_accounts
          SET metaapi_account_id = ?, status = 'deploying', status_detail = ''
        WHERE id = ?", [$res['account_id'], $acc['id']]);
    gs_audit('system', null, 'account_provisioned',
             ['account' => $acc['id'], 'metaapi' => $res['account_id']]);
    plog("acc#{$acc['id']} provisioned -> {$res['account_id']}");
}

/* --- 1b. re-enabled accounts that already exist on MetaApi --------- */
foreach (qall("SELECT * FROM broker_accounts
                WHERE status = 'pending' AND metaapi_account_id IS NOT NULL
                ORDER BY id LIMIT 10") as $acc) {
    q("UPDATE broker_accounts SET status = 'deploying', status_detail = '' WHERE id = ?", [$acc['id']]);
}

/* --- 2. deploying -> connected once the broker link is up ---------- */
foreach (qall("SELECT * FROM broker_accounts
                WHERE status = 'deploying' AND metaapi_account_id IS NOT NULL
                ORDER BY id LIMIT 20") as $acc) {

    $maId = (string)$acc['metaapi_account_id'];
    $st = ma_account_state($maId);
    if (empty($st['ok'])) {
        q("UPDATE broker_accounts SET status_detail = ? WHERE id = ?",
          [substr((string)$st['error'], 0, 240), $acc['id']]);
        continue;
    }

    // An account we undeployed when the customer switched it off, or one
    // MetaApi never started, has to be (re)deployed before it can connect.
    if (in_array($st['state'], ['UNDEPLOYED', 'CREATED'], true)) {
        ma_deploy($maId);
        q("UPDATE broker_accounts SET status_detail = 'deploying' WHERE id = ?", [$acc['id']]);
        plog("acc#{$acc['id']} deploy requested");
        continue;
    }

    $deployed  = $st['state'] === 'DEPLOYED';
    $connected = stripos($st['connection'], 'CONNECTED') !== false;

    if ($deployed && $connected) {
        $info = ma_account_information($maId);
        $bal  = (float)($info['balance'] ?? 0);
        $eq   = (float)($info['equity'] ?? 0);
        // Trust the broker's own view of demo vs real over what the user ticked.
        $isDemo = isset($info['type'])
            ? (stripos((string)$info['type'], 'DEMO') !== false)
            : (int)$acc['is_demo'];

        q("UPDATE broker_accounts
              SET status = 'connected', status_detail = '',
                  balance = ?, equity = ?, equity_peak = ?,
                  day_start_balance = ?, day_start_equity = ?, is_demo = ?,
                  last_sync = ?
            WHERE id = ?",
          [$bal, $eq, $eq, $bal, $eq, $isDemo ? 1 : 0, gs_now(), $acc['id']]);

        // The password is only needed to provision. Drop it afterwards.
        q("UPDATE broker_accounts SET enc_password = NULL WHERE id = ?", [$acc['id']]);

        gs_audit('system', null, 'account_connected',
                 ['account' => $acc['id'], 'demo' => $isDemo]);
        gs_push_user((int)$acc['user_id'], 'Account connected',
            'Your ' . ($isDemo ? 'demo' : 'live') . ' account is linked and ready.',
            ['type' => 'account_connected']);
        plog("acc#{$acc['id']} CONNECTED (demo=" . ($isDemo ? 1 : 0) . ")");
    } else {
        q("UPDATE broker_accounts SET status_detail = ? WHERE id = ?",
          [substr(strtolower($st['state'] . ' / ' . $st['connection']), 0, 240), $acc['id']]);
    }
}

/* --- 3. switched-off accounts: stop the MetaApi side, keep the record --- */
foreach (qall("SELECT * FROM broker_accounts
                WHERE status = 'disabled' AND metaapi_account_id IS NOT NULL
                  AND status_detail <> 'undeployed'
                LIMIT 10") as $acc) {
    if (ma_undeploy((string)$acc['metaapi_account_id'])) {
        q("UPDATE broker_accounts SET status_detail = 'undeployed' WHERE id = ?", [$acc['id']]);
        plog("acc#{$acc['id']} undeployed on MetaApi");
    }
}

plog('done');
