<?php
/**
 * Shared bootstrap: loads config, opens the DB, exposes helpers.
 * Every entry point (api, cron, admin) includes exactly this file.
 */
declare(strict_types=1);

define('GS_ROOT', dirname(__DIR__));

/* ------------------------------------------------------------------
 * Config lookup.
 *
 * Walks UP from the install directory looking for .gs-app-config.php,
 * rather than checking one hard-coded parent. Hostinger does not always
 * give a subdomain its own docroot — it can land at
 * <main-site>/public_html/app, in which case the immediate parent is a
 * LIVE WEB ROOT and also inside the git-deploy wipe path. Searching
 * upward lets the config live anywhere safely above the install
 * (the domain folder, or the account home) without a code change.
 *
 * The in-tree config.php is last and deliberately discouraged: anything
 * inside public_html is destroyed by the next deploy.
 * ---------------------------------------------------------------- */
function gs_config_candidates(): array
{
    $paths = [];
    $dir = GS_ROOT;
    for ($i = 0; $i < 4; $i++) {
        $parent = dirname($dir);
        if ($parent === $dir) break;          // hit the filesystem root
        $paths[] = $parent . '/.gs-app-config.php';
        $dir = $parent;
    }
    $paths[] = GS_ROOT . '/config.php';       // last resort, deploy-fragile
    return $paths;
}

function gs_config(): array
{
    static $cfg = null;
    if ($cfg !== null) return $cfg;

    foreach (gs_config_candidates() as $p) {
        if (is_readable($p)) {
            $c = require $p;
            if (is_array($c)) {
                $cfg = $c;                 // set the static FIRST so nested calls
                $cfg['_config_path'] = $p; // (db() while overlaying) see the file values
                $cfg = gs_apply_overrides($cfg);
                return $cfg;
            }
        }
    }
    gs_fail(500, 'server_not_configured',
        'No config found. Searched: ' . implode(', ', gs_config_candidates()));
}

/* ------------------------------------------------------------------
 * Runtime overrides (Admin -> Settings).
 *
 * Operators set MetaApi / push / engine values from the admin panel instead
 * of editing the config file on the host. They live as one JSON document in
 * engine_state under GS_OVERRIDES_KEY. Secrets are stored encrypted with
 * app_key (a key like "token_enc" is decrypted into "token"); if decryption
 * fails, e.g. after an app_key rotation, that value silently falls back to
 * the file. Anything not overridden keeps its file value.
 * ---------------------------------------------------------------- */
const GS_OVERRIDES_KEY      = 'settings_overrides';
const GS_OVERRIDE_SECTIONS  = ['metaapi', 'fcm', 'engine'];

function gs_apply_overrides(array $cfg): array
{
    static $done = false;
    if ($done || defined('GS_SKIP_OVERRIDES')) return $cfg;
    $done = true;
    try {
        $row = q1('SELECT v FROM engine_state WHERE k = ?', [GS_OVERRIDES_KEY]);
        if (!$row || $row['v'] === null || $row['v'] === '') return $cfg;
        $ov = json_decode((string)$row['v'], true);
        if (!is_array($ov)) return $cfg;

        require_once __DIR__ . '/crypto.php';
        $cfg['_overrides'] = [];
        foreach (GS_OVERRIDE_SECTIONS as $sec) {
            if (empty($ov[$sec]) || !is_array($ov[$sec])) continue;
            $section = is_array($cfg[$sec] ?? null) ? $cfg[$sec] : [];
            foreach ($ov[$sec] as $k => $v) {
                if (substr((string)$k, -4) === '_enc') {
                    $plain = gs_decrypt(is_string($v) ? $v : null);
                    if ($plain === null) continue;          // undecryptable: keep file value
                    $k = substr((string)$k, 0, -4);
                    $v = $plain;
                }
                $section[$k] = $v;
                $cfg['_overrides'][] = "$sec.$k";
            }
            $cfg[$sec] = $section;
        }
    } catch (Throwable $e) {
        // Table missing or DB unavailable: the file config stands.
    }
    return $cfg;
}

/** The raw stored override document (secrets still encrypted). */
function gs_overrides_raw(): array
{
    try {
        $row = q1('SELECT v FROM engine_state WHERE k = ?', [GS_OVERRIDES_KEY]);
        $d = $row ? json_decode((string)$row['v'], true) : null;
        return is_array($d) ? $d : [];
    } catch (Throwable $e) {
        return [];
    }
}

