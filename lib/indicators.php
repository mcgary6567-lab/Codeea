<?php
/**
 * Faithful PHP port of the three bundled MQL5 indicators.
 *
 *   SlopeDirectionLine.mq5   -> gs_slope_line()      (EA reads buffer 2 = Main)
 *   ZeroLagMACD.mq5          -> gs_zerolag_macd()    (EA reads buffer 0 = MACD)
 *   GS_EMA50.mq5             -> gs_ema_mt5()         (EA reads buffer 0)
 *
 * ── PARITY NOTE — READ BEFORE CHANGING ANYTHING IN HERE ───────────────
 * The strategy uses TWO DIFFERENT EMAs and they are not interchangeable:
 *
 *   1. SlopeDirectionLine and ZeroLagMACD both carry their own private
 *      EMASeries() which seeds  out[0] = src[0].          -> gs_ema_seed_first()
 *   2. GS_EMA50 is a thin wrapper over the terminal's built-in iMA(),
 *      whose EMA is conventionally seeded from the SMA of the first
 *      `period` values.                                    -> gs_ema_mt5()
 *
 * The two seedings differ only during warm-up and converge exponentially.
 * With the 400-bar warm-up this engine uses, a 50-period EMA has converged
 * to far below tick resolution, so the choice cannot change a signal — but
 * only as long as the warm-up stays long. If you ever shorten `bar_history`,
 * re-run tools/parity_harness.py before shipping.
 *
 * Every function takes and returns CHRONOLOGICAL arrays (index 0 = oldest),
 * exactly like MQL's OnCalculate series. The strategy layer converts to
 * MQL's series indexing (0 = current bar) at the boundary.
 * ---------------------------------------------------------------------
 */
declare(strict_types=1);

const GS_MODE_SMA  = 0;
const GS_MODE_EMA  = 1;
const GS_MODE_SMMA = 2;
const GS_MODE_LWMA = 3;

/** MT4 applied-price numbering, as used by SlopeDirectionLine's `price` input. */
function gs_price_by_code(int $code, float $o, float $h, float $l, float $c): float
{
    switch ($code) {
        case 1:  return $o;
        case 2:  return $h;
        case 3:  return $l;
        case 4:  return ($h + $l) / 2.0;
        case 5:  return ($h + $l + $c) / 3.0;
        case 6:  return ($h + $l + 2.0 * $c) / 4.0;
        default: return $c;
    }
}

/** SMA with the MQL expanding-window warm-up (out[i] = mean of first i+1). */
function gs_sma(array $src, int $per): array
{
    $n = count($src); $out = []; $sum = 0.0;
    for ($i = 0; $i < $n; $i++) {
        $sum += $src[$i];
        if ($i >= $per) $sum -= $src[$i - $per];
        $out[$i] = $sum / ($i < $per ? $i + 1 : $per);
    }
    return $out;
}

/** EMA seeded out[0] = src[0]  — matches the indicators' private EMASeries(). */
function gs_ema_seed_first(array $src, int $per): array
{
    $n = count($src); if ($n === 0) return [];
    $a = 2.0 / ($per + 1.0);
    $out = [$src[0]];
    for ($i = 1; $i < $n; $i++) $out[$i] = $src[$i] * $a + $out[$i - 1] * (1.0 - $a);
    return $out;
}

/**
 * EMA seeded from the SMA of the first `per` values — the terminal's iMA.
 * Indices before per-1 are filled with the running SMA so the array stays
 * dense; they are inside the warm-up and never read by the strategy.
 */
function gs_ema_mt5(array $src, int $per): array
{
    $n = count($src); if ($n === 0) return [];
    if ($per < 1) $per = 1;
    $a = 2.0 / ($per + 1.0);
    $out = []; $sum = 0.0;
    $seed = min($per, $n);
    for ($i = 0; $i < $seed; $i++) { $sum += $src[$i]; $out[$i] = $sum / ($i + 1); }
    for ($i = $seed; $i < $n; $i++) $out[$i] = $src[$i] * $a + $out[$i - 1] * (1.0 - $a);
    return $out;
}

