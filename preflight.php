<?php
/**
 * Deployment self-check.  Visit  https://<your-app-host>/preflight.php
 *
 * Answers "did step N actually land?" without needing SSH or a database
 * client. Every check is read-only and safe to run on a live install.
 *
 * DELETE THIS FILE once the deployment is green — it reveals which pieces
 * of infrastructure exist, which is more than a stranger needs to know.
 */
declare(strict_types=1);

$checks = [];
$fail = 0;
$warn = 0;

function chk(string $name, string $state, string $detail = ''): void
{
    global $checks, $fail, $warn;
    if ($state === 'FAIL') $fail++;
    if ($state === 'WARN') $warn++;
    $checks[] = ['name' => $name, 'state' => $state, 'detail' => $detail];
}

/* ---------------- 1. PHP itself ---------------- */
chk('PHP version', version_compare(PHP_VERSION, '8.0', '>=') ? 'OK' : 'FAIL', PHP_VERSION);

foreach (['pdo_mysql' => 'database', 'openssl' => 'credential encryption',
          'curl' => 'MetaApi calls', 'json' => 'everything'] as $ext => $why) {
    chk("ext: $ext", extension_loaded($ext) ? 'OK' : 'FAIL', "needed for $why");
}

/* ---------------- 2. Where am I installed? ---------------- */
// Hostinger does not always give a subdomain its own docroot. If this install
// sits inside another site's public_html, that site's git deploy will delete
// it - so say so loudly rather than let it be discovered the hard way.
$root = __DIR__;
$webServed = (bool)preg_match('#/(public_html|htdocs|www|web)(/|$)#', $root);
$nested = (bool)preg_match('#/public_html/.+#', $root);

chk('install path', $nested ? 'FAIL' : 'OK', $root);
if ($nested) {
    chk('install is inside another web root', 'FAIL',
        'This tree lives under a parent public_html. A git deploy of the parent '
        . 'site WIPES untracked files and will delete this install. Give the '
        . 'subdomain its own document root outside public_html.');
}

/* ---------------- 3. Config ---------------- */
$cfg = null;
$configPath = '';
$candidates = [];
$dir = $root;
for ($i = 0; $i < 4; $i++) {
    $parent = dirname($dir);
    if ($parent === $dir) break;
    $candidates[] = $parent . '/.gs-app-config.php';
    $dir = $parent;
}
$candidates[] = $root . '/config.php';

foreach ($candidates as $p) {
    if (is_readable($p)) { $configPath = $p; break; }
}

if ($configPath === '') {
    $safe = null;
    foreach ($candidates as $p) {
        if (!preg_match('#/(public_html|htdocs|www|web)(/|$)#', $p)) { $safe = $p; break; }
    }
    chk('config file', 'FAIL',
        'Not found. Put it at: ' . ($safe ?? $candidates[0])
        . '  (searched ' . count($candidates) . ' locations upward)');
} else {
    $cfg = require $configPath;
    $inWebRoot = (bool)preg_match('#/(public_html|htdocs|www|web)(/|$)#', $configPath);
    chk('config file', 'OK', $configPath);
    chk('config outside the web root', $inWebRoot ? 'FAIL' : 'OK',
        $inWebRoot
            ? 'The config sits inside a served directory - a deploy will delete it '
              . 'and a rewrite mistake could expose it. Move it above public_html.'
            : 'safe from the deploy wipe');
}

/* ---------------- 3. app_key ---------------- */
if (is_array($cfg)) {
    $raw = (string)($cfg['app_key'] ?? '');
    $bin = base64_decode($raw, true);
    if ($raw === '' || str_contains($raw, 'CHANGEME')) {
        chk('app_key', 'FAIL', 'still the placeholder - run: openssl rand -base64 32');
    } elseif ($bin === false || strlen($bin) !== 32) {
        chk('app_key', 'FAIL', 'must decode to exactly 32 bytes, got ' .
            ($bin === false ? 'invalid base64' : strlen($bin) . ' bytes'));
    } else {
        chk('app_key', 'OK', '32 bytes');
        // Prove encrypt/decrypt actually round-trips on this host.
        $iv = random_bytes(12); $tag = '';
        $ct = openssl_encrypt('probe', 'aes-256-gcm', $bin, OPENSSL_RAW_DATA, $iv, $tag, '', 16);
        $pt = $ct === false ? false
            : openssl_decrypt($ct, 'aes-256-gcm', $bin, OPENSSL_RAW_DATA, $iv, $tag);
        chk('AES-256-GCM round trip', $pt === 'probe' ? 'OK' : 'FAIL');
    }
}

