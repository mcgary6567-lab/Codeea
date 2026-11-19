<?php
/**
 * Lead Agent licence server: one PHP file, no database.
 *
 * Data lives in a private folder OUTSIDE the public website folder (…/licence-data/ next to
 * public_html), so it is never served and is never touched by the GitHub deploy:
 *   store.json   licences, computers, settings, admin login (password stored as a hash)
 *   events.log   one JSON line per event (activations, revokes, logins…)
 *   signing.pem  the private signing key, created on this server during setup; it never leaves it
 *
 * Lead Agent calls:  POST api/activate, api/check, api/release    GET api/health, api/public-key
 * The admin panel (index.html + admin.js) calls api/admin/* with a login session.
 * A pass is signed with ECDSA P-256 / SHA-256 (raw r||s), exactly what the extension verifies.
 */

declare(strict_types=1);
error_reporting(E_ALL);
ini_set('display_errors', '0');

const KEY_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'; // Crockford: no I, L, O, U
const FAIL_LIMIT = 30;           // wrong keys or passwords per IP per hour
const LOGIN_FAIL_LIMIT = 8;      // wrong admin passwords per IP per 15 minutes
const AUTO_ISSUE_LIMIT = 5;      // licences created automatically per IP per day
const SESSION_HOURS = 12;

// ---------------------------------------------------------------- storage
function data_dir(): string {
    static $dir = null;
    if ($dir !== null) return $dir;
    $env = getenv('LA_LICENCE_DATA');
    if ($env) { $dir = rtrim($env, '/\\'); }
    else {
        $root = rtrim((string)($_SERVER['DOCUMENT_ROOT'] ?? ''), '/\\');
        $dir = ($root !== '' ? dirname($root) : dirname(__DIR__, 2)) . DIRECTORY_SEPARATOR . 'licence-data';
    }
    if (!is_dir($dir)) @mkdir($dir, 0700, true);
    if (!is_dir($dir) || !is_writable($dir)) {
        // Fallback: a locked folder inside /licence/ (the .htaccess here refuses every request to it).
        $dir = __DIR__ . DIRECTORY_SEPARATOR . 'private-data';
        if (!is_dir($dir)) @mkdir($dir, 0700, true);
        if (!is_file($dir . '/.htaccess')) @file_put_contents($dir . '/.htaccess', "Require all denied\nDeny from all\n");
        if (!is_file($dir . '/index.html')) @file_put_contents($dir . '/index.html', '');
    }
    return $dir;
}

function default_store(): array {
    return [
        'version' => 1,
        'admin' => null,
        'licences' => [],
        'settings' => [
            'productName' => 'Lead Agent',
            'sellerName' => 'Ihsan Solo Ads',
            'supportEmail' => '',
            'defaultMaxDevices' => 2,
            'passDays' => 7,
            'activationsOpen' => true,
            'emailActivation' => true,
            'autoIssue' => true,
            'blockDisposable' => true,
            'requireEmailMatch' => false,
            'messageTemplate' => "Hi {name},\n\nThank you for buying {product}!\n\nInstall it, open it, and enter your name and this email address: {email}. It activates in a few seconds and works on {devices} computers.\n\nIf you ever need it, your licence key is: {key}\n\nAny questions, just reply to this email.\n\n{seller}",
        ],
        'rate' => [],
        'stats' => ['daily' => []],
    ];
}