/** Wilder / smoothed MA, matching SMMASeries(). */
function gs_smma(array $src, int $per): array
{
    $n = count($src); if ($n === 0) return [];
    if ($per < 1) $per = 1;
    $seed = min($per, $n);
    $out = []; $sum = 0.0;
    for ($i = 0; $i < $seed; $i++) { $sum += $src[$i]; $out[$i] = $sum / ($i + 1); }
    $prev = $out[$seed - 1];
    for ($i = $seed; $i < $n; $i++) {
        $prev = ($prev * ($per - 1) + $src[$i]) / $per;
        $out[$i] = $prev;
    }
    return $out;
}

/** Linear weighted MA with the MQL expanding warm-up. */
function gs_lwma(array $src, int $per): array
{
    $n = count($src); $out = [];
    for ($i = 0; $i < $n; $i++) {
        $p = min($per, $i + 1);
        $num = 0.0; $den = 0.0;
        for ($k = 0; $k < $p; $k++) { $w = $p - $k; $num += $src[$i - $k] * $w; $den += $w; }
        $out[$i] = $den > 0.0 ? $num / $den : $src[$i];
    }
    return $out;
}

/** MASeries() dispatcher — note EMA here is the seed-first variant. */
function gs_ma_series(array $src, int $per, int $method): array
{
    if ($per < 1) $per = 1;
    switch ($method) {
        case GS_MODE_EMA:  return gs_ema_seed_first($src, $per);
        case GS_MODE_SMMA: return gs_smma($src, $per);
        case GS_MODE_LWMA: return gs_lwma($src, $per);
        default:           return gs_sma($src, $per);
    }
}

/**
 * SlopeDirectionLine main buffer (Hull-style MA).
 *   half = max(1, period/2)              [integer division, as in MQL]
 *   hp   = max(1, floor(sqrt(period)))
 *   raw  = 2*MA(price, half) - MA(price, period)
 *   main = MA(raw, hp)
 *
 * @param array $bars chronological rows with keys o,h,l,c
 * @return float[]    the Main buffer (EA's buffer 2), chronological
 */
function gs_slope_line(array $bars, int $period = 12, int $method = GS_MODE_SMMA,
                       int $priceCode = 0): array
{
    $n = count($bars);
    if ($n < $period + 5) return [];

    $pr = [];
    foreach ($bars as $i => $b) {
        $pr[$i] = gs_price_by_code($priceCode,
            (float)$b['o'], (float)$b['h'], (float)$b['l'], (float)$b['c']);
    }

    $half = max(1, intdiv($period, 2));
    $hp   = max(1, (int)floor(sqrt((float)$period)));

    $maFull = gs_ma_series($pr, $period, $method);
    $maHalf = gs_ma_series($pr, $half,   $method);

    $raw = [];
    for ($i = 0; $i < $n; $i++) $raw[$i] = 2.0 * $maHalf[$i] - $maFull[$i];

    return gs_ma_series($raw, $hp, $method);
}

/** ZLEMA(x,n) = 2*EMA(x,n) - EMA(EMA(x,n),n), seed-first EMA throughout. */
function gs_zerolag_ema(array $src, int $per): array
{
    $e1 = gs_ema_seed_first($src, $per);
    $e2 = gs_ema_seed_first($e1, $per);
    $out = [];
    for ($i = 0, $n = count($src); $i < $n; $i++) $out[$i] = 2.0 * $e1[$i] - $e2[$i];
    return $out;
}

/**
 * ZeroLagMACD.
 * @return array{macd: float[], signal: float[]} chronological
 */
function gs_zerolag_macd(array $close, int $fast = 12, int $slow = 24,
                         int $signal = 9): array
{
    $n = count($close);
    if ($n < $slow + $signal + 5) return ['macd' => [], 'signal' => []];

    $zlFast = gs_zerolag_ema($close, $fast);
    $zlSlow = gs_zerolag_ema($close, $slow);

    $macd = [];
    for ($i = 0; $i < $n; $i++) $macd[$i] = $zlFast[$i] - $zlSlow[$i];

    return ['macd' => $macd, 'signal' => gs_zerolag_ema($macd, $signal)];
}

/**
 * Convert a chronological array to MQL series indexing (0 = most recent).
 * The strategy layer reads `slope[1]`, `ema[f+1]` etc. exactly as the EA does,
 * so doing the flip once here keeps the ported logic line-for-line comparable.
 */
function gs_as_series(array $chrono): array
{
    return array_reverse(array_values($chrono));
}
