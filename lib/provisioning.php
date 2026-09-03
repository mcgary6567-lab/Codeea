<?php
/**
 * Account provisioning: pending -> deploying -> connected on MetaApi.
 *
 * One function, three callers: the provisioning cron, the engine (as a
 * fallback when that cron has not run for a while) and the admin dashboard's
 * "Run now" button. Idempotent and bounded, so overlapping callers are safe.
 *
 * Lifecycle:  pending -> deploying -> connected
 *             disabled  (customer switch off; MetaApi side is undeployed, not
 *                        deleted, so re-enabling never needs the password again)
 *             error     (provisioning failed; customer toggles off/on to retry)
 */
declare(strict_types=1);

require_once __DIR__ . '/bootstrap.php';
require_once __DIR__ . '/crypto.php';
require_once __DIR__ . '/metaapi.php';
require_once __DIR__ . '/push.php';

/**
 * @param callable|null $log  receives one line per event
 * @return array{provisioned:int, connected:int, errors:int, note:string}
 */
function gs_provision_run(?callable $log = null): array
{
    $say = static function (string $m) use ($log): void {
        gs_log_append('provision', '[' . gmdate('Y-m-d H:i:s') . '] ' . $m);
        if ($log) $log($m);
    };
    $out = ['provisioned' => 0, 'connected' => 0, 'errors' => 0, 'note' => ''];

    // Heartbeat first, so the dashboard can tell "never ran" from "ran and did nothing".
    q("INSERT INTO engine_state (k, v) VALUES ('provision_last_run', ?)
       ON DUPLICATE KEY UPDATE v = VALUES(v)", [gs_now()]);

    /* --- sanity: "connected" without a MetaApi account is impossible --- */
    q("UPDATE broker_accounts
          SET status = 'pending', status_detail = 'queued'
        WHERE status = 'connected' AND metaapi_account_id IS NULL");

    /* --- 0. nothing can happen until execution is configured -------- */
    $tok = (string)(ma_cfg()['token'] ?? '');
    if ($tok === '' || $tok === 'CHANGEME') {
        q("UPDATE broker_accounts
              SET status = 'pending', status_detail = 'execution_not_configured'
            WHERE status IN ('pending', 'deploying')
               OR (status = 'error' AND status_detail IN ('metaapi_token_missing', 'execution_not_configured'))");
        $say('MetaApi token not set - accounts left pending');
        $out['note'] = 'MetaApi token not set';
        return $out;
    }

    /* --- 1. brand-new (or retried) accounts -> create on MetaApi ---- */
    foreach (qall("SELECT * FROM broker_accounts
                    WHERE status = 'pending' AND metaapi_account_id IS NULL
                    ORDER BY id LIMIT 10") as $acc) {

        $pass = gs_decrypt($acc['enc_password']);
        if ($pass === null) {
            q("UPDATE broker_accounts
                  SET status = 'error', status_detail = 'credential_decrypt_failed'
                WHERE id = ?", [$acc['id']]);
            $say("acc#{$acc['id']} credential decrypt failed (app_key rotated?)");
            $out['errors']++;
            continue;
        }

        $res = ma_provision($acc, $pass);
        if (empty($res['ok'])) {
            q("UPDATE broker_accounts SET status = 'error', status_detail = ? WHERE id = ?",
              [substr((string)$res['error'], 0, 240), $acc['id']]);
            $say("acc#{$acc['id']} provision failed: " . $res['error']);
            $out['errors']++;
            continue;
        }

        q("UPDATE broker_accounts
              SET metaapi_account_id = ?, status = 'deploying', status_detail = 'deploying'
            WHERE id = ?", [$res['account_id'], $acc['id']]);
        gs_audit('system', null, 'account_provisioned',
                 ['account' => $acc['id'], 'metaapi' => $res['account_id']]);
        $say("acc#{$acc['id']} provisioned -> {$res['account_id']}");
        $out['provisioned']++;
    }

    /* --- 1b. re-enabled accounts that already exist on MetaApi ------ */
    foreach (qall("SELECT id FROM broker_accounts
                    WHERE status = 'pending' AND metaapi_account_id IS NOT NULL
                    ORDER BY id LIMIT 10") as $acc) {
        q("UPDATE broker_accounts SET status = 'deploying', status_detail = 'deploying' WHERE id = ?", [$acc['id']]);
    }

    /* --- 2. deploying -> connected once the broker link is up -------- */
    foreach (qall("SELECT * FROM broker_accounts
                    WHERE status = 'deploying' AND metaapi_account_id IS NOT NULL
                    ORDER BY id LIMIT 20") as $acc) {

        $maId = (string)$acc['metaapi_account_id'];
        $st = ma_account_state($maId);
        if (empty($st['ok'])) {
            q("UPDATE broker_accounts SET status_detail = ? WHERE id = ?",
              [substr((string)$st['error'], 0, 240), $acc['id']]);
            $say("acc#{$acc['id']} state check failed: " . $st['error']);
            continue;
        }

        // An account we undeployed when the customer switched it off, or one
        // MetaApi never started, has to be (re)deployed before it can connect.
        if (in_array($st['state'], ['UNDEPLOYED', 'CREATED'], true)) {
            ma_deploy($maId);
            q("UPDATE broker_accounts SET status_detail = 'deploying' WHERE id = ?", [$acc['id']]);
            $say("acc#{$acc['id']} deploy requested");
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
            $say("acc#{$acc['id']} CONNECTED (demo=" . ($isDemo ? 1 : 0) . ", balance=$bal)");
            $out['connected']++;
        } else {
            q("UPDATE broker_accounts SET status_detail = ? WHERE id = ?",
              [substr(strtolower($st['state'] . ' / ' . $st['connection']), 0, 240), $acc['id']]);
            $say("acc#{$acc['id']} waiting: {$st['state']} / {$st['connection']}");
        }
    }

    /* --- 3. switched-off accounts: stop the MetaApi side, keep the record --- */
    foreach (qall("SELECT * FROM broker_accounts
                    WHERE status = 'disabled' AND metaapi_account_id IS NOT NULL
                      AND status_detail <> 'undeployed'
                    LIMIT 10") as $acc) {
        if (ma_undeploy((string)$acc['metaapi_account_id'])) {
            q("UPDATE broker_accounts SET status_detail = 'undeployed' WHERE id = ?", [$acc['id']]);
            $say("acc#{$acc['id']} undeployed on MetaApi");
        }
    }

    $say(sprintf('done: %d provisioned, %d connected, %d error(s)',
        $out['provisioned'], $out['connected'], $out['errors']));
    return $out;
}
