<?php
/**
 * Port of the EA's signal engine and trade-geometry helpers.
 *
 * Mirrors, line for line where practical:
 *   GetSignal()      -> gs_eval_signal()
 *   ConfirmOK()      -> gs_confirm_ok()
 *   AvgBarBody()     -> gs_avg_bar_body()
 *   SwingSLPrice()   -> gs_swing_sl()
 *
 * ── INDEXING ─────────────────────────────────────────────────────────
 * MQL series indexing has 0 = the currently-forming bar and 1 = the last
 * CLOSED bar. This engine only ever runs on closed bars, so there is no
 * bar 0 to read — and the EA never reads index 0 in the signal path
 * (lowest access is `f = 1 + ConfirmCandles`, i.e. >= 1). gs_ser() maps
 * MQL series index i onto the chronological array:
 *
 *     series[i]  ==  chrono[count - i]        (valid for i >= 1)
 *
 * Keeping the EA's own indices in the code below is deliberate: it makes
 * this file diffable against the .mq5 by eye.
 * ---------------------------------------------------------------------
 */
declare(strict_types=1);

require_once __DIR__ . '/indicators.php';

/** MQL series accessor over a chronological array. Returns null out of range. */
function gs_ser(array $chrono, int $i): ?float
{
    $idx = count($chrono) - $i;
    return ($idx >= 0 && $idx < count($chrono)) ? (float)$chrono[$idx] : null;
}

/** Same, for an array of bar rows. */
function gs_bar(array $bars, int $i): ?array
{
    $idx = count($bars) - $i;
    return ($idx >= 0 && $idx < count($bars)) ? $bars[$idx] : null;
}

/** Pip size. Gold on a 2-digit feed: 1 pip == 1 point == 0.01, so the EA's
 *  TakeProfitPips = 500 is a $5.00 target — matching the shipped defaults. */
function gs_pip_size(array $cfg, int $digits = 2): float
{
    if (!empty($cfg['pip_size'])) return (float)$cfg['pip_size'];
    return pow(10, -$digits);
}

/** AvgBarBody(n) — mean |close-open| over the last n CLOSED bars. */
function gs_avg_bar_body(array $bars, int $n): float
{
    $sum = 0.0; $c = 0;
    for ($i = 1; $i <= $n; $i++) {
        $b = gs_bar($bars, $i);
        if ($b === null) break;
        $body = abs((float)$b['c'] - (float)$b['o']);
        if ($body > 0.0) { $sum += $body; $c++; }
    }
    return $c > 0 ? $sum / $c : 0.0;
}

/** ConfirmOK() — cc consecutive bars in the trade direction + body-ratio gate. */
function gs_confirm_ok(array $bars, bool $buy, int $cc, float $minBodyRatio): bool
{
    for ($i = 1; $i <= $cc; $i++) {
        $b = gs_bar($bars, $i);
        if ($b === null) return false;
        $o = (float)$b['o']; $c = (float)$b['c'];
        if ($buy  && !($c > $o)) return false;
        if (!$buy && !($c < $o)) return false;
    }
    if ($minBodyRatio > 0.0) {
        $b1 = gs_bar($bars, 1);
        if ($b1 === null) return false;
        $o1 = (float)$b1['o']; $c1 = (float)$b1['c'];
        $h1 = (float)$b1['h']; $l1 = (float)$b1['l'];
        $range = max($h1 - $l1, 1e-9);
        if (abs($c1 - $o1) / $range < $minBodyRatio) return false;
    }
    return true;
}

/**
 * The signal engine.
 *
 * @param array $bars chronological closed bars (o,h,l,c,ts). Needs >= 200.
 * @param array $cfg  merged strategy config
 * @return array{direction:int,trigger:string,blocked_by:string,
 *               close:float,slope:float,ema:float,macd:float,
 *               slope_series:array,ema_series:array}
 */
