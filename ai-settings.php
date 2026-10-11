<?php
// =====================================================================
//  Gold Scalpers - AI settings panel  (/ai-settings.php)
//  ---------------------------------------------------------------
//  Owner-only page: paste the model API key(s) here and they are saved
//  to .ai-config.php ABOVE public_html (survives deploys, never in git).
//  hotstory.php and ai-analyze.php read that file on every request, so a
//  saved key starts working at once. A "Test" button calls each configured
//  service with a one-word prompt and reports what it answered.
//
//  Login: the admin password. Its SHA-256 lives below; the panel can change
//  it (the new hash is stored in the config file and overrides this one).
//  Five wrong passwords lock the page for 15 minutes.
// =====================================================================

@ini_set('display_errors', '0');
@ini_set('session.cookie_httponly', '1');
@ini_set('session.cookie_secure', '1');
@ini_set('session.use_strict_mode', '1');
header('X-Robots-Tag: noindex, nofollow');
header('X-Frame-Options: DENY');
header('Cache-Control: no-store');
session_start();

$ADMIN_SHA_BUILTIN = 'a9519b8e817944c629ca072977e500ec929ebc59919a7468786ff63dcb027ae9';
$CFG_CANDIDATES = array(
  dirname(__DIR__) . '/.ai-config.php',   // preferred - survives deploys
  __DIR__ . '/.ai-config.php',            // wiped on every deploy
);
$CFG_FILE = $CFG_CANDIDATES[0];
foreach ($CFG_CANDIDATES as $cand) { if (is_readable($cand)) { $CFG_FILE = $cand; break; } }
$LOCK_FILE = dirname(__DIR__) . '/.ai-settings-lock.json';
if (!is_writable(dirname($LOCK_FILE))) $LOCK_FILE = __DIR__ . '/.ai-settings-lock.json';

$PROVIDERS = array(
  'gemini'    => array('label' => 'Google Gemini (free tier)', 'model' => 'gemini-2.5-flash', 'prefix' => 'AIza',
                       'where' => 'aistudio.google.com/apikey'),
  'anthropic' => array('label' => 'Anthropic Claude',          'model' => 'claude-sonnet-5',  'prefix' => 'sk-ant-',
                       'where' => 'console.anthropic.com'),
  'openai'    => array('label' => 'OpenAI',                    'model' => 'gpt-4o',           'prefix' => 'sk-',
                       'where' => 'platform.openai.com'),
);

// A model name must look like one of the provider's: gemini-*, claude-*, gpt-*/o*.
function model_fits($provider, $model) {
  $m = strtolower($model);
  if ($provider === 'gemini')    return strpos($m, 'gemini') === 0;
  if ($provider === 'anthropic') return strpos($m, 'claude') === 0;
  return strpos($m, 'gpt') === 0 || preg_match('~^o\d~', $m) === 1;
}
function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function mask($k) { $k = (string)$k; if ($k === '') return '(none)'; if (strlen($k) < 12) return '****'; return substr($k, 0, 6) . '...' . substr($k, -4); }

function cfg_read($file) {
  if (!is_readable($file)) return array();
  $c = @include $file;
  return is_array($c) ? $c : array();
}
function cfg_write($file, $arr) {
  $php = "<?php\n// Written by /ai-settings.php on " . gmdate('c') . " - do not commit this file.\nreturn " . var_export($arr, true) . ";\n";
  $ok = @file_put_contents($file, $php, LOCK_EX);
  if ($ok !== false) @chmod($file, 0600);
  return $ok !== false;
}
function lock_read($f) {
  $d = is_readable($f) ? json_decode((string)@file_get_contents($f), true) : null;
  return is_array($d) ? $d : array('fails' => 0, 'until' => 0);
}
function lock_write($f, $d) { @file_put_contents($f, json_encode($d)); }