/* ---------------- 4. Database ---------------- */
$pdo = null;
if (is_array($cfg) && !empty($cfg['db'])) {
    $d = $cfg['db'];
    if (str_contains((string)($d['user'] ?? ''), 'CHANGEME')) {
        chk('database credentials', 'FAIL', 'still the placeholder');
    } else {
        try {
            $pdo = new PDO(
                sprintf('mysql:host=%s;dbname=%s;charset=utf8mb4', $d['host'], $d['name']),
                $d['user'], $d['pass'],
                [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_TIMEOUT => 5]
            );
            chk('database connection', 'OK', $d['name'] . ' @ ' . $d['host']);
        } catch (Throwable $e) {
            // Never echo the DB password back, even in a diagnostic.
            chk('database connection', 'FAIL',
                preg_replace('/password=\S+/i', 'password=***', $e->getMessage()));
        }
    }
}

/* ---------------- 5. Schema ---------------- */
if ($pdo instanceof PDO) {
    $want = ['admins','users','auth_tokens','devices','broker_accounts',
             'strategy_configs','bars','signals','trades','app_commands',
             'command_receipts','audit_log','engine_state'];
    $have = $pdo->query('SHOW TABLES')->fetchAll(PDO::FETCH_COLUMN) ?: [];
    $missing = array_diff($want, $have);
    chk('schema imported', $missing ? 'FAIL' : 'OK',
        $missing ? 'missing: ' . implode(', ', $missing)
                 : count($want) . ' tables present');

    if (!$missing) {
        $g = $pdo->query("SELECT payload FROM strategy_configs
                          WHERE scope='global' AND ref_id=''")->fetchColumn();
        chk('global config seeded', $g ? 'OK' : 'FAIL',
            $g ? 'trading_enabled=' . (json_decode((string)$g, true)['trading_enabled'] ? 'TRUE' : 'false')
               : 're-run the seed INSERT at the end of schema.sql');

        $admins = (int)$pdo->query('SELECT COUNT(*) FROM admins')->fetchColumn();
        chk('admin account', $admins > 0 ? 'OK' : 'FAIL',
            $admins > 0 ? "$admins operator(s)"
                        : 'run: php cron/create_admin.php you@example.com \'passphrase\' owner');

        /* ---------------- 6. Cron ---------------- */
        $last = $pdo->query("SELECT v FROM engine_state WHERE k='last_run'")->fetchColumn();
        if (!$last) {
            chk('engine cron', 'FAIL', 'engine.php has never run - add the cron entry');
        } else {
            $age = time() - (int)strtotime((string)$last . ' UTC');
            chk('engine cron',
                $age < 180 ? 'OK' : ($age < 3600 ? 'WARN' : 'FAIL'),
                'last tick ' . ($age < 120 ? "{$age}s" : round($age / 60) . ' min') .
                ' ago (' . $last . ' UTC)');
        }
    }
}

/* ---------------- 7. Outbound HTTPS ---------------- */
if (function_exists('curl_init')) {
    $ch = curl_init('https://api.github.com/');
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 8,
        CURLOPT_SSL_VERIFYPEER => true, CURLOPT_USERAGENT => 'gs-preflight',
    ]);
    curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err  = curl_error($ch);
    curl_close($ch);
    chk('outbound HTTPS', $code > 0 ? 'OK' : 'FAIL',
        $code > 0 ? "reachable (HTTP $code)"
                  : "blocked: $err — MetaApi will not work until the host allows outbound calls");
}