function gs_eval_signal(array $bars, array $cfg): array
{
    $out = [
        'direction' => 0, 'trigger' => '', 'blocked_by' => '',
        'close' => 0.0, 'slope' => 0.0, 'ema' => 0.0, 'macd' => 0.0,
        'slope_chrono' => [], 'ema_chrono' => [],
    ];

    $cc   = max(0, (int)($cfg['confirm_candles'] ?? 1));
    $need = $cc + 5;
    if (count($bars) < max(200, $need + 60)) {
        $out['blocked_by'] = 'insufficient_history';
        return $out;
    }

    /* --- indicators ------------------------------------------------ */
    $close = array_map(static fn($b) => (float)$b['c'], $bars);

    $slopeC = gs_slope_line(
        $bars,
        (int)($cfg['slope_period'] ?? 12),
        (int)($cfg['slope_method'] ?? GS_MODE_SMMA),
        (int)($cfg['slope_price']  ?? 0)
    );
    $emaC = gs_ema_mt5($close, (int)($cfg['ema_period'] ?? 50));
    $mac  = gs_zerolag_macd(
        $close,
        (int)($cfg['macd_fast']   ?? 12),
        (int)($cfg['macd_slow']   ?? 24),
        (int)($cfg['macd_signal'] ?? 9)
    );
    $macdC = $mac['macd'];

    if (!$slopeC || !$macdC) { $out['blocked_by'] = 'indicator_warmup'; return $out; }

    $out['slope_chrono'] = $slopeC;
    $out['ema_chrono']   = $emaC;

    /* --- EA's GetSignal(), same indices ---------------------------- */
    $b1 = gs_bar($bars, 1);
    $c1 = (float)$b1['c'];
    $f  = 1 + $cc;

    $slope_f  = gs_ser($slopeC, $f);
    $slope_f1 = gs_ser($slopeC, $f + 1);
    $slope_f2 = gs_ser($slopeC, $f + 2);
    $ema_f    = gs_ser($emaC,   $f);
    $ema_f1   = gs_ser($emaC,   $f + 1);
    $ema_1    = gs_ser($emaC,   1);
    $macd_1   = gs_ser($macdC,  1);

    if ($slope_f2 === null || $ema_f1 === null || $macd_1 === null) {
        $out['blocked_by'] = 'indicator_warmup';
        return $out;
    }

    $out['close'] = $c1;
    $out['slope'] = $slope_f;
    $out['ema']   = $ema_1;
    $out['macd']  = $macd_1;

    $onlyTrend = !empty($cfg['only_with_trend_ema']);

    $upF  = $slope_f  > $slope_f1;
    $upFp = $slope_f1 > $slope_f2;

    $flipBuy  = ( $upF && !$upFp) && (!$onlyTrend || $c1 > $ema_1);
    $flipSell = (!$upF &&  $upFp) && (!$onlyTrend || $c1 < $ema_1);

    $crossBuy  = ($slope_f1 <= $ema_f1) && ($slope_f > $ema_f);
    $crossSell = ($slope_f1 >= $ema_f1) && ($slope_f < $ema_f);

    $mode = (string)($cfg['entry_mode'] ?? 'slope_ema_cross');
    if ($mode === 'slope_flip') {
        $buySig = $flipBuy;  $sellSig = $flipSell;
    } elseif ($mode === 'either') {
        $buySig = $flipBuy || $crossBuy;  $sellSig = $flipSell || $crossSell;
    } else { // slope_ema_cross (default)
        $buySig = $crossBuy; $sellSig = $crossSell;
    }

    if (!$buySig && !$sellSig) return $out;

    $out['trigger'] = $buySig
        ? ($crossBuy ? 'cross_buy' : 'flip_buy')
        : ($crossSell ? 'cross_sell' : 'flip_sell');

    /* --- chop filter ----------------------------------------------- */
    if (!empty($cfg['use_chop_filter'])) {
        $estBox = gs_avg_bar_body($bars, 10);
        $gap    = (float)($cfg['min_ema_gap_boxes'] ?? 0.8);
        if ($estBox > 0.0 && abs($c1 - $ema_1) < $gap * $estBox) {
            $out['blocked_by'] = 'chop_filter';
            return $out;
        }
    }

    $useMacd  = !empty($cfg['use_macd_filter']);
    $minBody  = (float)($cfg['min_body_ratio'] ?? 0.25);

    if ($buySig) {
        if (empty($cfg['allow_buy'])) { $out['blocked_by'] = 'buy_disabled'; return $out; }
        $macdOK = !$useMacd || $macd_1 > 0.0;
        if (!$macdOK)                             { $out['blocked_by'] = 'macd_filter';  return $out; }
        if (!gs_confirm_ok($bars, true, $cc, $minBody)) { $out['blocked_by'] = 'confirm'; return $out; }
        $out['direction'] = 1;
        return $out;
    }

    if ($sellSig) {
        if (empty($cfg['allow_sell'])) { $out['blocked_by'] = 'sell_disabled'; return $out; }
        $macdOK = !$useMacd || $macd_1 < 0.0;
        if (!$macdOK)                              { $out['blocked_by'] = 'macd_filter'; return $out; }
        if (!gs_confirm_ok($bars, false, $cc, $minBody)) { $out['blocked_by'] = 'confirm'; return $out; }
        $out['direction'] = -1;
        return $out;
    }

    return $out;
}

/**
 * SwingSLPrice() — nearest swing low/high beyond the 50 EMA.
 * Pass 0 requires the swing to sit the correct side of the EMA; pass 1 drops
 * that requirement. Returns 0.0 when nothing qualifies (caller must handle).
 */
