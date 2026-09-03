<?php
/**
 * Thin MetaApi (metaapi.cloud) REST client.
 *
 * Chosen because customers keep their existing MT5 broker AND their prop-firm
 * account — neither can be migrated to a broker with a native API.
 *
 * Safety posture baked in here, not left to callers:
 *   - Order placement is refused unless  metaapi.enabled  is true.
 *   - A LIVE (non-demo) account additionally needs  engine.allow_live  AND
 *     the account's own  live_approved  flag. Demo always works.
 *   - Volume is clamped to  engine.max_lot_per_trade  no matter what the
 *     strategy config says.
 */
declare(strict_types=1);

function ma_cfg(): array { return gs_config()['metaapi'] ?? []; }

/** Low-level request. Returns [httpCode, decodedBody, errorString]. */
function ma_request(string $method, string $url, ?array $body = null,
                    int $timeout = 20): array
{
    $cfg = ma_cfg();
    $token = (string)($cfg['token'] ?? '');
    if ($token === '') return [0, null, 'metaapi_token_missing'];

    $ch = curl_init($url);
    $headers = [
        'auth-token: ' . $token,
        'Accept: application/json',
    ];
    $opts = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => $timeout,
        CURLOPT_CONNECTTIMEOUT => 8,
        CURLOPT_CUSTOMREQUEST  => $method,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
    ];
    if ($body !== null) {
        $headers[] = 'Content-Type: application/json';
        $opts[CURLOPT_POSTFIELDS] = json_encode($body, JSON_UNESCAPED_SLASHES);
    }
    $opts[CURLOPT_HTTPHEADER] = $headers;
    curl_setopt_array($ch, $opts);

    $raw  = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err  = curl_error($ch);
    curl_close($ch);

    if ($raw === false) return [0, null, $err ?: 'curl_failed'];
    $decoded = json_decode((string)$raw, true);
    return [$code, $decoded, $code >= 400 ? ('http_' . $code) : ''];
}

function ma_client_url(string $path): string
{
    $base = rtrim((string)(ma_cfg()['base'] ?? ''), '/');
    return $base . $path;
}
function ma_prov_url(string $path): string
{
    $base = rtrim((string)(ma_cfg()['prov'] ?? ''), '/');
    return $base . $path;
}

/* ==================================================================
 *  Provisioning
 * ================================================================ */

/**
 * Create (deploy) a MetaApi account for a broker login.
 * @return array{ok:bool, account_id?:string, error?:string}
 */
function ma_provision(array $acc, string $password): array
{
    $payload = [
        'login'            => (string)$acc['broker_login'],
        'password'         => $password,
        'name'             => 'gs-' . $acc['id'],
        'server'           => (string)$acc['broker_server'],
        'platform'         => (string)$acc['platform'],
        'magic'            => 443439061,
        'type'             => 'cloud-g2',
        'region'           => (string)(ma_cfg()['region'] ?? 'new-york'),
        'keywords'         => ['gold scalpers'],
    ];
    [$code, $body, $err] = ma_request('POST',
        ma_prov_url('/users/current/accounts'), $payload, 40);

    if ($err !== '' || !is_array($body)) {
        return ['ok' => false, 'error' => ma_reason($body, $err)];
    }
    $id = (string)($body['id'] ?? '');
    if ($id === '') return ['ok' => false, 'error' => 'no_account_id'];
    return ['ok' => true, 'account_id' => $id];
}

function ma_account_state(string $accountId): array
{
    [$code, $body, $err] = ma_request('GET',
        ma_prov_url('/users/current/accounts/' . rawurlencode($accountId)));
    if ($err !== '' || !is_array($body)) {
        return ['ok' => false, 'error' => ma_reason($body, $err)];
    }
    return ['ok' => true, 'state' => (string)($body['state'] ?? ''),
            'connection' => (string)($body['connectionStatus'] ?? '')];
}

function ma_remove(string $accountId): bool
{
    [, , $err] = ma_request('DELETE',
        ma_prov_url('/users/current/accounts/' . rawurlencode($accountId)), null, 30);
    return $err === '';
}

/* ==================================================================
 *  Market data + account state
 * ================================================================ */

/**
 * Historical candles, oldest-first, normalised to the engine's bar shape.
 * @return array<int, array{ts:int,o:float,h:float,l:float,c:float,v:int}>
 */
function ma_candles(string $accountId, string $symbol, string $tf,
                    int $limit = 400): array
{
    $url = ma_client_url(sprintf(
        '/users/current/accounts/%s/historical-market-data/symbols/%s/timeframes/%s/candles?limit=%d',
        rawurlencode($accountId), rawurlencode($symbol), rawurlencode(strtolower($tf)),
        max(10, min(1000, $limit))
    ));
    [, $body, $err] = ma_request('GET', $url, null, 30);
    if ($err !== '' || !is_array($body)) return [];

    $out = [];
    foreach ($body as $c) {
        if (!isset($c['time'], $c['open'], $c['close'])) continue;
        $out[] = [
            'ts' => strtotime((string)$c['time']) ?: 0,
            'o'  => (float)$c['open'],
            'h'  => (float)($c['high'] ?? $c['open']),
            'l'  => (float)($c['low']  ?? $c['open']),
            'c'  => (float)$c['close'],
            'v'  => (int)($c['tickVolume'] ?? 0),
        ];
    }
    usort($out, static fn($a, $b) => $a['ts'] <=> $b['ts']);
    return $out;
}

