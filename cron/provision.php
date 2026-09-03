<?php
/**
 * Deploys newly-linked broker accounts to MetaApi and promotes them to
 * `connected` once the broker connection is actually up.
 *
 *   */5 * * * * /usr/bin/php /home/USER/domains/app.goldscalpers.com/cron/provision.php >> /home/USER/gs-provision.log 2>&1
 *
 * Kept out of engine.php on purpose: provisioning is slow and occasionally
 * hangs, and it must never delay a trading tick.
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') { http_response_code(403); exit("cli only\n"); }

require_once __DIR__ . '/../lib/bootstrap.php';
require_once __DIR__ . '/../lib/crypto.php';
require_once __DIR__ . '/../lib/metaapi.php';
require_once __DIR__ . '/../lib/push.php';

function plog(string $m): void { fwrite(STDOUT, gmdate('H:i:s') . ' ' . $m . "\n"); }

/* --- 1. brand-new accounts -> create on MetaApi -------------------- */
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

/* --- 2. deploying -> connected once the broker link is up ---------- */
foreach (qall("SELECT * FROM broker_accounts
                WHERE status = 'deploying' AND metaapi_account_id IS NOT NULL
                ORDER BY id LIMIT 20") as $acc) {

    $st = ma_account_state((string)$acc['metaapi_account_id']);
    if (empty($st['ok'])) {
        q("UPDATE broker_accounts SET status_detail = ? WHERE id = ?",
          [substr((string)$st['error'], 0, 240), $acc['id']]);
        continue;
    }

    $deployed  = $st['state'] === 'DEPLOYED';
    $connected = stripos($st['connection'], 'CONNECTED') !== false;

    if ($deployed && $connected) {
        $info = ma_account_information((string)$acc['metaapi_account_id']);
        $bal  = (float)($info['balance'] ?? 0);
        $eq   = (float)($info['equity'] ?? 0);
        // Trust the broker's own view of demo vs real over what the user ticked.
        $isDemo = isset($info['type'])
            ? (stripos((string)$info['type'], 'DEMO') !== false)
            : (int)$acc['is_demo'];

        q("UPDATE broker_accounts
              SET status = 'connected', status_detail = '',
                  balance = ?, equity = ?, equity_peak = ?,
                  day_start_balance = ?, day_start_equity = ?, is_demo = ?
            WHERE id = ?",
          [$bal, $eq, $eq, $bal, $eq, $isDemo ? 1 : 0, $acc['id']]);

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
          [substr($st['state'] . ' / ' . $st['connection'], 0, 240), $acc['id']]);
    }
}

/* --- 3. tear down disabled accounts -------------------------------- */
foreach (qall("SELECT * FROM broker_accounts
                WHERE status = 'disabled' AND metaapi_account_id IS NOT NULL
                LIMIT 10") as $acc) {
    if (ma_remove((string)$acc['metaapi_account_id'])) {
        q("UPDATE broker_accounts SET metaapi_account_id = NULL WHERE id = ?", [$acc['id']]);
        plog("acc#{$acc['id']} removed from MetaApi");
    }
}

plog('done');