/* ---------------- 8. Execution locks ---------------- */
if (is_array($cfg)) {
    $ma = $cfg['metaapi'] ?? [];
    $en = $cfg['engine'] ?? [];
    $tokenSet = !empty($ma['token']) && !str_contains((string)$ma['token'], 'CHANGEME');
    chk('MetaApi token', $tokenSet ? 'OK' : 'WARN',
        $tokenSet ? 'set' : 'not set - accounts cannot be provisioned yet');
    chk('metaapi.enabled', !empty($ma['enabled']) ? 'ON' : 'OFF',
        !empty($ma['enabled']) ? 'orders CAN be placed' : 'no orders will be placed (safe default)');
    chk('engine.allow_live', !empty($en['allow_live']) ? 'ON' : 'OFF',
        !empty($en['allow_live']) ? 'LIVE accounts may trade' : 'demo only (safe default)');
    chk('max lot clamp', 'INFO', (string)($en['max_lot_per_trade'] ?? '0.50'));
}

/* ---------------- 9. Web-root exposure ---------------- */
$leaks = [];
foreach (['lib/bootstrap.php', 'cron/engine.php', 'schema.sql', 'config.php'] as $f) {
    if (is_readable(__DIR__ . '/' . $f)) $leaks[] = $f;
}
chk('sensitive files present in web root', $leaks ? 'INFO' : 'OK',
    $leaks ? implode(', ', $leaks) . ' — .htaccess must be blocking these; verify by opening one in a browser'
           : 'none');

/* ================================================================== */
$overall = $fail > 0 ? 'NOT READY' : ($warn > 0 ? 'READY WITH WARNINGS' : 'READY');
$colour  = $fail > 0 ? '#F87171' : ($warn > 0 ? '#FBBF24' : '#34D399');
?><!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Preflight · Gold Scalpers App</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0A0E1A;color:#F4F6FA;padding:2rem 1.2rem}
.wrap{max-width:820px;margin:0 auto}
h1{font-size:1.5rem;font-weight:800;margin-bottom:.2rem}
.sub{color:#64748B;font-size:.88rem;margin-bottom:1.4rem}
.banner{border-radius:12px;padding:1rem 1.2rem;margin-bottom:1.4rem;
        border:1px solid <?= $colour ?>44;background:<?= $colour ?>1A}
.banner b{color:<?= $colour ?>;font-size:1.1rem}
table{width:100%;border-collapse:collapse;background:#121826;
      border:1px solid #1F2937;border-radius:12px;overflow:hidden}
th{text-align:left;font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;
   color:#64748B;padding:.6rem .8rem;border-bottom:1px solid #1F2937}
td{padding:.6rem .8rem;border-bottom:1px solid #1F293799;vertical-align:top;font-size:.9rem}
tr:last-child td{border-bottom:none}
.s{font-weight:800;font-size:.75rem;padding:.15rem .5rem;border-radius:99px;white-space:nowrap}
.OK{background:#34D39922;color:#34D399}
.FAIL{background:#F8717122;color:#F87171}
.WARN{background:#FBBF2422;color:#FBBF24}
.ON{background:#F8717122;color:#F87171}
.OFF{background:#94A3B822;color:#94A3B8}
.INFO{background:#94A3B822;color:#94A3B8}
.d{color:#94A3B8;font-size:.85rem}
.note{color:#64748B;font-size:.82rem;margin-top:1.2rem}
code{background:#F59E0B1A;color:#FCD34D;padding:.1em .35em;border-radius:4px;
     font-family:ui-monospace,Menlo,monospace;font-size:.85em}
</style></head><body><div class="wrap">
<h1>Deployment preflight</h1>
<p class="sub"><?= date('Y-m-d H:i:s') ?> UTC<?= $configPath ? ' · config: ' . htmlspecialchars(basename($configPath)) : '' ?></p>

<div class="banner"><b><?= $overall ?></b>
  <?php if ($fail): ?> — <?= $fail ?> blocking issue<?= $fail > 1 ? 's' : '' ?><?php endif; ?>
  <?php if ($warn): ?> · <?= $warn ?> warning<?= $warn > 1 ? 's' : '' ?><?php endif; ?>
</div>

<table>
<thead><tr><th>Check</th><th>State</th><th>Detail</th></tr></thead>
<tbody>
<?php foreach ($checks as $c): ?>
<tr>
  <td><?= htmlspecialchars($c['name']) ?></td>
  <td><span class="s <?= $c['state'] ?>"><?= $c['state'] ?></span></td>
  <td class="d"><?= htmlspecialchars($c['detail']) ?></td>
</tr>
<?php endforeach; ?>
</tbody>
</table>

<p class="note">Delete <code>preflight.php</code> once everything is green.</p>
</div></body></html>