/**
 * True when $path sits inside a directory that Apache serves. Used to warn
 * that a config is one bad rewrite away from being downloadable, and that a
 * git deploy will delete it.
 */
function gs_path_is_web_served(string $path): bool
{
    return (bool)preg_match('#/(public_html|htdocs|www|web)(/|$)#', $path);
}

/* ------------------------------------------------------------------
 * Database
 * ---------------------------------------------------------------- */
function db(): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) return $pdo;

    $c = gs_config()['db'];
    if ($pdo instanceof PDO) return $pdo;   // gs_config() may have connected while overlaying
    $dsn = sprintf('mysql:host=%s;dbname=%s;charset=utf8mb4', $c['host'], $c['name']);
    try {
        $pdo = new PDO($dsn, $c['user'], $c['pass'], [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES   => false,
        ]);
    } catch (Throwable $e) {
        gs_fail(500, 'db_unavailable', 'Database connection failed.');
    }
    return $pdo;
}

function q(string $sql, array $args = []): PDOStatement
{
    $st = db()->prepare($sql);
    $st->execute($args);
    return $st;
}
function q1(string $sql, array $args = []): ?array
{
    $r = q($sql, $args)->fetch();
    return $r === false ? null : $r;
}
function qall(string $sql, array $args = []): array
{
    return q($sql, $args)->fetchAll();
}
function qval(string $sql, array $args = [], $default = null)
{
    $r = q($sql, $args)->fetch(PDO::FETCH_NUM);
    return $r === false ? $default : $r[0];
}

/* ------------------------------------------------------------------
 * JSON responses
 * ---------------------------------------------------------------- */
function gs_json($data, int $code = 200): void
{
    http_response_code($code);
    header('Content-Type: application/json; charset=utf-8');
    header('X-Content-Type-Options: nosniff');
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}
function gs_ok($data = []): void      { gs_json(['ok' => true] + (array)$data); }
function gs_fail(int $code, string $err, string $msg = ''): void
{
    gs_json(['ok' => false, 'error' => $err, 'message' => $msg], $code);
}

/* ------------------------------------------------------------------
 * Misc helpers
 * ---------------------------------------------------------------- */
function gs_now(): string { return gmdate('Y-m-d H:i:s'); }
function gs_ip(): string  { return substr((string)($_SERVER['REMOTE_ADDR'] ?? ''), 0, 45); }

function gs_audit(string $actorType, ?int $actorId, string $action, $detail = null): void
{
    try {
        q('INSERT INTO audit_log (actor_type, actor_id, action, detail, ip)
           VALUES (?,?,?,?,?)',
          [$actorType, $actorId, $action,
           is_string($detail) ? $detail : json_encode($detail), gs_ip()]);
    } catch (Throwable $e) { /* auditing must never break the request */ }
}

/** Read a JSON request body into an array. */
function gs_body(): array
{
    static $b = null;
    if ($b !== null) return $b;
    $raw = file_get_contents('php://input') ?: '';
    $d = json_decode($raw, true);
    $b = is_array($d) ? $d : [];
    return $b;
}

function gs_str(array $a, string $k, string $def = ''): string
{
    return isset($a[$k]) && is_scalar($a[$k]) ? trim((string)$a[$k]) : $def;
}
function gs_num(array $a, string $k, float $def = 0.0): float
{
    return isset($a[$k]) && is_numeric($a[$k]) ? (float)$a[$k] : $def;
}
function gs_bool(array $a, string $k, bool $def = false): bool
{
    if (!array_key_exists($k, $a)) return $def;
    return filter_var($a[$k], FILTER_VALIDATE_BOOLEAN, FILTER_NULL_ON_FAILURE) ?? $def;
}

/** Simple per-key fixed-window rate limit backed by engine_state. */
function gs_rate_limit(string $key, int $max, int $windowSec): bool
{
    $k = 'rl:' . substr(hash('sha256', $key), 0, 40);
    $row = q1('SELECT v, updated_at FROM engine_state WHERE k = ?', [$k]);
    $now = time();
    if ($row) {
        [$count, $start] = array_pad(explode(':', (string)$row['v']), 2, '0');
        if ($now - (int)$start < $windowSec) {
            if ((int)$count >= $max) return false;
            q('UPDATE engine_state SET v = ? WHERE k = ?',
              [((int)$count + 1) . ':' . $start, $k]);
            return true;
        }
    }
    q('INSERT INTO engine_state (k, v) VALUES (?,?)
       ON DUPLICATE KEY UPDATE v = VALUES(v)', [$k, '1:' . $now]);
    return true;
}
