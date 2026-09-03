<?php
/**
 * Signal engine. Run once a minute from cron:
 *
 *   * * * * * /usr/bin/php /home/USER/domains/app.goldscalpers.com/cron/engine.php >> /home/USER/gs-engine.log 2>&1
 *
 * A minute cadence is plenty for an M5 strategy: it evaluates only on a NEW
 * CLOSED bar, so a bar is picked up within 60s of closing and processed once.
 *
 * Order of business each run:
 *   1. take the lock (overlapping runs must never double-fire)
 *   2. pull candles once per symbol/timeframe
 *   3. evaluate the signal once per distinct config, not once per account
 *   4. per account: roll the day, sync equity, manage open trades, then
 *      apply the risk gates and maybe enter
 *
 * Everything here is defensive by default. A missing config, an unreachable
 * broker or a malformed bar set results in "do nothing", never a guess.
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli' && empty($_GET['allow_web'])) {
    http_response_code(403);
    exit("cli only\n");
}

require_once __DIR__ . '/../lib/bootstrap.php';
require_once __DIR__ . '/../lib/crypto.php';
require_once __DIR__ . '/../lib/indicators.php';
require_once __DIR__ . '/../lib/strategy.php';
require_once __DIR__ . '/../lib/settings.php';
require_once __DIR__ . '/../lib/metaapi.php';
require_once __DIR__ . '/../lib/push.php';

$RUN_ID = substr(bin2hex(random_bytes(4)), 0, 8);

function elog(string $msg): void
{
    global $RUN_ID;
    $line = sprintf("[%s %s] %s", gmdate('Y-m-d H:i:s'), $RUN_ID, $msg);
    fwrite(STDOUT, $line . "\n");
    gs_log_append('engine', $line);
}

/* ==================================================================
 *  1. Lock
 * ================================================================ */