/** Run $fn with the store locked; if it returns true the store is saved. */
function with_store(callable $fn) {
    $file = data_dir() . DIRECTORY_SEPARATOR . 'store.json';
    $fh = fopen($file, 'c+');
    if (!$fh) fail_out('storage', 'The licence server cannot write its data folder.', 500);
    flock($fh, LOCK_EX);
    $raw = stream_get_contents($fh);
    $store = $raw ? json_decode($raw, true) : null;
    if (!is_array($store)) $store = default_store();
    $store['settings'] = array_merge(default_store()['settings'], $store['settings'] ?? []);
    if (!isset($store['blocked']) || !is_array($store['blocked'])) $store['blocked'] = [];
    $GLOBALS['__blocked'] = $store['blocked'];
    // The first message template asked buyers to paste the key; swap it for the email one if unchanged.
    if (strpos((string) $store['settings']['messageTemplate'], "Open Lead Agent, enter your name, email and this key, and press Activate.") !== false
        && strpos((string) $store['settings']['messageTemplate'], 'Your licence key:') !== false) {
        $store['settings']['messageTemplate'] = default_store()['settings']['messageTemplate'];
    }
    $save = false;
    try {
        $result = $fn($store, $save);
    } finally {
        if ($save) {
            $tmp = json_encode($store, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
            ftruncate($fh, 0); rewind($fh); fwrite($fh, $tmp); fflush($fh);
        }
        flock($fh, LOCK_UN); fclose($fh);
    }
    return $result;
}

function read_store(): array { return with_store(function (&$s, &$save) { return $s; }); }

function log_event(string $type, array $detail = []): void {
    $line = json_encode(['t' => now_ms(), 'type' => $type] + $detail, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . "\n";
    @file_put_contents(data_dir() . DIRECTORY_SEPARATOR . 'events.log', $line, FILE_APPEND | LOCK_EX);
}

function read_events(int $limit = 500, ?string $key = null): array {
    $file = data_dir() . DIRECTORY_SEPARATOR . 'events.log';
    if (!is_file($file)) return [];
    $lines = @file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [];
    $out = [];
    for ($i = count($lines) - 1; $i >= 0 && count($out) < $limit; $i--) {
        $e = json_decode($lines[$i], true);
        if (!is_array($e)) continue;
        if ($key !== null && ($e['key'] ?? '') !== $key) continue;
        $out[] = $e;
    }
    return $out;
}

// ---------------------------------------------------------------- helpers
function now_ms(): int { return (int) floor(microtime(true) * 1000); }
function b64url(string $bin): string { return rtrim(strtr(base64_encode($bin), '+/', '-_'), '='); }

function send_json(array $body, int $status = 200): void {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    echo json_encode($body, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
}
function ok_out(array $body = []): void { send_json(['ok' => true] + $body); exit; }
function fail_out(string $code, string $error, int $status = 200): void { send_json(['ok' => false, 'code' => $code, 'error' => $error], $status); exit; }

function body(): array {
    static $b = null;
    if ($b === null) { $b = json_decode((string) file_get_contents('php://input'), true); if (!is_array($b)) $b = []; }
    return $b;
}
function clean_text($s, int $max = 120): string {
    $s = preg_replace('/[\x00-\x1f\x7f]/u', ' ', (string) ($s ?? ''));
    return mb_substr(trim((string) $s), 0, $max);
}
function clean_email($s): string {
    $e = mb_strtolower(clean_text($s, 160));
    return filter_var($e, FILTER_VALIDATE_EMAIL) ? $e : '';
}
function clean_device($d): string { $d = (string) ($d ?? ''); return preg_match('/^[0-9a-f-]{16,64}$/i', $d) ? strtolower($d) : ''; }

function normalize_key($k): string {
    $n = strtoupper(preg_replace('/[^0-9A-Za-z]/', '', (string) $k));
    $n = preg_replace('/^LA/', '', $n);
    return strtr($n, ['I' => '1', 'L' => '1', 'O' => '0', 'U' => 'V']);
}
function key_id($k): string { $n = normalize_key($k); return strlen($n) === 12 ? 'LA-' . substr($n, 0, 4) . '-' . substr($n, 4, 4) . '-' . substr($n, 8) : ''; }
function new_key(): string {
    $b = random_bytes(12); $c = '';
    for ($i = 0; $i < 12; $i++) $c .= KEY_ALPHABET[ord($b[$i]) % 32];
    return 'LA-' . substr($c, 0, 4) . '-' . substr($c, 4, 4) . '-' . substr($c, 8, 4);
}

function client_ip(): string { return (string) ($_SERVER['HTTP_CF_CONNECTING_IP'] ?? $_SERVER['REMOTE_ADDR'] ?? 'local'); }
function ip_tag(): string { return substr(hash('sha256', 'la-rl|' . client_ip()), 0, 16); } // the IP itself is not stored

function too_many(array &$store, string $bucket, int $limit, int $windowMs): bool {
    $now = now_ms(); $tag = $bucket . ':' . ip_tag();
    $list = array_values(array_filter($store['rate'][$tag] ?? [], fn($t) => $now - $t < $windowMs));
    $store['rate'][$tag] = $list;
    return count($list) >= $limit;
}
function count_fail(array &$store, string $bucket): void {
    $store['rate'][$bucket . ':' . ip_tag()][] = now_ms();
    // keep the rate table small
    if (count($store['rate']) > 2000) $store['rate'] = array_slice($store['rate'], -1000, null, true);
}

function bump_daily(array &$store, string $field): void {
    $d = gmdate('Y-m-d');
    $store['stats']['daily'][$d][$field] = ($store['stats']['daily'][$d][$field] ?? 0) + 1;
    if (count($store['stats']['daily']) > 400) $store['stats']['daily'] = array_slice($store['stats']['daily'], -370, null, true);
}

// ---------------------------------------------------------------- signing
function key_file(): string { return data_dir() . DIRECTORY_SEPARATOR . 'signing.pem'; }

function make_signing_key(): void {
    if (is_file(key_file())) return;
    $opts = ['private_key_type' => OPENSSL_KEYTYPE_EC, 'curve_name' => 'prime256v1'];
    $k = openssl_pkey_new($opts);
    if (!$k) { // some servers (PHP on Windows) need OpenSSL's settings file named
        foreach ([getenv('OPENSSL_CONF'), dirname(PHP_BINARY) . '/extras/ssl/openssl.cnf', '/etc/ssl/openssl.cnf', '/usr/lib/ssl/openssl.cnf'] as $cnf) {
            if ($cnf && is_file($cnf) && ($k = openssl_pkey_new($opts + ['config' => $cnf]))) { $opts['config'] = $cnf; break; }
        }
    }
    if (!$k || !openssl_pkey_export($k, $pem, null, isset($opts['config']) ? ['config' => $opts['config']] : [])) {
        fail_out('crypto', 'This server could not create a signing key (OpenSSL with EC support is needed).', 500);
    }
    file_put_contents(key_file(), $pem, LOCK_EX);
    @chmod(key_file(), 0600);
}

function public_jwk(): ?array {
    if (!is_file(key_file())) return null;
    $k = openssl_pkey_get_private((string) file_get_contents(key_file()));
    if (!$k) return null;
    $d = openssl_pkey_get_details($k);
    $x = str_pad($d['ec']['x'], 32, "\0", STR_PAD_LEFT);
    $y = str_pad($d['ec']['y'], 32, "\0", STR_PAD_LEFT);
    return ['kty' => 'EC', 'crv' => 'P-256', 'x' => b64url($x), 'y' => b64url($y)];
}

/** DER ECDSA signature -> raw 64-byte r||s (what WebCrypto expects). */
function der_to_raw(string $der): string {
    $pos = 2; if (ord($der[1]) & 0x80) $pos += ord($der[1]) & 0x7f;
    $read = function () use ($der, &$pos) { $len = ord($der[$pos + 1]); $v = substr($der, $pos + 2, $len); $pos += 2 + $len; return $v; };
    $r = ltrim($read(), "\0"); $s = ltrim($read(), "\0");
    return str_pad($r, 32, "\0", STR_PAD_LEFT) . str_pad($s, 32, "\0", STR_PAD_LEFT);
}

function sign_pass(array $payload): string {
    $body = b64url(json_encode($payload, JSON_UNESCAPED_SLASHES));
    $k = openssl_pkey_get_private((string) @file_get_contents(key_file()));
    if (!$k || !openssl_sign($body, $der, $k, OPENSSL_ALGO_SHA256)) fail_out('not_set_up', 'The licence server is not set up yet.', 500);
    return $body . '.' . b64url(der_to_raw($der));
}

function issue_pass(array $lic, string $device, array $settings): array {
    $now = now_ms();
    $until = $now + max(1, (int) $settings['passDays']) * 86400000;
    if (!empty($lic['expiresAt'])) $until = min($until, (int) $lic['expiresAt']);
    $payload = ['v' => 1, 'k' => $lic['key'], 'd' => $device, 'p' => 'full', 'iat' => $now, 'cu' => $until];
    return ['token' => sign_pass($payload), 'payload' => $payload, 'devices' => count($lic['devices']), 'maxDevices' => (int) $lic['maxDevices'],
        'licensedTo' => $lic['name'] ?: ($lic['email'] ?: '')];
}

// Throwaway inbox services: they get no automatic licence (setting "blockDisposable").
const DISPOSABLE_DOMAINS = ['mailinator.com', 'guerrillamail.com', 'guerrillamail.net', 'sharklasers.com', 'grr.la', '10minutemail.com', '10minutemail.net',
    'temp-mail.org', 'tempmail.com', 'tempmail.net', 'tempmailo.com', 'temp-mail.io', 'yopmail.com', 'yopmail.net', 'trashmail.com', 'trashmail.de', 'getnada.com',
    'nada.email', 'maildrop.cc', 'dispostable.com', 'throwawaymail.com', 'fakeinbox.com', 'mintemail.com', 'mohmal.com', 'emailondeck.com', 'burnermail.io',
    'mailnesia.com', 'mytemp.email', 'tempinbox.com', 'spamgourmet.com', 'mailcatch.com', 'moakt.com', 'tmail.ws', 'tmpmail.org', 'tmpmail.net', 'emailfake.com',
    'luxusmail.org', 'inboxkitten.com', 'mail.tm', 'internxt.com', 'dropmail.me', 'fexpost.com', 'linshiyouxiang.net', 'mailpoof.com', 'spambox.us', 'wegwerfmail.de'];

function email_domain(string $e): string { $p = strrpos($e, '@'); return $p === false ? '' : strtolower(substr($e, $p + 1)); }
/** Blocked: the address itself, or its whole domain ("@domain.com"). */
function is_blocked_email(array $blocked, string $email): bool {
    $e = strtolower($email);
    return $e !== '' && (isset($blocked[$e]) || isset($blocked['@' . email_domain($e)]));
}

function lic_status(array $lic): string {
    if (!empty($lic['email']) && is_blocked_email($GLOBALS['__blocked'] ?? [], (string) $lic['email'])) return 'blocked';
    if (!empty($lic['revoked'])) return 'revoked';
    if (!empty($lic['expiresAt']) && now_ms() >= (int) $lic['expiresAt']) return 'expired';
    return count($lic['devices']) ? 'active' : 'unused';
}

// ---------------------------------------------------------------- public API (Lead Agent)
/** The paid buyer's licence for an email: one that is on and has room for this computer (or already has it). */
function licence_for_email(array $s, string $email, string $device): string {
    $fallback = '';
    foreach ($s['licences'] as $k => $l) {
        if (($l['email'] ?? '') !== $email || !empty($l['revoked'])) continue;
        if (!empty($l['expiresAt']) && now_ms() >= (int) $l['expiresAt']) { $fallback = $fallback ?: $k; continue; }
        foreach ($l['devices'] as $d) if ($d['id'] === $device) return $k;
        if (count($l['devices']) < (int) $l['maxDevices']) return $k;
        $fallback = $k; // full: gives the "limit reached" answer below
    }
    return $fallback;
}

/** POST api/activate {key, name, email, device, label} — or api/claim without a key: the email must be a paid buyer's. */
function api_activate(bool $claim = false): void {
    $b = body();
    $res = with_store(function (&$s, &$save) use ($b, $claim) {
        $save = true;
        if (too_many($s, 'key', FAIL_LIMIT, 3600000)) return ['fail', 'slow_down', 'Too many attempts. Try again in an hour.'];
        if (empty($s['settings']['activationsOpen'])) return ['fail', 'closed', 'New activations are paused right now. Please contact the seller.'];
        $key = key_id($b['key'] ?? ''); $device = clean_device($b['device'] ?? '');
        if (!$device) return ['fail', 'bad_request', 'Missing computer id.'];
        $name = clean_text($b['name'] ?? '', 80); $email = clean_email($b['email'] ?? '');
        if ($name === '') return ['fail', 'need_name', 'Enter your name.'];
        if ($email === '') return ['fail', 'need_email', 'Enter a valid email address.'];
        if (is_blocked_email($s['blocked'] ?? [], $email)) return ['fail', 'blocked', "This email address can't be used to activate Lead Agent. Please contact the seller."];
        $auto = false;
        if ($claim) {
            if (empty($s['settings']['emailActivation'])) return ['fail', 'email_off', 'Enter the licence key you received after buying.'];
            $key = licence_for_email($s, $email, $device);
            if (!$key && !empty($s['settings']['autoIssue']) && !empty($s['settings']['blockDisposable']) && in_array(email_domain($email), DISPOSABLE_DOMAINS, true)) {
                count_fail($s, 'key');
                return ['fail', 'disposable', 'Please use your real email address. Temporary email addresses cannot be used.'];
            }
            if (!$key && !empty($s['settings']['autoIssue'])) {
                // Buyers pay before they can download, so a new email gets its own licence at once.
                // A cap per network stops one person from making licences in bulk.
                if (too_many($s, 'auto', AUTO_ISSUE_LIMIT, 86400000)) return ['fail', 'slow_down', 'Too many new activations from this network today. Please contact the seller.'];
                count_fail($s, 'auto');
                $now = now_ms();
                do { $key = new_key(); } while (isset($s['licences'][$key]));
                $s['licences'][$key] = ['key' => $key, 'name' => $name, 'email' => $email, 'note' => 'Created automatically when they activated',
                    'maxDevices' => max(1, min((int) $s['settings']['defaultMaxDevices'], 50)), 'expiresAt' => null, 'devices' => [], 'revoked' => false,
                    'createdAt' => $now, 'updatedAt' => $now, 'activatedAt' => null, 'auto' => true];
                bump_daily($s, 'created');
                $auto = true;
            }
            if (!$key) { count_fail($s, 'key'); return ['fail', 'not_buyer', "We could not find a purchase for $email. Use the email address you paid with, or enter your licence key."]; }
        }
        if (!$key || !isset($s['licences'][$key])) { count_fail($s, 'key'); return ['fail', 'invalid', 'This licence key was not found. Check it and try again.']; }
        $lic = &$s['licences'][$key];
        if (!empty($lic['revoked'])) return ['fail', 'revoked', 'This licence key has been switched off. Contact the seller.'];
        if (!empty($lic['expiresAt']) && now_ms() >= (int) $lic['expiresAt']) return ['fail', 'expired', 'This licence key has expired. Contact the seller to renew it.'];
        if (!empty($s['settings']['requireEmailMatch']) && $lic['email'] !== '' && $lic['email'] !== $email) {
            count_fail($s, 'key');
            return ['fail', 'email_mismatch', 'Use the email address you bought Lead Agent with.'];
        }
        $label = clean_text($b['label'] ?? '', 60) ?: 'Computer';
        $now = now_ms(); $new = true;
        foreach ($lic['devices'] as &$d) {
            if ($d['id'] === $device) { $d['lastSeen'] = $now; $d['label'] = $label; $d['userName'] = $name; $d['userEmail'] = $email; $new = false; }
        }
        unset($d);
        if ($new) {
            if (count($lic['devices']) >= (int) $lic['maxDevices']) {
                $n = count($lic['devices']);
                return ['fail', 'device_limit', "This key is already active on $n computer" . ($n === 1 ? '' : 's') . ' (the limit). Release one in Lead Agent on that computer, or ask the seller.'];
            }
            $lic['devices'][] = ['id' => $device, 'label' => $label, 'userName' => $name, 'userEmail' => $email, 'firstSeen' => $now, 'lastSeen' => $now, 'checks' => 0];
            bump_daily($s, 'activations');
        }
        if ($lic['name'] === '') $lic['name'] = $name;
        if ($lic['email'] === '') $lic['email'] = $email;
        if (empty($lic['activatedAt'])) $lic['activatedAt'] = $now;
        $lic['updatedAt'] = $now;
        return ['ok', $lic, $device, $s['settings'], $new, $name, $email, $label, $auto];
    });
    if ($res[0] === 'fail') fail_out($res[1], $res[2], $res[1] === 'slow_down' ? 429 : ($res[1] === 'bad_request' ? 400 : 200));
    [, $lic, $device, $settings, $new, $name, $email, $label, $auto] = $res;
    if ($auto) log_event('created', ['key' => $lic['key'], 'name' => $name, 'email' => $email, 'auto' => true]);
    if ($new) log_event('activated', ['key' => $lic['key'], 'name' => $name, 'email' => $email, 'computer' => $label, 'how' => $claim ? 'email' : 'key']);
    ok_out(issue_pass($lic, $device, $settings));
}

function api_check(): void {
    $b = body();
    $res = with_store(function (&$s, &$save) use ($b) {
        $key = key_id($b['key'] ?? ''); $device = clean_device($b['device'] ?? '');
        if (!$key || !isset($s['licences'][$key])) return ['fail', 'invalid', 'This licence key no longer exists.'];
        $lic = &$s['licences'][$key];
        if (!empty($lic['revoked'])) return ['fail', 'revoked', 'This licence key has been switched off. Contact the seller.'];
        if (!empty($lic['expiresAt']) && now_ms() >= (int) $lic['expiresAt']) return ['fail', 'expired', 'This licence key has expired. Contact the seller to renew it.'];
        $found = false; $now = now_ms();
        foreach ($lic['devices'] as &$d) {
            if ($d['id'] !== $device) continue;
            $found = true;
            // written at most once an hour per computer, to keep writes low
            if ($now - (int) ($d['lastSeen'] ?? 0) > 3600000) { $d['lastSeen'] = $now; $d['checks'] = (int) ($d['checks'] ?? 0) + 1; $save = true; bump_daily($s, 'checks'); }
        }
        unset($d);
        if (!$found) return ['fail', 'released', 'This computer was released from the licence. Enter the key again to use it here.'];
        return ['ok', $lic, $device, $s['settings']];
    });
    if ($res[0] === 'fail') fail_out($res[1], $res[2]);
    ok_out(issue_pass($res[1], $res[2], $res[3]));
}

function api_release(): void {
    $b = body();
    $res = with_store(function (&$s, &$save) use ($b) {
        $key = key_id($b['key'] ?? ''); $device = clean_device($b['device'] ?? '');
        if (!$key || !isset($s['licences'][$key])) return null;
        $lic = &$s['licences'][$key];
        $before = count($lic['devices']);
        $lic['devices'] = array_values(array_filter($lic['devices'], fn($d) => $d['id'] !== $device));
        $save = count($lic['devices']) !== $before;
        return ['key' => $key, 'released' => $save, 'devices' => count($lic['devices']), 'maxDevices' => (int) $lic['maxDevices']];
    });
    if (!$res) fail_out('invalid', 'This licence key was not found.');
    if ($res['released']) log_event('released_by_user', ['key' => $res['key']]);
    ok_out(['devices' => $res['devices'], 'maxDevices' => $res['maxDevices']]);
}

// ---------------------------------------------------------------- admin session
function start_session(): void {
    if (session_status() === PHP_SESSION_ACTIVE) return;
    $secure = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
    session_name('la_admin');
    session_set_cookie_params(['lifetime' => 0, 'path' => '/licence/', 'secure' => $secure, 'httponly' => true, 'samesite' => 'Strict']);
    $dir = data_dir() . DIRECTORY_SEPARATOR . 'sessions';
    if (!is_dir($dir)) @mkdir($dir, 0700, true);
    if (is_dir($dir) && is_writable($dir)) session_save_path($dir);
    session_start();
}
function logged_in(): bool {
    start_session();
    if (empty($_SESSION['admin']) || empty($_SESSION['at']) || now_ms() - $_SESSION['at'] > SESSION_HOURS * 3600000) return false;
    return true;
}
function require_admin(): void {
    if (!logged_in()) fail_out('unauthorized', 'Please log in again.', 401);
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'GET') {
        $sent = (string) ($_SERVER['HTTP_X_CSRF'] ?? '');
        if (!hash_equals((string) ($_SESSION['csrf'] ?? ''), $sent)) fail_out('csrf', 'Your session expired. Reload the page.', 403);
    }
}
function strong_password(string $p): ?string {
    if (mb_strlen($p) < 10) return 'Use at least 10 characters for the password.';
    if (!preg_match('/[A-Za-z]/', $p) || !preg_match('/[0-9]/', $p)) return 'Use letters and numbers in the password.';
    return null;
}

function admin_status(): void {
    $s = read_store();
    $in = logged_in();
    ok_out(['setupDone' => !empty($s['admin']), 'loggedIn' => $in, 'csrf' => $in ? $_SESSION['csrf'] : null,
        'admin' => $in ? ['name' => $s['admin']['name'], 'email' => $s['admin']['email']] : null,
        'productName' => $s['settings']['productName']]);
}

function admin_setup(): void {
    $b = body();
    $name = clean_text($b['name'] ?? '', 80); $email = clean_email($b['email'] ?? ''); $pass = (string) ($b['password'] ?? '');
    if ($name === '' || $email === '') fail_out('bad_request', 'Enter your name and a valid email.');
    if ($e = strong_password($pass)) fail_out('weak_password', $e);
    if (!empty(read_store()['admin'])) fail_out('already_set_up', 'Setup is already done. Log in instead.', 403);
    make_signing_key(); // first: if this server cannot make keys, nothing is saved
    $done = with_store(function (&$s, &$save) use ($name, $email, $pass) {
        if (!empty($s['admin'])) return false;
        $s['admin'] = ['name' => $name, 'email' => $email, 'hash' => password_hash($pass, PASSWORD_DEFAULT), 'created' => now_ms()];
        $save = true;
        return true;
    });
    if (!$done) fail_out('already_set_up', 'Setup is already done. Log in instead.', 403);
    log_event('setup', ['by' => $email]);
    start_session(); session_regenerate_id(true);
    $_SESSION['admin'] = $email; $_SESSION['at'] = now_ms(); $_SESSION['csrf'] = b64url(random_bytes(24));
    ok_out(['csrf' => $_SESSION['csrf']]);
}

function admin_login(): void {
    $b = body();
    $email = clean_email($b['email'] ?? ''); $pass = (string) ($b['password'] ?? '');
    $res = with_store(function (&$s, &$save) use ($email, $pass) {
        $save = true;
        if (too_many($s, 'login', LOGIN_FAIL_LIMIT, 900000)) return 'slow';
        $a = $s['admin'] ?? null;
        if (!$a || $email !== $a['email'] || !password_verify($pass, $a['hash'])) { count_fail($s, 'login'); return 'bad'; }
        if (password_needs_rehash($a['hash'], PASSWORD_DEFAULT)) $s['admin']['hash'] = password_hash($pass, PASSWORD_DEFAULT);
        $s['admin']['lastLogin'] = now_ms();
        return 'ok';
    });
    if ($res === 'slow') fail_out('slow_down', 'Too many wrong passwords. Wait 15 minutes and try again.', 429);
    if ($res === 'bad') { log_event('login_failed', []); fail_out('bad_login', 'Wrong email or password.', 401); }
    start_session(); session_regenerate_id(true);
    $_SESSION['admin'] = $email; $_SESSION['at'] = now_ms(); $_SESSION['csrf'] = b64url(random_bytes(24));
    log_event('login', ['by' => $email]);
    ok_out(['csrf' => $_SESSION['csrf']]);
}

function admin_logout(): void { start_session(); $_SESSION = []; session_destroy(); ok_out(); }

// ---------------------------------------------------------------- admin data
function lic_public(array $l): array {
    $l['status'] = lic_status($l);
    $l['deviceCount'] = count($l['devices']);
    $l['lastSeen'] = 0;
    foreach ($l['devices'] as $d) $l['lastSeen'] = max($l['lastSeen'], (int) ($d['lastSeen'] ?? 0));
    return $l;
}

function admin_overview(): void {
    $s = read_store(); $now = now_ms();
    $counts = ['total' => 0, 'active' => 0, 'unused' => 0, 'revoked' => 0, 'expired' => 0, 'blocked' => 0, 'computers' => 0, 'online7' => 0, 'online1' => 0, 'users' => 0];
    $users = [];
    foreach ($s['licences'] as $l) {
        $counts['total']++; $counts[lic_status($l)]++;
        foreach ($l['devices'] as $d) {
            $counts['computers']++;
            if ($now - (int) $d['lastSeen'] < 7 * 86400000) $counts['online7']++;
            if ($now - (int) $d['lastSeen'] < 86400000) $counts['online1']++;
            $users[strtolower($d['userEmail'] ?? '')] = true;
        }
    }
    $counts['users'] = count(array_filter(array_keys($users)));
    $series = [];
    for ($i = 29; $i >= 0; $i--) {
        $d = gmdate('Y-m-d', (int) ($now / 1000) - $i * 86400);
        $series[] = ['day' => $d, 'activations' => (int) ($s['stats']['daily'][$d]['activations'] ?? 0), 'checks' => (int) ($s['stats']['daily'][$d]['checks'] ?? 0), 'created' => (int) ($s['stats']['daily'][$d]['created'] ?? 0)];
    }
    $browsers = [];
    foreach ($s['licences'] as $l) foreach ($l['devices'] as $d) { $b = explode(' on ', (string) $d['label'])[0] ?: 'Other'; $browsers[$b] = ($browsers[$b] ?? 0) + 1; }
    arsort($browsers);
    $rows = user_rows($s);
    $counts['newUsers7'] = count(array_filter($rows, fn($r) => $now - $r['createdAt'] < 7 * 86400000));
    $counts['newUsers1'] = count(array_filter($rows, fn($r) => $now - $r['createdAt'] < 86400000));
    $counts['users'] = count($rows);
    usort($rows, fn($a, $b) => $b['createdAt'] <=> $a['createdAt']);
    ok_out(['counts' => $counts, 'series' => $series, 'browsers' => $browsers, 'events' => read_events(12), 'newest' => array_slice($rows, 0, 6),
        'activationsOpen' => !empty($s['settings']['activationsOpen']), 'autoIssue' => !empty($s['settings']['autoIssue'])]);
}

function admin_licences(): void {
    $s = read_store();
    $list = array_map('lic_public', array_values($s['licences']));
    usort($list, fn($a, $b) => ($b['createdAt'] ?? 0) <=> ($a['createdAt'] ?? 0));
    ok_out(['licences' => $list, 'settings' => $s['settings']]);
}

/**
 * New licences. Either one per paid buyer email (emails: "a@x.com\nb@y.com" or "Name <a@x.com>"),
 * or {count} keys (count 1 may carry a name and email).
 */
function admin_create(): void {
    $b = body();
    $buyers = [];
    if (!empty($b['emails'])) {
        foreach (preg_split('/[\r\n,;]+/', (string) $b['emails']) as $line) {
            $line = trim($line); if ($line === '') continue;
            $name = ''; $email = $line;
            if (preg_match('/^(.*?)<([^>]+)>$/', $line, $m)) { $name = trim($m[1], " \t\"'"); $email = $m[2]; }
            $e = clean_email($email);
            if ($e === '') { $buyers[] = ['bad' => $line]; continue; }
            $buyers[$e] = ['name' => clean_text($name, 80), 'email' => $e];
        }
        if (!$buyers) fail_out('bad_request', 'Enter at least one email address.');
        if (count($buyers) > 500) fail_out('bad_request', 'Add at most 500 emails at once.');
    }
    $count = $buyers ? count($buyers) : max(1, min((int) ($b['count'] ?? 1), 200));
    $res = with_store(function (&$s, &$save) use ($b, $count, $buyers) {
        $out = []; $skipped = []; $bad = []; $now = now_ms();
        $max = (int) ($b['maxDevices'] ?? 0) ?: (int) $s['settings']['defaultMaxDevices'];
        $expires = !empty($b['expiresAt']) ? (int) $b['expiresAt'] : 0;
        $list = $buyers ?: array_fill(0, $count, ['name' => $count === 1 ? clean_text($b['name'] ?? '', 80) : '', 'email' => $count === 1 ? clean_email($b['email'] ?? '') : '']);
        $have = email_map($s); // one licence per email
        if (!$buyers && $count === 1 && $list[0]['email'] !== '' && isset($have[$list[0]['email']])) return ['exists' => $have[$list[0]['email']], 'email' => $list[0]['email']];
        foreach ($list as $who) {
            if (isset($who['bad'])) { $bad[] = $who['bad']; continue; }
            if ($buyers && isset($have[$who['email']])) { $skipped[] = $who['email']; continue; }
            do { $key = new_key(); } while (isset($s['licences'][$key]));
            $s['licences'][$key] = ['key' => $key, 'name' => $who['name'], 'email' => $who['email'],
                'note' => clean_text($b['note'] ?? '', 300), 'maxDevices' => max(1, min($max, 50)), 'expiresAt' => $expires ?: null,
                'devices' => [], 'revoked' => false, 'createdAt' => $now, 'updatedAt' => $now, 'activatedAt' => null];
            bump_daily($s, 'created');
            $out[] = lic_public($s['licences'][$key]);
        }
        $save = (bool) $out;
        return ['made' => $out, 'skipped' => $skipped, 'bad' => $bad];
    });
    if (isset($res['exists'])) fail_out('exists', "{$res['email']} already has a licence ({$res['exists']}). Each email can have one licence: open it under Users to change it.");
    foreach ($res['made'] as $l) log_event('created', ['key' => $l['key'], 'name' => $l['name'], 'email' => $l['email']]);
    ok_out(['licences' => $res['made'], 'skipped' => $res['skipped'], 'invalid' => $res['bad']]);
}

function admin_licence_action(string $key, string $action): void {
    $key = key_id($key);
    $b = body();
    $res = with_store(function (&$s, &$save) use ($key, $action, $b) {
        if (!$key || !isset($s['licences'][$key])) return null;
        if ($action === 'delete') { unset($s['licences'][$key]); $save = true; return ['deleted' => true]; }
        $l = &$s['licences'][$key];
        switch ($action) {
            case 'revoke': $l['revoked'] = true; break;
            case 'restore': $l['revoked'] = false; break;
            case 'release': $id = (string) ($b['device'] ?? ''); $l['devices'] = array_values(array_filter($l['devices'], fn($d) => $d['id'] !== $id)); break;
            case 'release-all': $l['devices'] = []; break;
            case 'edit':
                if (array_key_exists('name', $b)) $l['name'] = clean_text($b['name'], 80);
                if (array_key_exists('email', $b)) {
                    $ne = clean_email($b['email']);
                    $owner = $ne !== '' ? (email_map($s)[$ne] ?? '') : '';
                    if ($owner !== '' && $owner !== $key) return ['error' => "$ne already has a licence ($owner). Each email can have one licence."];
                    $l['email'] = $ne;
                }
                if (array_key_exists('note', $b)) $l['note'] = clean_text($b['note'], 300);
                if (array_key_exists('maxDevices', $b)) $l['maxDevices'] = max(1, min((int) $b['maxDevices'], 50));
                if (array_key_exists('expiresAt', $b)) $l['expiresAt'] = $b['expiresAt'] ? (int) $b['expiresAt'] : null;
                break;
            case 'get': return ['licence' => lic_public($l)];
            default: return ['error' => 'Unknown action.'];
        }
        $l['updatedAt'] = now_ms(); $save = true;
        return ['licence' => lic_public($l)];
    });
    if ($res === null) fail_out('invalid', 'Licence not found.', 404);
    if (isset($res['error'])) fail_out('bad_request', $res['error'], 400);
    if ($action !== 'get') log_event($action === 'delete' ? 'deleted' : $action, ['key' => $key]);
    if ($action === 'get') $res['events'] = read_events(50, $key);
    ok_out($res);
}

// ---------------------------------------------------------------- users (one per email)
/** email => licence key, for every licence that has an email. */
function email_map(array $s): array {
    $m = [];
    foreach ($s['licences'] as $k => $l) if (($l['email'] ?? '') !== '') $m[strtolower($l['email'])] = $k;
    return $m;
}
/** The email a licence belongs to: its own, or the first person who activated it. */
function lic_owner(array $l): string {
    $e = strtolower((string) ($l['email'] ?? ''));
    if ($e === '') foreach ($l['devices'] as $d) if (!empty($d['userEmail'])) { $e = strtolower($d['userEmail']); break; }
    return $e;
}
function keys_for_email(array $s, string $email): array {
    return array_values(array_filter(array_keys($s['licences']), fn($k) => lic_owner($s['licences'][$k]) === $email));
}
function user_rows(array $s): array {
    $u = [];
    foreach ($s['licences'] as $l) {
        $email = lic_owner($l);
        if ($email === '') continue;
        if (!isset($u[$email])) $u[$email] = ['email' => $email, 'name' => '', 'keys' => [], 'computers' => 0, 'maxComputers' => 0, 'firstSeen' => 0, 'lastSeen' => 0,
            'createdAt' => PHP_INT_MAX, 'auto' => false, 'statuses' => [], 'browsers' => [], 'note' => ''];
        $r = &$u[$email];
        $r['keys'][] = $l['key'];
        $r['maxComputers'] += (int) $l['maxDevices'];
        $r['computers'] += count($l['devices']);
        $r['createdAt'] = min($r['createdAt'], (int) ($l['createdAt'] ?? 0));
        if (!empty($l['auto'])) $r['auto'] = true;
        if ($r['name'] === '' && !empty($l['name'])) $r['name'] = $l['name'];
        if ($r['note'] === '' && !empty($l['note'])) $r['note'] = $l['note'];
        $r['statuses'][] = lic_status($l);
        foreach ($l['devices'] as $d) {
            $f = (int) ($d['firstSeen'] ?? 0);
            $r['firstSeen'] = $r['firstSeen'] ? min($r['firstSeen'], $f) : $f;
            $r['lastSeen'] = max($r['lastSeen'], (int) ($d['lastSeen'] ?? 0));
            if ($r['name'] === '' && !empty($d['userName'])) $r['name'] = $d['userName'];
            $r['browsers'][] = (string) ($d['label'] ?? '');
        }
        unset($r);
    }
    foreach ($u as &$r) {
        $st = $r['statuses'];
        $r['status'] = in_array('blocked', $st, true) ? 'blocked' : (in_array('active', $st, true) ? 'active'
            : (count(array_filter($st, fn($x) => $x === 'revoked')) === count($st) ? 'revoked' : (in_array('expired', $st, true) ? 'expired' : 'unused')));
        $r['browsers'] = array_values(array_unique(array_filter($r['browsers'])));
        unset($r['statuses']);
    }
    unset($r);
    return array_values($u);
}

function admin_users(): void {
    $s = read_store();
    $rows = user_rows($s);
    usort($rows, fn($a, $b) => max($b['lastSeen'], $b['createdAt']) <=> max($a['lastSeen'], $a['createdAt']));
    ok_out(['users' => $rows]);
}

/** Apply one user action to every licence of an email (inside the store lock). */
function apply_user_action(array &$s, string $email, string $action, array $b): ?string {
    $keys = keys_for_email($s, $email);
    if (!$keys && !in_array($action, ['block', 'unblock'], true)) return 'User not found.';
    foreach ($keys as $k) {
        $l = &$s['licences'][$k];
        switch ($action) {
            case 'pause': case 'block': $l['revoked'] = true; break;
            case 'resume': case 'unblock': $l['revoked'] = false; break;
            case 'release': $l['devices'] = []; break;
            case 'delete': break;
            case 'edit':
                if (array_key_exists('name', $b)) $l['name'] = clean_text($b['name'], 80);
                if (array_key_exists('note', $b)) $l['note'] = clean_text($b['note'], 300);
                if (array_key_exists('maxDevices', $b)) $l['maxDevices'] = max(1, min((int) $b['maxDevices'], 50));
                break;
            default: return 'Unknown action.';
        }
        $l['updatedAt'] = now_ms();
        unset($l);
        if ($action === 'delete') unset($s['licences'][$k]);
    }
    if ($action === 'block') $s['blocked'][$email] = ['at' => now_ms(), 'note' => clean_text($b['note'] ?? '', 200)];
    if ($action === 'unblock') unset($s['blocked'][$email]);
    $GLOBALS['__blocked'] = $s['blocked'];
    return null;
}

function admin_user(string $raw, string $action): void {
    $email = clean_email(rawurldecode($raw));
    if ($email === '') fail_out('invalid', 'User not found.', 404);
    $b = body();
    if ($action === 'get') {
        $s = read_store();
        $keys = keys_for_email($s, $email);
        $row = array_values(array_filter(user_rows($s), fn($r) => $r['email'] === $email))[0] ?? null;
        if (!$row) fail_out('invalid', 'User not found.', 404);
        $events = array_values(array_filter(read_events(3000), fn($e) => strtolower($e['email'] ?? '') === $email || in_array($e['key'] ?? '', $keys, true)));
        ok_out(['user' => $row, 'licences' => array_map(fn($k) => lic_public($s['licences'][$k]), $keys), 'events' => array_slice($events, 0, 60),
            'blocked' => is_blocked_email($s['blocked'] ?? [], $email)]);
    }
    $err = with_store(function (&$s, &$save) use ($email, $action, $b) {
        $e = apply_user_action($s, $email, $action, $b);
        if (!$e) $save = true;
        return $e;
    });
    if ($err) fail_out('bad_request', $err, $err === 'User not found.' ? 404 : 400);
    log_event("user_$action", ['email' => $email]);
    ok_out();
}

/** POST admin/bulk {action, emails: [...]} or {action, keys: [...]}. */
function admin_bulk(): void {
    $b = body();
    $action = (string) ($b['action'] ?? '');
    $emails = array_values(array_filter(array_map('clean_email', (array) ($b['emails'] ?? []))));
    $keys = array_values(array_filter(array_map('key_id', (array) ($b['keys'] ?? []))));
    if (!$emails && !$keys) fail_out('bad_request', 'Nothing selected.');
    if (count($emails) + count($keys) > 1000) fail_out('bad_request', 'Select at most 1000 at once.');
    $done = with_store(function (&$s, &$save) use ($action, $emails, $keys, $b) {
        $n = 0;
        foreach ($emails as $e) if (!apply_user_action($s, $e, $action, $b)) $n++;
        foreach ($keys as $k) {
            if (!isset($s['licences'][$k])) continue;
            $l = &$s['licences'][$k];
            if ($action === 'pause') $l['revoked'] = true;
            elseif ($action === 'resume') $l['revoked'] = false;
            elseif ($action === 'release') $l['devices'] = [];
            elseif ($action !== 'delete') { unset($l); continue; }
            $l['updatedAt'] = now_ms();
            unset($l);
            if ($action === 'delete') unset($s['licences'][$k]);
            $n++;
        }
        $save = $n > 0;
        return $n;
    });
    log_event("bulk_$action", ['count' => $done, 'what' => $emails ? 'users' : 'keys']);
    ok_out(['done' => $done]);
}

/** GET admin/blocked; POST {add: "x@y.com" | "@domain.com" | "domain.com", note} or {remove}. */
function admin_blocked(): void {
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'GET') {
        $s = read_store(); $out = [];
        foreach ($s['blocked'] as $k => $v) $out[] = ['entry' => $k, 'at' => $v['at'] ?? 0, 'note' => $v['note'] ?? ''];
        usort($out, fn($a, $b) => $b['at'] <=> $a['at']);
        ok_out(['blocked' => $out]);
    }
    $b = body();
    $norm = function ($v) {
        $v = strtolower(trim((string) $v));
        if ($v === '') return '';
        if (strpos($v, '@') > 0) return clean_email($v);
        $d = ltrim($v, '@');
        return preg_match('/^[a-z0-9.-]+\.[a-z]{2,}$/', $d) ? '@' . $d : '';
    };
    if (isset($b['remove'])) {
        $e = $norm($b['remove']);
        with_store(function (&$s, &$save) use ($e) { unset($s['blocked'][$e]); $save = true; });
        log_event('unblocked', ['email' => $e]);
        ok_out();
    }
    $e = $norm($b['add'] ?? '');
    if ($e === '') fail_out('bad_request', 'Enter an email address (name@company.com) or a domain (company.com).');
    $paused = with_store(function (&$s, &$save) use ($e, $b) {
        $s['blocked'][$e] = ['at' => now_ms(), 'note' => clean_text($b['note'] ?? '', 200)];
        $GLOBALS['__blocked'] = $s['blocked'];
        $n = 0;
        foreach ($s['licences'] as &$l) {
            $o = lic_owner($l);
            if ($o !== '' && is_blocked_email([$e => 1], $o) && empty($l['revoked'])) { $l['revoked'] = true; $l['updatedAt'] = now_ms(); $n++; }
        }
        unset($l);
        $save = true;
        return $n;
    });
    log_event('blocked', ['email' => $e, 'paused' => $paused]);
    ok_out(['entry' => $e, 'paused' => $paused]);
}

function admin_events(): void { ok_out(['events' => read_events(min(2000, max(10, (int) ($_GET['limit'] ?? 300))))]); }

function admin_settings(): void {
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'GET') {
        $s = read_store();
        ok_out(['settings' => $s['settings'], 'publicKey' => public_jwk(), 'dataDir' => data_dir(), 'php' => PHP_VERSION]);
    }
    $b = body();
    $out = with_store(function (&$s, &$save) use ($b) {
        $st = &$s['settings'];
        foreach (['productName' => 60, 'sellerName' => 80] as $k => $max) if (isset($b[$k])) $st[$k] = clean_text($b[$k], $max) ?: $st[$k];
        if (isset($b['supportEmail'])) $st['supportEmail'] = clean_email($b['supportEmail']);
        if (isset($b['defaultMaxDevices'])) $st['defaultMaxDevices'] = max(1, min((int) $b['defaultMaxDevices'], 50));
        if (isset($b['passDays'])) $st['passDays'] = max(1, min((int) $b['passDays'], 30));
        if (isset($b['activationsOpen'])) $st['activationsOpen'] = (bool) $b['activationsOpen'];
        if (isset($b['emailActivation'])) $st['emailActivation'] = (bool) $b['emailActivation'];
        if (isset($b['autoIssue'])) $st['autoIssue'] = (bool) $b['autoIssue'];
        if (isset($b['blockDisposable'])) $st['blockDisposable'] = (bool) $b['blockDisposable'];
        if (isset($b['requireEmailMatch'])) $st['requireEmailMatch'] = (bool) $b['requireEmailMatch'];
        if (isset($b['messageTemplate'])) $st['messageTemplate'] = mb_substr((string) $b['messageTemplate'], 0, 3000);
        $save = true;
        return $st;
    });
    log_event('settings', []);
    ok_out(['settings' => $out]);
}