// One-word round trip to a provider. Returns array(ok, detail).
function model_ping($provider, $model, $key) {
  $SYS = 'Reply with exactly the single word OK and nothing else.';
  if ($provider === 'gemini') {
    $url  = 'https://generativelanguage.googleapis.com/v1beta/models/' . rawurlencode($model) . ':generateContent';
    $hdrs = array('Content-Type: application/json', 'x-goog-api-key: ' . $key);
    $body = array('system_instruction' => array('parts' => array(array('text' => $SYS))),
                  'contents' => array(array('role' => 'user', 'parts' => array(array('text' => 'ping')))),
                  'generationConfig' => array('temperature' => 0, 'maxOutputTokens' => 20));
  } elseif ($provider === 'anthropic') {
    $url  = 'https://api.anthropic.com/v1/messages';
    $hdrs = array('content-type: application/json', 'x-api-key: ' . $key, 'anthropic-version: 2023-06-01');
    $body = array('model' => $model, 'max_tokens' => 20, 'system' => $SYS,
                  'messages' => array(array('role' => 'user', 'content' => 'ping')));
  } else {
    $url  = 'https://api.openai.com/v1/chat/completions';
    $hdrs = array('Content-Type: application/json', 'Authorization: Bearer ' . $key);
    $body = array('model' => $model, 'max_tokens' => 20, 'temperature' => 0,
                  'messages' => array(array('role' => 'system', 'content' => $SYS), array('role' => 'user', 'content' => 'ping')));
  }
  $t0 = microtime(true);
  $ch = curl_init($url);
  curl_setopt_array($ch, array(CURLOPT_RETURNTRANSFER => true, CURLOPT_POST => true, CURLOPT_HTTPHEADER => $hdrs,
                               CURLOPT_POSTFIELDS => json_encode($body), CURLOPT_TIMEOUT => 25));
  $raw  = curl_exec($ch);
  $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
  $err  = curl_error($ch);
  curl_close($ch);
  $ms = (int)round((microtime(true) - $t0) * 1000);
  if ($raw === false) return array(false, 'could not reach the service: ' . $err);
  $j = json_decode($raw, true);
  if ($code < 200 || $code > 299) {
    $why = '';
    if (isset($j['error']['message'])) $why = $j['error']['message'];
    elseif (isset($j['message'])) $why = $j['message'];
    return array(false, 'HTTP ' . $code . ($why !== '' ? ' - ' . substr($why, 0, 160) : ''));
  }
  $text = '';
  if (isset($j['content']) && is_array($j['content'])) { foreach ($j['content'] as $b) if (isset($b['text'])) $text .= $b['text']; }
  if ($text === '' && isset($j['choices'][0]['message']['content'])) $text = (string)$j['choices'][0]['message']['content'];
  if ($text === '' && isset($j['candidates'][0]['content']['parts'])) { foreach ($j['candidates'][0]['content']['parts'] as $p) if (isset($p['text'])) $text .= $p['text']; }
  return array(true, 'answered "' . substr(trim($text), 0, 40) . '" in ' . $ms . ' ms');
}

// ---------------------------------------------------------------- auth
$cfg = cfg_read($CFG_FILE);
$adminSha = (isset($cfg['admin_sha']) && strlen($cfg['admin_sha']) === 64) ? $cfg['admin_sha'] : $ADMIN_SHA_BUILTIN;
$msgs = array(); $errs = array();
if (empty($_SESSION['csrf'])) $_SESSION['csrf'] = bin2hex(random_bytes(16));
$csrf = $_SESSION['csrf'];
$post = ($_SERVER['REQUEST_METHOD'] === 'POST');
$action = $post && isset($_POST['action']) ? (string)$_POST['action'] : '';
if ($post && (!isset($_POST['csrf']) || !hash_equals($csrf, (string)$_POST['csrf']))) { $errs[] = 'The form expired - please try again.'; $action = ''; }

if ($action === 'logout') { $_SESSION = array(); session_destroy(); header('Location: ai-settings.php'); exit; }