function engine_lock(int $ttl): bool
{
    $now = time();
    $row = q1("SELECT v FROM engine_state WHERE k = 'engine_lock'");
    if ($row && (int)$row['v'] > $now) return false;
    q("INSERT INTO engine_state (k, v) VALUES ('engine_lock', ?)
       ON DUPLICATE KEY UPDATE v = VALUES(v)", [(string)($now + $ttl)]);
    return true;
}
function engine_unlock(): void
{
    q("UPDATE engine_state SET v = '0' WHERE k = 'engine_lock'");
}

$engineCfg = gs_config()['engine'] ?? [];
if (!engine_lock((int)($engineCfg['lock_ttl_sec'] ?? 55))) {
    elog('another run holds the lock — exiting');
    exit(0);
}
register_shutdown_function('engine_unlock');

q("INSERT INTO engine_state (k, v) VALUES ('last_run', ?)
   ON DUPLICATE KEY UPDATE v = VALUES(v)", [gs_now()]);

/* ------------------------------------------------------------------
 *  1b. Provisioning fallback. The provisioning cron is the normal path;
 *  if it has not reported for 6 minutes (never created, or stalled) the
 *  engine runs one bounded pass itself so linked accounts still connect.
 * ---------------------------------------------------------------- */
$provLast = qval("SELECT v FROM engine_state WHERE k = 'provision_last_run'", [], null);
if (!$provLast || time() - strtotime((string)$provLast . ' UTC') > 360) {
    require_once __DIR__ . '/../lib/provisioning.php';
    $pr = gs_provision_run('elog');
    elog(sprintf('provisioning fallback: %d provisioned, %d connected, %d error(s)',
        $pr['provisioned'], $pr['connected'], $pr['errors']));
}

/* ==================================================================
 *  2. Accounts in scope
 * ================================================================ */
$accounts = qall(
    "SELECT a.*, u.plan, u.status AS user_status, u.trading_enabled AS user_enabled,
            u.plan_expires, u.email
       FROM broker_accounts a
       JOIN users u ON u.id = a.user_id
      WHERE a.status = 'connected'
        AND a.metaapi_account_id IS NOT NULL
        AND u.status = 'active'
      ORDER BY a.last_sync IS NULL DESC, a.last_sync ASC
      LIMIT " . (int)($engineCfg['max_accounts_per_run'] ?? 50));

if (!$accounts) { elog('no connected accounts'); exit(0); }
elog(sprintf('%d account(s) in scope', count($accounts)));

/* ==================================================================
 *  3. Bars + signal, memoised
 * ================================================================ */
$barCache    = [];   // "SYMBOL|TF"        => bars[]
$signalCache = [];   // "SYMBOL|TF|hash"   => signal[]

function get_bars(string $accountId, string $symbol, string $tf, int $limit): array
{
    global $barCache;
    $k = "$symbol|$tf";
    if (isset($barCache[$k])) return $barCache[$k];

    $bars = ma_candles($accountId, $symbol, $tf, $limit);

    // Drop the final candle if it is still forming: the engine only ever
    // evaluates closed bars, exactly like the EA reading index 1.
    $tfSec = tf_seconds($tf);
    if ($bars && $tfSec > 0) {
        $last = end($bars);
        if (time() - (int)$last['ts'] < $tfSec) array_pop($bars);
    }

    if ($bars) {
        // Persist for the admin chart + so a broker outage does not blind us.
        $st = db()->prepare(
            'INSERT INTO bars (symbol, timeframe, ts, o, h, l, c, v)
             VALUES (?,?,?,?,?,?,?,?)
             ON DUPLICATE KEY UPDATE o=VALUES(o), h=VALUES(h),
                                     l=VALUES(l), c=VALUES(c), v=VALUES(v)');
        foreach (array_slice($bars, -120) as $b) {
            $st->execute([$symbol, $tf, $b['ts'], $b['o'], $b['h'], $b['l'], $b['c'], $b['v']]);
        }
    }
    $barCache[$k] = $bars;
    return $bars;
}

function tf_seconds(string $tf): int
{
    return [
        'M1' => 60, 'M5' => 300, 'M15' => 900, 'M30' => 1800,
        'H1' => 3600, 'H4' => 14400, 'D1' => 86400,
    ][strtoupper($tf)] ?? 0;
}

function get_signal(array $bars, array $cfg, string $symbol, string $tf): array
{
    global $signalCache;
    $hash = gs_config_hash(array_intersect_key($cfg, array_flip([
        'slope_period','slope_method','slope_price','ema_period',
        'macd_fast','macd_slow','macd_signal','use_macd_filter',
        'only_with_trend_ema','confirm_candles','min_body_ratio',
        'use_chop_filter','min_ema_gap_boxes','entry_mode',
        'allow_buy','allow_sell',
    ])));
    $k = "$symbol|$tf|$hash";
    if (isset($signalCache[$k])) return $signalCache[$k];

    $sig = gs_eval_signal($bars, $cfg);
    $sig['config_hash'] = $hash;
    $signalCache[$k] = $sig;
    return $sig;
}

/* ==================================================================
 *  4. Per-account processing
 * ================================================================ */
foreach ($accounts as $acc) {
    try {
        process_account($acc);
    } catch (Throwable $e) {
        elog(sprintf('acc#%d EXCEPTION %s', $acc['id'], $e->getMessage()));
        q('UPDATE broker_accounts SET status_detail = ? WHERE id = ?',
          [substr($e->getMessage(), 0, 250), $acc['id']]);
    }
}
elog('done');


// ==================================================================

function process_account(array $acc): void
{
    global $engineCfg;

    $accId  = (int)$acc['id'];
    $maId   = (string)$acc['metaapi_account_id'];
    $user   = [
        'id' => (int)$acc['user_id'], 'plan' => $acc['plan'],
        'status' => $acc['user_status'], 'trading_enabled' => $acc['user_enabled'],
    ];
    $cfg    = gs_resolve_config($user, $acc);
    $symbol = (string)($cfg['symbol'] ?? $acc['symbol'] ?? 'XAUUSD');
    $tf     = strtoupper((string)($cfg['timeframe'] ?? 'M5'));

    /* --- live account information -------------------------------- */
    $info = ma_account_information($maId);
    if (!$info) {
        q("UPDATE broker_accounts
              SET status_detail = 'broker_unreachable', last_sync = ?
            WHERE id = ?", [gs_now(), $accId]);
        elog("acc#$accId broker unreachable");
        return;
    }
    $balance = (float)($info['balance'] ?? 0);
    $equity  = (float)($info['equity']  ?? 0);
    if ((string)$acc['status_detail'] === 'broker_unreachable') {
        q("UPDATE broker_accounts SET status_detail = '' WHERE id = ?", [$accId]);
    }

    /* --- day rollover -------------------------------------------- */
    $serverTime = gs_server_time((int)($cfg['server_offset_hours'] ?? 3));
    $dayKey     = $serverTime->format('Y-m-d');
    if ((string)$acc['day_key'] !== $dayKey) {
        q('UPDATE broker_accounts
              SET day_key = ?, day_start_equity = ?, day_start_balance = ?,
                  day_trades = 0, equity_peak = ?, halted = 0, halt_reason = \'\'
            WHERE id = ?',
          [$dayKey, $equity, $balance, max($equity, (float)$acc['equity_peak']), $accId]);
        $acc['day_key']           = $dayKey;
        $acc['day_start_equity']  = $equity;
        $acc['day_start_balance'] = $balance;
        $acc['day_trades']        = 0;
        $acc['halted']            = 0;
        elog("acc#$accId new trading day $dayKey");
    }

    $peak = max((float)$acc['equity_peak'], $equity);
    q('UPDATE broker_accounts
          SET balance = ?, equity = ?, equity_peak = ?, last_sync = ?
        WHERE id = ?', [$balance, $equity, $peak, gs_now(), $accId]);

    /* --- reconcile + manage what is already open ------------------ */
    $positions = ma_positions($maId);
    sync_positions($acc, $positions);
    manage_positions($acc, $cfg, $positions, $symbol);

    /* --- hard risk gates (mirror the EA) -------------------------- */
    $dayPnl = $equity - (float)$acc['day_start_equity'];

    $halt = null;
    if (!empty($cfg['daily_loss_limit']) && $dayPnl <= -abs((float)$cfg['daily_loss_limit'])) {
        $halt = 'daily_loss_limit';
    }
    if (!empty($cfg['daily_profit_target']) && $dayPnl >= abs((float)$cfg['daily_profit_target'])) {
        $halt = 'daily_profit_target';
    }
    if (!empty($cfg['max_drawdown_pct']) && $peak > 0) {
        $ddPct = ($peak - $equity) / $peak * 100.0;
        if ($ddPct >= (float)$cfg['max_drawdown_pct']) $halt = 'max_drawdown';
    }
    if (!empty($cfg['prop_firm_mode'])) {
        $dayStartBal = (float)$acc['day_start_balance'] ?: $balance;
        if ($dayStartBal > 0) {
            $lossPct = ($dayStartBal - $equity) / $dayStartBal * 100.0;
            if ($lossPct >= (float)($cfg['prop_daily_loss_pct'] ?? 4.0)) {
                $halt = 'prop_daily_loss';
            }
        }
        $ddBase = !empty($cfg['prop_trailing_dd']) ? $peak : (float)$acc['day_start_balance'];
        if ($ddBase > 0) {
            $ddPct = ($ddBase - $equity) / $ddBase * 100.0;
            if ($ddPct >= (float)($cfg['prop_max_drawdown_pct'] ?? 8.0)) {
                $halt = 'prop_max_drawdown';
            }
        }
    }

    if ($halt !== null) {
        halt_account($acc, $halt, $positions, $symbol);
        return;
    }

    /* --- flatten before end of day -------------------------------- */
    if (gs_past_flat_time($cfg, $serverTime) && $positions) {
        elog("acc#$accId flat time — closing " . count($positions));
        foreach ($positions as $p) ma_close_position($maId, (string)$p['id']);
        return;
    }

    /* --- soft gates ------------------------------------------------ */
    if (empty($cfg['trading_enabled']))      { return; }
    if (!gs_session_open($cfg, $serverTime)) { return; }

    if (!empty($acc['cooldown_until']) && strtotime((string)$acc['cooldown_until']) > time()) {
        return;
    }
    $maxDay = (int)($cfg['max_trades_per_day'] ?? 0);
    if ($maxDay > 0 && (int)$acc['day_trades'] >= $maxDay) return;

    $maxOpen = min(
        (int)($cfg['max_open_positions'] ?? 3) ?: 99,
        (int)($engineCfg['max_open_per_account'] ?? 5)
    );
    if (count($positions) >= $maxOpen) return;

    /* --- spread gate ------------------------------------------------ */
    $price = ma_symbol_price($maId, $symbol);
    if (!$price) return;
    $ask = (float)($price['ask'] ?? 0);
    $bid = (float)($price['bid'] ?? 0);
    if ($ask <= 0 || $bid <= 0) return;

    $digits = (int)($cfg['digits'] ?? 2);
    $pip    = gs_pip_size($cfg, $digits);
    $maxSpread = (float)($cfg['max_spread_pips'] ?? 0);
    if ($maxSpread > 0 && ($ask - $bid) / $pip > $maxSpread) {
        elog(sprintf('acc#%d spread %.2f pips > %.2f — skip',
             $accId, ($ask - $bid) / $pip, $maxSpread));
        return;
    }

    /* --- signal ----------------------------------------------------- */
    $bars = get_bars($maId, $symbol, $tf, (int)($engineCfg['bar_history'] ?? 400));
    if (count($bars) < 200) { elog("acc#$accId not enough bars"); return; }

    $lastBarTs = (int)end($bars)['ts'];
    if ((int)$acc['last_bar_ts'] === $lastBarTs) return;   // already handled

    $sig = get_signal($bars, $cfg, $symbol, $tf);

    q('UPDATE broker_accounts SET last_bar_ts = ? WHERE id = ?', [$lastBarTs, $accId]);

    // record the evaluation (unique per bar+config, so this dedupes itself)
    q('INSERT IGNORE INTO signals
         (symbol, timeframe, bar_ts, direction, close_price, slope, ema, macd,
          trigger_type, blocked_by, config_hash)
       VALUES (?,?,?,?,?,?,?,?,?,?,?)',
      [$symbol, $tf, $lastBarTs, $sig['direction'], $sig['close'],
       $sig['slope'], $sig['ema'], $sig['macd'],
       $sig['trigger'], $sig['blocked_by'], $sig['config_hash']]);

    if ($sig['direction'] === 0) return;

    $signalId = (int)qval(
        'SELECT id FROM signals WHERE symbol=? AND timeframe=? AND bar_ts=? AND config_hash=?',
        [$symbol, $tf, $lastBarTs, $sig['config_hash']], 0);

    /* --- enter ------------------------------------------------------ */
    $isBuy  = $sig['direction'] === 1;
    $entry  = $isBuy ? $ask : $bid;
    $levels = gs_trade_levels($bars, $sig['ema_chrono'], $isBuy, $entry, $cfg, $digits);

    $lot = (float)($cfg['fixed_lot'] ?? 0.01);
    $n   = max(1, min(10, (int)($cfg['trades_per_signal'] ?? 1)));

    for ($i = 0; $i < $n; $i++) {
        if (count($positions) + $i >= $maxOpen) break;

        q('INSERT INTO trades
             (user_id, account_id, signal_id, symbol, side, lot, sl, tp, status)
           VALUES (?,?,?,?,?,?,?,?, "sending")',
          [$acc['user_id'], $accId, $signalId ?: null, $symbol,
           $isBuy ? 'buy' : 'sell', $lot, $levels['sl'], $levels['tp']]);
        $tradeId = (int)db()->lastInsertId();

        $res = ma_market_order($acc, $isBuy, $lot, $levels['sl'], $levels['tp'],
                               'GS-' . $signalId);

        if (!empty($res['ok'])) {
            q('UPDATE trades
                  SET status = "open", broker_ticket = ?, entry_price = ?, opened_at = ?
                WHERE id = ?',
              [$res['ticket'] ?? '', $res['price'] ?? $entry, gs_now(), $tradeId]);
            q('UPDATE broker_accounts SET day_trades = day_trades + 1 WHERE id = ?', [$accId]);

            elog(sprintf('acc#%d ENTER %s %s %.2f @ %.2f sl=%.2f tp=%.2f (%s)',
                 $accId, $isBuy ? 'BUY' : 'SELL', $symbol, $lot,
                 $res['price'] ?? $entry, $levels['sl'], $levels['tp'], $levels['source']));

            gs_push_user((int)$acc['user_id'],
                sprintf('%s %s opened', $isBuy ? 'BUY' : 'SELL', $symbol),
                sprintf('%.2f lots at %.2f · SL %.2f · TP %.2f',
                        $lot, $res['price'] ?? $entry, $levels['sl'], $levels['tp']),
                ['type' => 'trade_open', 'trade_id' => $tradeId]);
        } else {
            q('UPDATE trades SET status = "rejected", reject_reason = ? WHERE id = ?',
              [substr((string)($res['error'] ?? 'unknown'), 0, 180), $tradeId]);
            elog(sprintf('acc#%d REJECT %s', $accId, $res['error'] ?? 'unknown'));
            break;   // do not hammer a broker that just refused us
        }
    }
}

/* ------------------------------------------------------------------
 * Reconcile local trade rows against what the broker actually holds.
 * ---------------------------------------------------------------- */
function sync_positions(array $acc, array $positions): void
{
    $live = [];
    foreach ($positions as $p) $live[(string)$p['id']] = $p;

    $open = qall('SELECT id, broker_ticket FROM trades
                   WHERE account_id = ? AND status = "open"', [$acc['id']]);

    foreach ($open as $t) {
        $tk = (string)$t['broker_ticket'];
        if ($tk !== '' && !isset($live[$tk])) {
            // Gone from the broker => closed. Profit is settled by the broker;
            // we record the close and let the history sync fill the number.
            q('UPDATE trades SET status = "closed", closed_at = ? WHERE id = ?',
              [gs_now(), $t['id']]);
        }
    }
    foreach ($open as $t) {
        $tk = (string)$t['broker_ticket'];
        if ($tk !== '' && isset($live[$tk])) {
            q('UPDATE trades SET profit = ? WHERE id = ?',
              [(float)($live[$tk]['profit'] ?? 0), $t['id']]);
        }
    }
}

/* ------------------------------------------------------------------
 * Break-even and step-stop, mirroring the EA's management.
 * ---------------------------------------------------------------- */
function manage_positions(array $acc, array $cfg, array $positions, string $symbol): void
{
    if (!$positions || empty($cfg['use_break_even'])) return;

    $maId   = (string)$acc['metaapi_account_id'];
    $digits = (int)($cfg['digits'] ?? 2);
    $pip    = gs_pip_size($cfg, $digits);
    $trigger = (float)($cfg['break_even_pips'] ?? 300.0) * $pip;
    $lock    = (float)($cfg['break_even_lock_pips'] ?? 200.0) * $pip;
    if ($trigger <= 0) return;

    foreach ($positions as $p) {
        if ((string)($p['symbol'] ?? '') !== $symbol) continue;

        $isBuy   = strtoupper((string)($p['type'] ?? '')) === 'POSITION_TYPE_BUY';
        $open    = (float)($p['openPrice'] ?? 0);
        $current = (float)($p['currentPrice'] ?? 0);
        $sl      = (float)($p['stopLoss'] ?? 0);
        if ($open <= 0 || $current <= 0) continue;

        $profit = $isBuy ? $current - $open : $open - $current;
        if ($profit < $trigger) continue;

        $target = $isBuy ? $open + $lock : $open - $lock;
        // Only ever tighten, never loosen.
        $improves = $sl <= 0
            || ($isBuy && $target > $sl)
            || (!$isBuy && $target < $sl);
        if (!$improves) continue;

        $r = ma_modify_position($maId, (string)$p['id'],
                                round($target, $digits),
                                (float)($p['takeProfit'] ?? 0));
        if (!empty($r['ok'])) {
            elog(sprintf('acc#%d BE lock %s -> %.2f', $acc['id'], $p['id'], $target));
        }
    }
}

/* ------------------------------------------------------------------
 * Halt: flatten, mark, notify, audit.
 * ---------------------------------------------------------------- */
function halt_account(array $acc, string $reason, array $positions, string $symbol): void
{
    $maId = (string)$acc['metaapi_account_id'];
    foreach ($positions as $p) ma_close_position($maId, (string)$p['id']);

    q("UPDATE broker_accounts SET halted = 1, halt_reason = ? WHERE id = ?",
      [$reason, $acc['id']]);

    gs_audit('system', null, 'account_halted',
             ['account' => $acc['id'], 'reason' => $reason]);

    gs_push_user((int)$acc['user_id'], 'Trading paused',
        'Your account hit the ' . str_replace('_', ' ', $reason)
        . '. Trading is paused until the next session.',
        ['type' => 'halt', 'reason' => $reason]);

    elog(sprintf('acc#%d HALT %s (closed %d)', $acc['id'], $reason, count($positions)));
}