function gs_swing_sl(array $bars, array $emaChrono, bool $isBuy): float
{
    $wing = 2; $look = 200;
    $avail = count($bars) - $wing - 1;
    if ($avail < 10) return 0.0;
    $maxS = min($look, $avail);

    for ($pass = 0; $pass < 2; $pass++) {
        for ($s = 1 + $wing; $s <= $maxS; $s++) {
            $bs = gs_bar($bars, $s);
            if ($bs === null) continue;
            $ema_s = gs_ser($emaChrono, $s);
            if ($ema_s === null) continue;

            if ($isBuy) {
                $lo = (float)$bs['l'];
                $swing = true;
                for ($w = 1; $w <= $wing && $swing; $w++) {
                    $a = gs_bar($bars, $s - $w);
                    $b = gs_bar($bars, $s + $w);
                    if ($a === null || $b === null) { $swing = false; break; }
                    if ((float)$a['l'] < $lo || (float)$b['l'] < $lo) $swing = false;
                }
                if ($swing && ($pass === 1 || $lo < $ema_s)) return $lo;
            } else {
                $hi = (float)$bs['h'];
                $swing = true;
                for ($w = 1; $w <= $wing && $swing; $w++) {
                    $a = gs_bar($bars, $s - $w);
                    $b = gs_bar($bars, $s + $w);
                    if ($a === null || $b === null) { $swing = false; break; }
                    if ((float)$a['h'] > $hi || (float)$b['h'] > $hi) $swing = false;
                }
                if ($swing && ($pass === 1 || $hi > $ema_s)) return $hi;
            }
        }
    }
    return 0.0;
}

/**
 * Compute SL/TP for an entry, mirroring the EA's geometry.
 * @return array{sl: float, tp: float, source: string}
 */
function gs_trade_levels(array $bars, array $emaChrono, bool $isBuy,
                         float $entry, array $cfg, int $digits = 2): array
{
    $pip    = gs_pip_size($cfg, $digits);
    $buf    = (float)($cfg['swing_sl_buffer_pips'] ?? 20.0) * $pip;
    $tpPips = (float)($cfg['take_profit_pips'] ?? 500.0);

    $swing = gs_swing_sl($bars, $emaChrono, $isBuy);
    $source = 'swing';

    if ($swing <= 0.0) {
        // EA falls back to a distance stop when no swing qualifies.
        $fallback = (float)($cfg['step_stop_distance_pips'] ?? 300.0) * $pip;
        $sl = $isBuy ? $entry - $fallback : $entry + $fallback;
        $source = 'fallback_distance';
    } else {
        $sl = $isBuy ? $swing - $buf : $swing + $buf;
    }

    $tp = 0.0;
    if ($tpPips > 0.0) $tp = $isBuy ? $entry + $tpPips * $pip : $entry - $tpPips * $pip;

    return [
        'sl'     => round($sl, $digits),
        'tp'     => round($tp, $digits),
        'source' => $source,
    ];
}

/* ====================================================================
 *  Session / time gates
 * ================================================================== */

/** Is `$hhmm` inside a window that may wrap midnight? */
function gs_in_window(int $mins, string $window): bool
{
    [$a, $b] = array_pad(explode('-', $window), 2, '');
    $toM = static function (string $s): int {
        [$h, $m] = array_pad(explode(':', trim($s)), 2, '0');
        return ((int)$h) * 60 + (int)$m;
    };
    $s = $toM($a); $e = $toM($b);
    return $s <= $e ? ($mins >= $s && $mins < $e) : ($mins >= $s || $mins < $e);
}

/**
 * Session gate. $serverTime is a DateTimeImmutable in BROKER SERVER time —
 * the caller is responsible for that conversion (see gs_server_time()).
 */
function gs_session_open(array $cfg, DateTimeImmutable $serverTime): bool
{
    if (empty($cfg['only_trade_sessions'])) return true;

    $mins = (int)$serverTime->format('H') * 60 + (int)$serverTime->format('i');
    $windows = [
        'sess_sydney'  => '21:00-06:00',
        'sess_tokyo'   => '00:00-09:00',
        'sess_london'  => '07:00-16:00',
        'sess_newyork' => '13:00-22:00',
    ];
    foreach ($windows as $key => $w) {
        if (!empty($cfg[$key]) && gs_in_window($mins, $w)) return true;
    }
    return false;
}

/** True once the server clock has reached the flatten time. */
function gs_past_flat_time(array $cfg, DateTimeImmutable $serverTime): bool
{
    if (empty($cfg['no_overnight'])) return false;
    $flat = (string)($cfg['flat_time'] ?? '23:50');
    [$h, $m] = array_pad(explode(':', $flat), 2, '0');
    $target = ((int)$h) * 60 + (int)$m;
    $mins = (int)$serverTime->format('H') * 60 + (int)$serverTime->format('i');
    return $mins >= $target;
}

/**
 * Broker server time. Most MT5 gold brokers run GMT+2/+3 (EET with DST).
 * Store the real offset per account once known; this is the default.
 */
function gs_server_time(int $offsetHours = 3): DateTimeImmutable
{
    return (new DateTimeImmutable('now', new DateTimeZone('UTC')))
        ->modify(sprintf('%+d hours', $offsetHours));
}

/** Stable short hash of the config, so signals can be attributed to a version. */
function gs_config_hash(array $cfg): string
{
    $c = $cfg;
    unset($c['_meta']);
    ksort($c);
    return substr(hash('sha256', json_encode($c)), 0, 12);
}