if ($action === 'login') {
  $lock = lock_read($LOCK_FILE);
  if ((int)$lock['until'] > time()) {
    $errs[] = 'Locked after too many wrong passwords. Try again in ' . (int)ceil(((int)$lock['until'] - time()) / 60) . ' minutes.';
  } else {
    usleep(300000);
    $pw = isset($_POST['password']) ? (string)$_POST['password'] : '';
    if ($pw !== '' && hash_equals($adminSha, hash('sha256', $pw))) {
      session_regenerate_id(true);
      $_SESSION['auth'] = true;
      lock_write($LOCK_FILE, array('fails' => 0, 'until' => 0));
    } else {
      $lock['fails'] = (int)$lock['fails'] + 1;
      if ($lock['fails'] >= 5) { $lock['until'] = time() + 900; $lock['fails'] = 0; }
      lock_write($LOCK_FILE, $lock);
      $errs[] = 'Wrong password.';
    }
  }
}
$authed = !empty($_SESSION['auth']);

// ---------------------------------------------------------------- actions
if ($authed && $action === 'save') {
  $new = $cfg;
  $p = isset($_POST['provider']) ? strtolower(trim((string)$_POST['provider'])) : '';
  if (!isset($PROVIDERS[$p])) { $errs[] = 'Choose a provider.'; }
  else {
    $key = isset($_POST['key']) ? trim((string)$_POST['key']) : '';
    if ($key === '' && isset($cfg['key']) && (!isset($cfg['provider']) || $cfg['provider'] === $p)) $key = trim((string)$cfg['key']);
    if ($key === '') $errs[] = 'Paste the ' . $PROVIDERS[$p]['label'] . ' API key.';
    $model = isset($_POST['model']) ? trim((string)$_POST['model']) : '';
    if ($model === '' || !model_fits($p, $model)) {
      if ($model !== '') $msgs[] = '"' . $model . '" is not a ' . $PROVIDERS[$p]['label'] . ' model name, so the default ' . $PROVIDERS[$p]['model'] . ' was used.';
      $model = $PROVIDERS[$p]['model'];
    }
    $new['provider'] = $p; $new['key'] = $key; $new['model'] = $model;

    $fp = isset($_POST['f_provider']) ? strtolower(trim((string)$_POST['f_provider'])) : '';
    if ($fp === '' || !isset($PROVIDERS[$fp])) { unset($new['fallback']); }
    else {
      $fkey = isset($_POST['f_key']) ? trim((string)$_POST['f_key']) : '';
      if ($fkey === '' && isset($cfg['fallback']['key']) && (!isset($cfg['fallback']['provider']) || $cfg['fallback']['provider'] === $fp))
        $fkey = trim((string)$cfg['fallback']['key']);
      $fmodel = isset($_POST['f_model']) ? trim((string)$_POST['f_model']) : '';
      if ($fmodel === '' || !model_fits($fp, $fmodel)) $fmodel = $PROVIDERS[$fp]['model'];
      if ($fkey === '') $errs[] = 'The fallback needs a key too, or set its provider to "none".';
      else $new['fallback'] = array('provider' => $fp, 'key' => $fkey, 'model' => $fmodel);
    }
  }
  if (empty($errs)) {
    $target = $CFG_CANDIDATES[0];
    if (!is_writable(dirname($target)) && !is_writable($target)) { $target = $CFG_CANDIDATES[1]; $msgs[] = 'Note: the folder above public_html is not writable, so the file was saved inside public_html - it will be wiped by the next site deploy.'; }
    if (cfg_write($target, $new)) { $msgs[] = 'Saved. The news reader and the chart analysis use the new settings from their next call.'; $CFG_FILE = $target; $cfg = cfg_read($CFG_FILE); }
    else $errs[] = 'Could not write ' . $target . ' - check folder permissions.';
  }
}

if ($authed && $action === 'setpass') {
  $a = isset($_POST['pass1']) ? (string)$_POST['pass1'] : ''; $b = isset($_POST['pass2']) ? (string)$_POST['pass2'] : '';
  if (strlen($a) < 10) $errs[] = 'Use at least 10 characters.';
  elseif ($a !== $b) $errs[] = 'The two passwords differ.';
  else {
    $new = $cfg; $new['admin_sha'] = hash('sha256', $a);
    if (cfg_write($CFG_FILE, $new)) { $msgs[] = 'Password changed.'; $cfg = $new; }
    else $errs[] = 'Could not write the config file.';
  }
}