function admin_password(): void {
    $b = body();
    $old = (string) ($b['current'] ?? ''); $new = (string) ($b['password'] ?? '');
    if ($e = strong_password($new)) fail_out('weak_password', $e);
    $ok = with_store(function (&$s, &$save) use ($old, $new, $b) {
        if (!password_verify($old, $s['admin']['hash'])) return false;
        $s['admin']['hash'] = password_hash($new, PASSWORD_DEFAULT);
        if (!empty($b['name'])) $s['admin']['name'] = clean_text($b['name'], 80);
        $save = true;
        return true;
    });
    if (!$ok) fail_out('bad_login', 'The current password is wrong.');
    log_event('password_changed', []);
    ok_out();
}

function admin_export(): void {
    $s = read_store();
    $cell = fn($v) => preg_match('/^[=+\-@]/', (string) $v) ? "'" . $v : $v; // no formulas in spreadsheets
    if (($_GET['type'] ?? '') === 'users') {
        header('Content-Type: text/csv; charset=utf-8');
        header('Content-Disposition: attachment; filename="lead-agent-users-' . gmdate('Y-m-d') . '.csv"');
        header('Cache-Control: no-store');
        $o = fopen('php://output', 'w');
        fputcsv($o, ['Name', 'Email', 'Status', 'Licence keys', 'Computers', 'Computers allowed', 'Browsers', 'Signed up', 'First activated', 'Last seen', 'How', 'Note']);
        $label = ['revoked' => 'paused', 'unused' => 'not activated'];
        foreach (user_rows($s) as $r) {
            fputcsv($o, array_map($cell, [$r['name'], $r['email'], $label[$r['status']] ?? $r['status'], implode(' ', $r['keys']), $r['computers'], $r['maxComputers'],
                implode('; ', $r['browsers']), gmdate('Y-m-d', (int) ($r['createdAt'] / 1000)), $r['firstSeen'] ? gmdate('Y-m-d', (int) ($r['firstSeen'] / 1000)) : '',
                $r['lastSeen'] ? gmdate('Y-m-d H:i', (int) ($r['lastSeen'] / 1000)) : '', $r['auto'] ? 'automatic' : 'added by admin', $r['note']]));
        }
        fclose($o);
        exit;
    }
    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename="lead-agent-licences-' . gmdate('Y-m-d') . '.csv"');
    header('Cache-Control: no-store');
    $o = fopen('php://output', 'w');
    fputcsv($o, ['Key', 'Status', 'Buyer name', 'Buyer email', 'Note', 'Max computers', 'Computers', 'User names', 'User emails', 'Created', 'Expires', 'Last seen']);
    foreach ($s['licences'] as $l) {
        $p = lic_public($l);
        $cell = fn($v) => preg_match('/^[=+\-@]/', (string) $v) ? "'" . $v : $v; // no formulas in spreadsheets
        fputcsv($o, array_map($cell, [$l['key'], $p['status'], $l['name'], $l['email'], $l['note'], $l['maxDevices'], count($l['devices']),
            implode('; ', array_column($l['devices'], 'userName')), implode('; ', array_column($l['devices'], 'userEmail')),
            gmdate('Y-m-d', (int) ($l['createdAt'] / 1000)), $l['expiresAt'] ? gmdate('Y-m-d', (int) ($l['expiresAt'] / 1000)) : '',
            $p['lastSeen'] ? gmdate('Y-m-d H:i', (int) ($p['lastSeen'] / 1000)) : '']));
    }
    fclose($o);
    exit;
}

