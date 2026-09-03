<?php
/**
 * Mobile API. Everything the Android app talks to lives here.
 *
 * Auth: opaque bearer token in `Authorization: Bearer <token>`, stored only as
 * a SHA-256 hash. The app holds it in EncryptedSharedPreferences.
 *
 * Design rule: the app is a VIEW. It never decides whether to trade, what the
 * limits are, or whether it is allowed to run. It renders what /v1/sync says
 * and obeys the commands it is handed.
 */
declare(strict_types=1);

require_once __DIR__ . '/../lib/bootstrap.php';
require_once __DIR__ . '/../lib/crypto.php';
require_once __DIR__ . '/../lib/settings.php';
require_once __DIR__ . '/../lib/metaapi.php';
require_once __DIR__ . '/../lib/brokers.php';

header('X-Frame-Options: DENY');
header('Referrer-Policy: no-referrer');
header('Cache-Control: no-store');

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') {
    header('Allow: GET, POST');
    exit;
}

/* ------------------------------------------------------------------
 * Routing
 * ---------------------------------------------------------------- */
$path = (string)($_GET['r'] ?? '');
if ($path === '') {
    $uri  = parse_url((string)($_SERVER['REQUEST_URI'] ?? ''), PHP_URL_PATH) ?: '';
    $path = preg_replace('#^.*/v1/#', '', $uri) ?? '';
}
$path   = trim((string)$path, '/');
$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
$parts  = $path === '' ? [] : explode('/', $path);

/* ------------------------------------------------------------------
 * Auth helper
 * ---------------------------------------------------------------- */
function current_user(bool $required = true): ?array
{
    $hdr = '';
    foreach (['HTTP_AUTHORIZATION', 'REDIRECT_HTTP_AUTHORIZATION'] as $k) {
        if (!empty($_SERVER[$k])) { $hdr = (string)$_SERVER[$k]; break; }
    }
    if ($hdr === '' && function_exists('apache_request_headers')) {
        $h = apache_request_headers();
        $hdr = (string)($h['Authorization'] ?? $h['authorization'] ?? '');
    }
    if (!preg_match('/Bearer\s+(\S+)/i', $hdr, $m)) {
        if ($required) gs_fail(401, 'unauthorised', 'Missing bearer token.');
        return null;
    }

    $row = q1('SELECT u.*, t.id AS token_id, t.device_id
                 FROM auth_tokens t
                 JOIN users u ON u.id = t.user_id
                WHERE t.token_hash = ?
                  AND t.revoked_at IS NULL
                  AND t.expires_at > UTC_TIMESTAMP()',
              [gs_hash_token($m[1])]);

    if (!$row) {
        if ($required) gs_fail(401, 'unauthorised', 'Token invalid or expired.');
        return null;
    }
    if ($row['status'] === 'suspended') {
        gs_fail(403, 'suspended', 'This account has been suspended.');
    }
    q('UPDATE users SET last_seen = ? WHERE id = ?', [gs_now(), $row['id']]);
    return $row;
}

/* ==================================================================
 *  Routes
 * ================================================================ */