$tests = array();
if ($authed && $action === 'test') {
  if (!empty($cfg['key'])) {
    $p = isset($cfg['provider']) ? $cfg['provider'] : 'anthropic';
    $m = !empty($cfg['model']) ? $cfg['model'] : $PROVIDERS[isset($PROVIDERS[$p]) ? $p : 'anthropic']['model'];
    $tests['Primary - ' . $p . ' / ' . $m] = model_ping($p, $m, $cfg['key']);
  } else $tests['Primary'] = array(false, 'no key saved yet');
  if (!empty($cfg['fallback']['key'])) {
    $p = isset($cfg['fallback']['provider']) ? $cfg['fallback']['provider'] : 'anthropic';
    $m = !empty($cfg['fallback']['model']) ? $cfg['fallback']['model'] : $PROVIDERS[isset($PROVIDERS[$p]) ? $p : 'anthropic']['model'];
    $tests['Fallback - ' . $p . ' / ' . $m] = model_ping($p, $m, $cfg['fallback']['key']);
  }
}

$curP = isset($cfg['provider']) ? $cfg['provider'] : 'gemini';
$curM = isset($cfg['model']) ? $cfg['model'] : '';
$curFP = isset($cfg['fallback']['provider']) ? $cfg['fallback']['provider'] : '';
$curFM = isset($cfg['fallback']['model']) ? $cfg['fallback']['model'] : '';
?><!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>AI settings - Gold Scalpers</title>
<style>
:root{--bg:#F7F8FC;--card:#fff;--border:#E5E7EB;--gold:#D97706;--text:#111827;--muted:#6B7280;--green:#059669;--red:#DC2626}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:24px 16px 48px}h1{font-size:1.4rem;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:16px}
h2{font-size:1.05rem;margin:0 0 12px}label{display:block;font-weight:600;font-size:.9rem;margin:12px 0 4px}
input,select{width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:8px;font:inherit;background:#fff}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:560px){.row{grid-template-columns:1fr}}
.btn{display:inline-block;background:var(--gold);color:#fff;border:0;border-radius:999px;padding:10px 20px;font:inherit;font-weight:700;cursor:pointer;margin-top:14px}
.btn.alt{background:#fff;color:var(--text);border:1px solid var(--border)}
.msg{padding:10px 14px;border-radius:8px;margin-bottom:10px;font-size:.92rem}.ok{background:#ECFDF5;color:#065F46;border:1px solid #A7F3D0}.bad{background:#FEF2F2;color:#991B1B;border:1px solid #FECACA}
.hint{color:var(--muted);font-size:.86rem;margin-top:4px}code{background:#F3F4F6;padding:1px 5px;border-radius:4px}
table{width:100%;border-collapse:collapse;font-size:.9rem}td{padding:6px 4px;border-top:1px solid var(--border);vertical-align:top}td:first-child{color:var(--muted);width:38%}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px}
</style></head><body><div class="wrap">
<div class="top"><div><h1>AI settings</h1><p class="sub">Keys for the news reader and the chart analysis. Saved outside the web root, never in git.</p></div>
<?php if ($authed): ?><form method="post"><input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="action" value="logout"><button class="btn alt" style="margin:0">Log out</button></form><?php endif; ?></div>
<?php foreach ($errs as $e): ?><div class="msg bad"><?= h($e) ?></div><?php endforeach; ?>
<?php foreach ($msgs as $m): ?><div class="msg ok"><?= h($m) ?></div><?php endforeach; ?>

<?php if (!$authed): ?>
<div class="card"><h2>Log in</h2>
<form method="post"><input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="action" value="login">
<label for="pw">Admin password</label><input id="pw" name="password" type="password" autocomplete="current-password" autofocus>
<button class="btn">Log in</button></form></div>
<?php else: ?>

<div class="card"><h2>Current status</h2><table>
<tr><td>Config file</td><td><code><?= h($CFG_FILE) ?></code> <?= is_readable($CFG_FILE) ? '(found)' : '(not created yet)' ?></td></tr>
<tr><td>Primary</td><td><?= h($curP) ?> &middot; <?= h($curM !== '' ? $curM : '(default model)') ?> &middot; key <?= h(mask(isset($cfg['key']) ? $cfg['key'] : '')) ?></td></tr>
<tr><td>Fallback</td><td><?= $curFP !== '' ? h($curFP) . ' &middot; ' . h($curFM !== '' ? $curFM : '(default model)') . ' &middot; key ' . h(mask($cfg['fallback']['key'])) : '(none)' ?></td></tr>
<tr><td>Checks</td><td><a href="hotstory.php?selftest=1" target="_blank">news reader self-test</a> &middot; <a href="ai-analyze.php?selftest=1" target="_blank">chart analysis self-test</a> &middot; <a href="hotstory.txt" target="_blank">current hot story</a></td></tr>
</table>
<form method="post"><input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="action" value="test"><button class="btn">Test the saved keys now</button></form>
<?php foreach ($tests as $name => $t): ?><div class="msg <?= $t[0] ? 'ok' : 'bad' ?>" style="margin-top:10px"><b><?= h($name) ?></b>: <?= h($t[1]) ?></div><?php endforeach; ?>
</div>

<div class="card"><h2>Keys</h2>
<form method="post"><input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="action" value="save">
<div class="row"><div><label for="provider">Primary provider</label><select id="provider" name="provider">
<?php foreach ($PROVIDERS as $k => $v): ?><option value="<?= h($k) ?>"<?= $curP === $k ? ' selected' : '' ?>><?= h($v['label']) ?></option><?php endforeach; ?></select>
<div class="hint">Gemini: free key at aistudio.google.com/apikey. Anthropic: console.anthropic.com. OpenAI: platform.openai.com.</div></div>
<div><label for="model">Model</label><input id="model" name="model" value="<?= h($curM) ?>" placeholder="gemini-2.5-flash / claude-sonnet-5 / gpt-4o"><div class="hint">Leave blank for the provider's default.</div></div></div>
<label for="key">Primary API key</label><input id="key" name="key" type="password" autocomplete="off" placeholder="<?= isset($cfg['key']) && $cfg['key'] !== '' ? 'saved - leave blank to keep ' . h(mask($cfg['key'])) : 'paste the key' ?>">
<hr style="border:0;border-top:1px solid var(--border);margin:18px 0">
<div class="row"><div><label for="f_provider">Fallback provider (optional)</label><select id="f_provider" name="f_provider"><option value="">none</option>
<?php foreach ($PROVIDERS as $k => $v): ?><option value="<?= h($k) ?>"<?= $curFP === $k ? ' selected' : '' ?>><?= h($v['label']) ?></option><?php endforeach; ?></select>
<div class="hint">Used only when the primary fails: no credit, rate limit, outage.</div></div>
<div><label for="f_model">Fallback model</label><input id="f_model" name="f_model" value="<?= h($curFM) ?>" placeholder="blank = default"></div></div>
<label for="f_key">Fallback API key</label><input id="f_key" name="f_key" type="password" autocomplete="off" placeholder="<?= $curFP !== '' ? 'saved - leave blank to keep ' . h(mask($cfg['fallback']['key'])) : 'paste the key' ?>">
<button class="btn">Save</button></form></div>

<div class="card"><h2>Change the admin password</h2>
<form method="post"><input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="action" value="setpass">
<div class="row"><div><label for="p1">New password</label><input id="p1" name="pass1" type="password" autocomplete="new-password"></div>
<div><label for="p2">Again</label><input id="p2" name="pass2" type="password" autocomplete="new-password"></div></div>
<button class="btn alt">Change password</button></form></div>
<?php endif; ?>
</div></body></html>
