<?php
/**
 * AES-256-GCM for broker credentials at rest.
 *
 * Rules for this file:
 *   - Plaintext broker passwords never touch the log, the audit trail,
 *     an exception message, or an API response. Not once.
 *   - Rotating app_key invalidates every stored credential by design.
 */
declare(strict_types=1);

function gs_key(): string
{
    static $k = null;
    if ($k !== null) return $k;
    $raw = (string)(gs_config()['app_key'] ?? '');
    $bin = base64_decode($raw, true);
    if ($bin === false || strlen($bin) !== 32) {
        gs_fail(500, 'bad_app_key',
            'app_key must be 32 bytes, base64. Generate: openssl rand -base64 32');
    }
    $k = $bin;
    return $k;
}

function gs_encrypt(string $plain): string
{
    $iv  = random_bytes(12);
    $tag = '';
    $ct  = openssl_encrypt($plain, 'aes-256-gcm', gs_key(),
                           OPENSSL_RAW_DATA, $iv, $tag, '', 16);
    if ($ct === false) throw new RuntimeException('encrypt_failed');
    return base64_encode($iv . $tag . $ct);
}

function gs_decrypt(?string $blob): ?string
{
    if ($blob === null || $blob === '') return null;
    $raw = base64_decode($blob, true);
    if ($raw === false || strlen($raw) < 29) return null;
    $iv  = substr($raw, 0, 12);
    $tag = substr($raw, 12, 16);
    $ct  = substr($raw, 28);
    $pt  = openssl_decrypt($ct, 'aes-256-gcm', gs_key(), OPENSSL_RAW_DATA, $iv, $tag);
    return $pt === false ? null : $pt;
}

/** Opaque bearer token for the app. Returned once, stored only as a hash. */
function gs_new_token(): array
{
    $t = rtrim(strtr(base64_encode(random_bytes(32)), '+/', '-_'), '=');
    return ['token' => $t, 'hash' => hash('sha256', $t)];
}

function gs_hash_token(string $t): string { return hash('sha256', $t); }