try {
    switch ($parts[0] ?? '') {

        /* ---------------- auth ---------------- */
        case 'auth':
            $action = $parts[1] ?? '';

            if ($action === 'register' && $method === 'POST') {
                $b     = gs_body();
                $email = strtolower(gs_str($b, 'email'));
                $pass  = gs_str($b, 'password');
                $name  = substr(gs_str($b, 'name'), 0, 120);

                if (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
                    gs_fail(422, 'bad_email', 'Enter a valid email address.');
                }
                if (strlen($pass) < 8) {
                    gs_fail(422, 'weak_password', 'Use at least 8 characters.');
                }
                if (!gs_rate_limit('reg:' . gs_ip(), 5, 3600)) {
                    gs_fail(429, 'rate_limited', 'Too many attempts. Try later.');
                }
                if (qval('SELECT 1 FROM users WHERE email = ?', [$email])) {
                    gs_fail(409, 'email_taken', 'That email is already registered.');
                }

                q('INSERT INTO users (email, pass_hash, name, status, plan)
                   VALUES (?,?,?, "active", "trial")',
                  [$email, password_hash($pass, PASSWORD_DEFAULT), $name]);
                $uid = (int)db()->lastInsertId();
                gs_audit('user', $uid, 'register', ['email' => $email]);
                issue_token($uid);
            }

            if ($action === 'login' && $method === 'POST') {
                $b     = gs_body();
                $email = strtolower(gs_str($b, 'email'));
                $pass  = gs_str($b, 'password');

                if (!gs_rate_limit('login:' . gs_ip(), 12, 900)) {
                    gs_fail(429, 'rate_limited', 'Too many attempts. Try again shortly.');
                }
                $u = q1('SELECT * FROM users WHERE email = ?', [$email]);
                // Constant-ish work either way, and one message for both cases.
                if (!$u || !password_verify($pass, (string)$u['pass_hash'])) {
                    gs_audit('user', $u['id'] ?? null, 'login_failed', ['email' => $email]);
                    gs_fail(401, 'bad_credentials', 'Email or password is incorrect.');
                }
                if ($u['status'] === 'suspended') {
                    gs_fail(403, 'suspended', 'This account has been suspended.');
                }
                gs_audit('user', (int)$u['id'], 'login');
                issue_token((int)$u['id']);
            }

            if ($action === 'logout' && $method === 'POST') {
                $u = current_user();
                q('UPDATE auth_tokens SET revoked_at = ? WHERE id = ?',
                  [gs_now(), $u['token_id']]);
                gs_ok(['message' => 'Signed out.']);
            }
            gs_fail(404, 'not_found');

        /* ---------------- device registration ---------------- */
        case 'device':
            if ($method !== 'POST') gs_fail(405, 'method_not_allowed');
            $u = current_user();
            $b = gs_body();
            $install = gs_str($b, 'install_id');
            if ($install === '') gs_fail(422, 'missing_install_id');

            q('INSERT INTO devices
                 (user_id, install_id, fcm_token, platform, app_version, os_version, model, last_seen)
               VALUES (?,?,?,?,?,?,?,?)
               ON DUPLICATE KEY UPDATE
                 fcm_token = VALUES(fcm_token), app_version = VALUES(app_version),
                 os_version = VALUES(os_version), model = VALUES(model),
                 last_seen = VALUES(last_seen)',
              [$u['id'], $install, gs_str($b, 'fcm_token') ?: null, 'android',
               substr(gs_str($b, 'app_version'), 0, 20),
               substr(gs_str($b, 'os_version'), 0, 20),
               substr(gs_str($b, 'model'), 0, 80), gs_now()]);
            gs_ok(['message' => 'Device registered.']);

        /* ---------------- the app's heartbeat ---------------- */
        case 'sync':
            $u = current_user();
            gs_ok(build_sync($u));

        /* ---------------- broker accounts ---------------- */
        case 'accounts':
            $u = current_user();

            if ($method === 'GET') {
                gs_ok(['accounts' => list_accounts((int)$u['id'])]);
            }

            if ($method === 'POST' && !isset($parts[1])) {
                $b      = gs_body();
                $login  = gs_str($b, 'login');
                $server = gs_str($b, 'server');
                $pass   = gs_str($b, 'password');
                $isDemo = gs_bool($b, 'is_demo', true);

                if ($login === '' || $server === '' || $pass === '') {
                    gs_fail(422, 'missing_fields',
                        'Login, server and investor/trading password are all required.');
                }
                if (qval('SELECT COUNT(*) FROM broker_accounts WHERE user_id = ?',
                         [$u['id']]) >= 3) {
                    gs_fail(409, 'too_many_accounts', 'Limit is 3 linked accounts.');
                }

                q('INSERT INTO broker_accounts
                     (user_id, label, platform, broker_login, broker_server,
                      enc_password, symbol, is_demo, status, status_detail)
                   VALUES (?,?,?,?,?,?,?,?, "pending", "queued")',
                  [$u['id'], substr(gs_str($b, 'label', 'MT5'), 0, 80),
                   gs_str($b, 'platform', 'mt5') === 'mt4' ? 'mt4' : 'mt5',
                   $login, $server, gs_encrypt($pass),
                   strtoupper(gs_str($b, 'symbol', 'XAUUSD')), $isDemo ? 1 : 0]);

                $accId = (int)db()->lastInsertId();
                gs_audit('user', (int)$u['id'], 'account_linked',
                         ['account' => $accId, 'server' => $server, 'demo' => $isDemo]);

                // Provisioning is slow; the cron picks pending accounts up.
                gs_ok([
                    'account_id' => $accId,
                    'status'     => 'pending',
                    'message'    => 'Account queued. Connection usually completes in a few minutes.',
                ]);
            }

            if ($method === 'POST' && isset($parts[1], $parts[2])) {
                $accId = (int)$parts[1];
                $acc = q1('SELECT * FROM broker_accounts WHERE id = ? AND user_id = ?',
                          [$accId, $u['id']]);
                if (!$acc) gs_fail(404, 'not_found');

                if ($parts[2] === 'disable') {
                    // The MetaApi side is undeployed by the provisioning cron;
                    // the password is never needed again for a re-enable.
                    q('UPDATE broker_accounts SET status = "disabled", status_detail = \'\'
                        WHERE id = ?', [$accId]);
                    gs_audit('user', (int)$u['id'], 'account_disabled', ['account' => $accId]);
                    gs_ok(['message' => 'Account disabled.']);
                }
                if ($parts[2] === 'resume') {
                    // Clearing a halt is deliberately allowed; enabling LIVE is not.
                    // A disabled account goes back through provisioning: "pending"
                    // if it never reached MetaApi, "deploying" if it did (the cron
                    // re-deploys it and promotes it to connected once the broker
                    // link is up). It is never marked connected by hand.
                    q('UPDATE broker_accounts
                          SET halted = 0, halt_reason = \'\',
                              status_detail = IF(status = "disabled", "queued", status_detail),
                              status = IF(status = "disabled",
                                          IF(metaapi_account_id IS NULL, "pending", "deploying"),
                                          IF(status = "error", "pending", status))
                        WHERE id = ?', [$accId]);
                    gs_audit('user', (int)$u['id'], 'account_resumed', ['account' => $accId]);
                    gs_ok(['message' => 'Account resumed.']);
                }
            }
            gs_fail(404, 'not_found');

        /* ---------------- settings ---------------- */
        case 'settings':
            $u = current_user();

            if ($method === 'GET') {
                $acc = q1('SELECT * FROM broker_accounts
                            WHERE user_id = ? ORDER BY id LIMIT 1', [$u['id']]);
                gs_ok(['config' => gs_public_config(gs_resolve_config($u, $acc ?: null))]);
            }

            if ($method === 'POST') {
                $res = gs_sanitise_user_patch(gs_body());
                if ($res['clean']) {
                    gs_save_config('user', (string)$u['id'], $res['clean'], null);
                    gs_audit('user', (int)$u['id'], 'settings_changed', $res['clean']);
                }
                $acc = q1('SELECT * FROM broker_accounts
                            WHERE user_id = ? ORDER BY id LIMIT 1', [$u['id']]);
                gs_ok([
                    'applied'  => $res['clean'],
                    'rejected' => $res['rejected'],
                    'config'   => gs_public_config(gs_resolve_config($u, $acc ?: null)),
                ]);
            }
            gs_fail(405, 'method_not_allowed');

        /* ---------------- trades / signals ---------------- */
        case 'trades':
            $u = current_user();
            $limit = max(1, min(200, (int)($_GET['limit'] ?? 50)));
            gs_ok(['trades' => qall(
                'SELECT id, symbol, side, lot, entry_price, sl, tp, exit_price,
                        profit, status, reject_reason, opened_at, closed_at, created_at
                   FROM trades WHERE user_id = ?
                  ORDER BY id DESC LIMIT ' . $limit, [$u['id']])]);

        case 'signals':
            current_user();
            $limit = max(1, min(100, (int)($_GET['limit'] ?? 30)));
            gs_ok(['signals' => qall(
                'SELECT symbol, timeframe, bar_ts, direction, close_price,
                        trigger_type, blocked_by, created_at
                   FROM signals
                  WHERE direction <> 0
                  ORDER BY id DESC LIMIT ' . $limit)]);

        case 'brokers':
            gs_ok(['brokers' => gs_broker_catalog()]);

        case 'ping':
            gs_ok(['time' => gs_now(), 'version' => '1.1.0']);

        default:
            gs_fail(404, 'not_found', 'Unknown endpoint.');
    }
} catch (Throwable $e) {
    if (!empty(gs_config()['debug'])) {
        gs_fail(500, 'server_error', $e->getMessage());
    }
    gs_audit('system', null, 'api_exception', substr($e->getMessage(), 0, 500));
    gs_fail(500, 'server_error', 'Something went wrong.');
}

/* ==================================================================
 *  Helpers
 * ================================================================ */

function issue_token(int $userId): void
{
    $ttl = (int)(gs_config()['token_ttl'] ?? 2592000);
    $t   = gs_new_token();
    q('INSERT INTO auth_tokens (user_id, token_hash, expires_at)
       VALUES (?,?, DATE_ADD(UTC_TIMESTAMP(), INTERVAL ? SECOND))',
      [$userId, $t['hash'], $ttl]);

    $u = q1('SELECT id, email, name, plan, plan_expires, status FROM users WHERE id = ?',
            [$userId]);
    gs_ok(['token' => $t['token'], 'expires_in' => $ttl, 'user' => $u]);
}

function list_accounts(int $userId): array
{
    return qall(
        'SELECT id, label, platform, broker_login, broker_server, symbol,
                is_demo, live_approved, status, status_detail, halted, halt_reason,
                balance, equity, day_trades, last_sync
           FROM broker_accounts WHERE user_id = ? ORDER BY id', [$userId]);
}

/**
 * The single call the app makes on a timer. Everything it needs to render,
 * plus any pending server commands.
 */
function build_sync(array $u): array
{
    $accounts = list_accounts((int)$u['id']);
    $primary  = $accounts[0] ?? null;
    $accRow   = $primary
        ? q1('SELECT * FROM broker_accounts WHERE id = ?', [$primary['id']])
        : null;

    $cfg = gs_resolve_config($u, $accRow);

    // Pending commands: targeted or broadcast, not yet expired.
    $cmds = qall(
        'SELECT id, type, payload, created_at
           FROM app_commands
          WHERE (target_user = ? OR target_user IS NULL)
            AND (expires_at IS NULL OR expires_at > UTC_TIMESTAMP())
            AND created_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 7 DAY)
          ORDER BY id DESC LIMIT 20', [$u['id']]);
    foreach ($cmds as &$c) {
        $c['payload'] = $c['payload'] ? json_decode((string)$c['payload'], true) : null;
    }
    unset($c);

    $openTrades = qall(
        'SELECT id, symbol, side, lot, entry_price, sl, tp, profit, opened_at
           FROM trades WHERE user_id = ? AND status = "open"
          ORDER BY id DESC', [$u['id']]);

    $today = qval(
        'SELECT COALESCE(SUM(profit),0) FROM trades
          WHERE user_id = ? AND DATE(created_at) = UTC_DATE()', [$u['id']], 0);

    $lastSignal = q1(
        'SELECT symbol, direction, close_price, trigger_type, blocked_by, created_at
           FROM signals ORDER BY id DESC LIMIT 1');

    // Headline numbers for the dashboard. Rejected trades never count.
    $st = q1(
        'SELECT COUNT(*)                                   AS total_trades,
                COALESCE(SUM(profit), 0)                   AS total_profit,
                COALESCE(SUM(status = "closed"), 0)        AS closed,
                COALESCE(SUM(status = "closed" AND profit > 0), 0) AS wins,
                COALESCE(SUM(DATE(created_at) = UTC_DATE()), 0)    AS today_trades,
                COALESCE(SUM(CASE WHEN created_at >= DATE_SUB(UTC_DATE(), INTERVAL WEEKDAY(UTC_DATE()) DAY)
                                  THEN profit ELSE 0 END), 0)      AS week_profit
           FROM trades WHERE user_id = ? AND status <> "rejected"', [$u['id']]) ?: [];
    $stats = [
        'total_trades' => (int)($st['total_trades'] ?? 0),
        'total_profit' => (float)($st['total_profit'] ?? 0),
        'closed'       => (int)($st['closed'] ?? 0),
        'wins'         => (int)($st['wins'] ?? 0),
        'today_trades' => (int)($st['today_trades'] ?? 0),
        'week_profit'  => (float)($st['week_profit'] ?? 0),
    ];

    return [
        'server_time' => gs_now(),
        'user' => [
            'id' => (int)$u['id'], 'email' => $u['email'], 'name' => $u['name'],
            'plan' => $u['plan'], 'plan_expires' => $u['plan_expires'],
            'status' => $u['status'],
            'trading_enabled' => (bool)$u['trading_enabled'],
        ],
        'accounts'     => $accounts,
        'config'       => gs_public_config($cfg),
        'commands'     => $cmds,
        'open_trades'  => $openTrades,
        'today_profit' => (float)$today,
        'last_signal'  => $lastSignal,
        'stats'        => $stats,
        'brokers'      => gs_broker_catalog(),
        'engine' => [
            'last_run' => qval("SELECT v FROM engine_state WHERE k = 'last_run'", [], null),
        ],
    ];
}