function ma_account_information(string $accountId): ?array
{
    [, $body, $err] = ma_request('GET', ma_client_url(
        '/users/current/accounts/' . rawurlencode($accountId) . '/account-information'));
    return ($err === '' && is_array($body)) ? $body : null;
}

function ma_positions(string $accountId): array
{
    [, $body, $err] = ma_request('GET', ma_client_url(
        '/users/current/accounts/' . rawurlencode($accountId) . '/positions'));
    return ($err === '' && is_array($body)) ? $body : [];
}

function ma_symbol_price(string $accountId, string $symbol): ?array
{
    [, $body, $err] = ma_request('GET', ma_client_url(sprintf(
        '/users/current/accounts/%s/symbols/%s/current-price',
        rawurlencode($accountId), rawurlencode($symbol))));
    return ($err === '' && is_array($body)) ? $body : null;
}

/* ==================================================================
 *  Order flow
 * ================================================================ */

/**
 * Place a market order.
 *
 * @param array $acc broker_accounts row (needs is_demo, live_approved)
 * @return array{ok:bool, ticket?:string, price?:float, error?:string}
 */
function ma_market_order(array $acc, bool $isBuy, float $volume,
                         float $sl, float $tp, string $comment = 'GS'): array
{
    $cfg    = gs_config();
    $engine = $cfg['engine'] ?? [];

    if (empty(ma_cfg()['enabled'])) {
        return ['ok' => false, 'error' => 'execution_disabled'];
    }

    $isDemo = !empty($acc['is_demo']);
    if (!$isDemo) {
        if (empty($engine['allow_live'])) {
            return ['ok' => false, 'error' => 'live_disabled_globally'];
        }
        if (empty($acc['live_approved'])) {
            return ['ok' => false, 'error' => 'account_not_live_approved'];
        }
    }

    // Hard clamp — the strategy config is never trusted with size.
    $maxLot = (float)($engine['max_lot_per_trade'] ?? 0.5);
    if ($volume <= 0) return ['ok' => false, 'error' => 'bad_volume'];
    $volume = min($volume, $maxLot);

    $payload = [
        'actionType' => $isBuy ? 'ORDER_TYPE_BUY' : 'ORDER_TYPE_SELL',
        'symbol'     => (string)$acc['symbol'],
        'volume'     => round($volume, 2),
        'comment'    => substr($comment, 0, 26),
        'magic'      => 443439061,
    ];
    if ($sl > 0) $payload['stopLoss']   = $sl;
    if ($tp > 0) $payload['takeProfit'] = $tp;

    [, $body, $err] = ma_request('POST', ma_client_url(
        '/users/current/accounts/' . rawurlencode((string)$acc['metaapi_account_id']) . '/trade'),
        $payload, 30);

    if ($err !== '' || !is_array($body)) {
        return ['ok' => false, 'error' => ma_reason($body, $err)];
    }
    $code = (string)($body['stringCode'] ?? $body['numericCode'] ?? '');
    if ($code !== '' && stripos($code, 'ERR') !== false) {
        return ['ok' => false, 'error' => $code];
    }
    return [
        'ok'     => true,
        'ticket' => (string)($body['positionId'] ?? $body['orderId'] ?? ''),
        'price'  => (float)($body['price'] ?? 0),
    ];
}

function ma_close_position(string $accountId, string $positionId): array
{
    [, $body, $err] = ma_request('POST', ma_client_url(
        '/users/current/accounts/' . rawurlencode($accountId) . '/trade'), [
            'actionType' => 'POSITION_CLOSE_ID',
            'positionId' => $positionId,
        ], 30);
    return $err === ''
        ? ['ok' => true]
        : ['ok' => false, 'error' => ma_reason($body, $err)];
}

function ma_modify_position(string $accountId, string $positionId,
                            float $sl, float $tp): array
{
    $p = ['actionType' => 'POSITION_MODIFY', 'positionId' => $positionId];
    if ($sl > 0) $p['stopLoss']   = $sl;
    if ($tp > 0) $p['takeProfit'] = $tp;
    [, $body, $err] = ma_request('POST', ma_client_url(
        '/users/current/accounts/' . rawurlencode($accountId) . '/trade'), $p, 30);
    return $err === ''
        ? ['ok' => true]
        : ['ok' => false, 'error' => ma_reason($body, $err)];
}

/** Extract a human-usable reason without ever echoing credentials back. */
function ma_reason($body, string $fallback): string
{
    if (is_array($body)) {
        foreach (['message', 'error', 'stringCode'] as $k) {
            if (!empty($body[$k]) && is_string($body[$k])) {
                return substr($body[$k], 0, 180);
            }
        }
    }
    return $fallback !== '' ? $fallback : 'unknown_error';
}
