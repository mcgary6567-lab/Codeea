<?php
/**
 * Firebase Cloud Messaging (HTTP v1) push.
 *
 * Uses a service-account JSON key and mints its own OAuth2 access token, so
 * there is no Composer dependency — this host has no package manager.
 * Silently no-ops when fcm.enabled is false, so nothing else has to guard it.
 */
declare(strict_types=1);

function gs_push_enabled(): bool
{
    $c = gs_config()['fcm'] ?? [];
    return !empty($c['enabled'])
        && !empty($c['project_id'])
        && is_readable((string)($c['service_account'] ?? ''));
}

/** Google OAuth2 access token from the service account (cached ~55 min). */
function gs_fcm_token(): ?string
{
    static $cache = null;
    if ($cache !== null && $cache['exp'] > time() + 60) return $cache['tok'];

    $c  = gs_config()['fcm'];
    $sa = json_decode((string)file_get_contents($c['service_account']), true);
    if (!is_array($sa) || empty($sa['private_key']) || empty($sa['client_email'])) {
        return null;
    }

    $now = time();
    $header  = ['alg' => 'RS256', 'typ' => 'JWT'];
    $claim   = [
        'iss'   => $sa['client_email'],
        'scope' => 'https://www.googleapis.com/auth/firebase.messaging',
        'aud'   => 'https://oauth2.googleapis.com/token',
        'iat'   => $now,
        'exp'   => $now + 3600,
    ];
    $b64 = static fn($d) => rtrim(strtr(base64_encode(json_encode($d)), '+/', '-_'), '=');
    $signingInput = $b64($header) . '.' . $b64($claim);

    $sig = '';
    if (!openssl_sign($signingInput, $sig, $sa['private_key'], OPENSSL_ALGO_SHA256)) {
        return null;
    }
    $jwt = $signingInput . '.' . rtrim(strtr(base64_encode($sig), '+/', '-_'), '=');

    $ch = curl_init('https://oauth2.googleapis.com/token');
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST           => true,
        CURLOPT_TIMEOUT        => 15,
        CURLOPT_POSTFIELDS     => http_build_query([
            'grant_type' => 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion'  => $jwt,
        ]),
    ]);
    $res = curl_exec($ch);
    curl_close($ch);

    $d = json_decode((string)$res, true);
    if (!is_array($d) || empty($d['access_token'])) return null;

    $cache = ['tok' => (string)$d['access_token'], 'exp' => $now + 3500];
    return $cache['tok'];
}

/**
 * Send to one device token.
 * @param array $data string-only data payload (FCM requirement)
 */
function gs_push_send(string $fcmToken, string $title, string $body,
                      array $data = []): bool
{
    if (!gs_push_enabled() || $fcmToken === '') return false;
    $tok = gs_fcm_token();
    if ($tok === null) return false;

    $project = gs_config()['fcm']['project_id'];
    $payload = [
        'message' => [
            'token'        => $fcmToken,
            'notification' => ['title' => $title, 'body' => $body],
            'data'         => array_map('strval', $data),
            'android'      => [
                'priority' => 'high',
                'notification' => ['channel_id' => 'gs_trades'],
            ],
        ],
    ];

    $ch = curl_init("https://fcm.googleapis.com/v1/projects/$project/messages:send");
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST           => true,
        CURLOPT_TIMEOUT        => 15,
        CURLOPT_HTTPHEADER     => [
            'Authorization: Bearer ' . $tok,
            'Content-Type: application/json',
        ],
        CURLOPT_POSTFIELDS     => json_encode($payload, JSON_UNESCAPED_SLASHES),
    ]);
    $res  = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    // A 404/400 usually means the install is gone — drop the stale token.
    if ($code === 404 || $code === 400) {
        q('UPDATE devices SET fcm_token = NULL WHERE fcm_token = ?', [$fcmToken]);
    }
    return $code >= 200 && $code < 300;
}

/** Push to every registered device of a user. */
function gs_push_user(int $userId, string $title, string $body, array $data = []): int
{
    $sent = 0;
    foreach (qall('SELECT fcm_token FROM devices
                   WHERE user_id = ? AND fcm_token IS NOT NULL', [$userId]) as $d) {
        if (gs_push_send((string)$d['fcm_token'], $title, $body, $data)) $sent++;
    }
    return $sent;
}