// ---------------------------------------------------------------- router
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
if ($method === 'OPTIONS') { http_response_code(204); exit; }

$route = (string) ($_GET['route'] ?? '');
if ($route === '') { // without the .htaccess rewrite (e.g. PHP's own test server)
    $path = parse_url((string) ($_SERVER['REQUEST_URI'] ?? ''), PHP_URL_PATH) ?: '';
    $i = strpos($path, '/api/');
    $route = $i === false ? '' : substr($path, $i + 5);
}
$route = trim($route, '/');
$parts = $route === '' ? [] : explode('/', $route);

try {
    if ($route === 'health') ok_out(['service' => 'lead-agent-licence', 'time' => now_ms(), 'ready' => is_file(key_file())]);
    if ($route === 'public-key') { $k = public_jwk(); $k ? ok_out(['publicKey' => $k]) : fail_out('not_set_up', 'Finish setup in the admin panel first.', 404); }
    if (in_array($route, ['activate', 'claim', 'check', 'release'], true)) {
        if ($method !== 'POST') fail_out('bad_request', 'Use POST.', 405);
        if (!is_file(key_file())) fail_out('not_set_up', 'The licence server is not set up yet.', 500);
        if ($route === 'activate') api_activate();
        if ($route === 'claim') api_activate(true);
        $route === 'check' ? api_check() : api_release();
    }
    if (($parts[0] ?? '') === 'admin') {
        // Admin answers are for this site only.
        header_remove('Access-Control-Allow-Origin');
        $a = $parts[1] ?? '';
        if ($a === 'status') admin_status();
        if ($a === 'setup' && $method === 'POST') admin_setup();
        if ($a === 'login' && $method === 'POST') admin_login();
        if ($a === 'logout' && $method === 'POST') admin_logout();
        require_admin();
        $_SESSION['at'] = now_ms(); // sliding session
        if ($a === 'overview') admin_overview();
        if ($a === 'licences' && !isset($parts[2])) { $method === 'POST' ? admin_create() : admin_licences(); }
        if ($a === 'licences' && isset($parts[2])) admin_licence_action($parts[2], $parts[3] ?? 'get');
        if ($a === 'users' && !isset($parts[2])) admin_users();
        if ($a === 'users' && isset($parts[2])) admin_user($parts[2], $parts[3] ?? 'get');
        if ($a === 'bulk' && $method === 'POST') admin_bulk();
        if ($a === 'blocked') admin_blocked();
        if ($a === 'events') admin_events();
        if ($a === 'settings') admin_settings();
        if ($a === 'password' && $method === 'POST') admin_password();
        if ($a === 'export') admin_export();
    }
    fail_out('not_found', 'Not found.', 404);
} catch (Throwable $e) {
    error_log('licence api: ' . $e->getMessage());
    fail_out('server_error', 'Something went wrong on the licence server.', 500);
}
