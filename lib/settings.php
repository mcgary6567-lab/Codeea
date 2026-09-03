<?php
/**
 * Layered strategy config.
 *
 *   global  <  plan  <  user  <  account
 *
 * The Android app never authors these values — it renders them and may POST a
 * change request, which the API accepts only for keys listed in GS_USER_KEYS.
 * Everything else is admin-only. That is what "controlled from the server side"
 * actually means in practice: one resolver, one allow-list.
 */
declare(strict_types=1);

/** Keys a customer is permitted to change from the app. */
const GS_USER_KEYS = [
    'allow_buy', 'allow_sell',
    'fixed_lot',
    'take_profit_pips',
    'daily_profit_target', 'daily_loss_limit',
    'max_trades_per_day', 'max_open_positions',
    'only_trade_sessions',
    'sess_sydney', 'sess_tokyo', 'sess_london', 'sess_newyork',
    'no_overnight',
    'use_break_even',
];

/** Numeric bounds enforced on anything a user submits. */
const GS_USER_BOUNDS = [
    'fixed_lot'           => [0.01, 0.50],
    'take_profit_pips'    => [0.0, 5000.0],
    'daily_profit_target' => [0.0, 100000.0],
    'daily_loss_limit'    => [0.0, 100000.0],
    'max_trades_per_day'  => [0, 100],
    'max_open_positions'  => [0, 10],
];

function gs_cfg_row(string $scope, string $ref = ''): array
{
    $r = q1('SELECT payload FROM strategy_configs WHERE scope = ? AND ref_id = ?',
            [$scope, $ref]);
    if (!$r) return [];
    $d = json_decode((string)$r['payload'], true);
    return is_array($d) ? $d : [];
}

/**
 * Resolve the effective config for an account (or a bare user).
 * Returns the merged array plus a `_layers` note for the admin UI.
 */
function gs_resolve_config(?array $user = null, ?array $account = null): array
{
    $global = gs_cfg_row('global', '');
    $plan   = $user ? gs_cfg_row('plan', (string)$user['plan']) : [];
    $usr    = $user ? gs_cfg_row('user', (string)$user['id']) : [];
    $acct   = $account ? gs_cfg_row('account', (string)$account['id']) : [];

    $merged = array_merge($global, $plan, $usr, $acct);

    // Server-side master switches always win, whatever a lower layer said.
    if ($user && empty($user['trading_enabled']))      $merged['trading_enabled'] = false;
    if ($user && $user['status'] !== 'active')         $merged['trading_enabled'] = false;
    if ($account && !empty($account['halted']))        $merged['trading_enabled'] = false;

    $merged['_layers'] = [
        'global'  => array_keys($global),
        'plan'    => array_keys($plan),
        'user'    => array_keys($usr),
        'account' => array_keys($acct),
    ];
    return $merged;
}

/** Persist one layer, bumping its version. */
function gs_save_config(string $scope, string $ref, array $patch, ?int $adminId): void
{
    $cur = gs_cfg_row($scope, $ref);
    $new = array_merge($cur, $patch);
    unset($new['_layers']);
    q('INSERT INTO strategy_configs (scope, ref_id, payload, version, updated_by)
       VALUES (?,?,?,1,?)
       ON DUPLICATE KEY UPDATE
         payload    = VALUES(payload),
         version    = version + 1,
         updated_by = VALUES(updated_by)',
      [$scope, $ref, json_encode($new, JSON_UNESCAPED_SLASHES), $adminId]);
}

/**
 * Filter and clamp a patch submitted by the app.
 * @return array{clean: array, rejected: array}
 */
function gs_sanitise_user_patch(array $patch): array
{
    $clean = []; $rejected = [];
    foreach ($patch as $k => $v) {
        if (!in_array($k, GS_USER_KEYS, true)) { $rejected[] = $k; continue; }

        if (isset(GS_USER_BOUNDS[$k])) {
            if (!is_numeric($v)) { $rejected[] = $k; continue; }
            [$lo, $hi] = GS_USER_BOUNDS[$k];
            $n = (float)$v;
            $clean[$k] = max($lo, min($hi, $n));
            if (in_array($k, ['max_trades_per_day', 'max_open_positions'], true)) {
                $clean[$k] = (int)$clean[$k];
            }
        } else {
            $clean[$k] = (bool)filter_var($v, FILTER_VALIDATE_BOOLEAN);
        }
    }
    return ['clean' => $clean, 'rejected' => $rejected];
}

/** Strip internals before handing config to the app. */
function gs_public_config(array $cfg): array
{
    $out = $cfg;
    unset($out['_layers']);
    $out['_editable'] = GS_USER_KEYS;
    $out['_bounds']   = GS_USER_BOUNDS;
    return $out;
}
