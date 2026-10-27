<?php
// =====================================================================
//  Gold Scalpers panel - AI settings
//  -------------------------------------------------------------------
//  Paste the model API key(s) here; they are saved to .ai-config.php
//  ABOVE public_html (survives deploys, never in git). hotstory.php and
//  ai-analyze.php read that file on every request, so a saved key starts
//  working at once. "Test" calls each configured service with a one-word
//  prompt. Login is the panel login (was /ai-settings.php until 2026-10-26).
// =====================================================================
@ini_set('display_errors', '0');
require __DIR__ . '/_common.php';
panel_require_login();

$CFG_CANDIDATES = array(
  $PRIVATE . '/.ai-config.php',      // preferred - survives deploys
  $SITE_ROOT . '/.ai-config.php',    // wiped on every deploy
);
$CFG_FILE = $CFG_CANDIDATES[0];
foreach ($CFG_CANDIDATES as $cand) { if (is_readable($cand)) { $CFG_FILE = $cand; break; } }

$PROVIDERS = array(
  'gemini'    => array('label' => 'Google Gemini (free tier)', 'model' => 'gemini-3.8-flash', 'prefix' => 'AIza',
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
function mask($k) { $k = (string)$k; if ($k === '') return '(none)'; if (strlen($k) < 12) return '****'; return substr($k, 0, 6) . '...' . substr($k, -4); }

function cfg_read($file) {
  if (!is_readable($file)) return array();
  if (function_exists('opcache_invalidate')) @opcache_invalidate($file, true);   // never a cached copy of the settings
  $c = @include $file;
  return is_array($c) ? $c : array();
}
function cfg_write($file, $arr) {
  $php = "<?php\n// Written by /panel/ai.php on " . gmdate('c') . " - do not commit this file.\nreturn " . var_export($arr, true) . ";\n";
  $ok = @file_put_contents($file, $php, LOCK_EX);
  if ($ok !== false) { @chmod($file, 0600); if (function_exists('opcache_invalidate')) @opcache_invalidate($file, true); }
  return $ok !== false;
}

// One-word round trip to a provider. Returns array(ok, detail).
function model_ping($provider, $model, $key) {
  $SYS = 'Reply with exactly the single word OK and nothing else.';
  if ($provider === 'gemini') {
    $url  = 'https://generativelanguage.googleapis.com/v1beta/models/' . rawurlencode($model) . ':generateContent';
    $hdrs = array('Content-Type: application/json', 'x-goog-api-key: ' . $key);
    $body = array('system_instruction' => array('parts' => array(array('text' => $SYS))),
                  'contents' => array(array('role' => 'user', 'parts' => array(array('text' => 'ping')))),
                  'generationConfig' => array('temperature' => 0, 'maxOutputTokens' => 400));
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
  $t0 = microtime(true); $raw = false; $code = 0; $err = ''; $tries = 0;
  for ($try = 1; $try <= 3; $try++) {
    $tries = $try;
    $ch = curl_init($url);
    curl_setopt_array($ch, array(CURLOPT_RETURNTRANSFER => true, CURLOPT_POST => true, CURLOPT_HTTPHEADER => $hdrs,
                                 CURLOPT_POSTFIELDS => json_encode($body), CURLOPT_TIMEOUT => 25));
    $raw  = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err  = curl_error($ch);
    curl_close($ch);
    if ($raw !== false && $code >= 200 && $code <= 299) break;
    if ($raw !== false && $code !== 429 && $code < 500) break;
    if ($try < 3) sleep(3);
  }
  $ms = (int)round((microtime(true) - $t0) * 1000);
  if ($raw === false) return array(false, 'could not reach the service: ' . $err);
  $j = json_decode($raw, true);
  if ($code < 200 || $code > 299) {
    $why = '';
    if (isset($j['error']['message'])) $why = $j['error']['message'];
    elseif (isset($j['message'])) $why = $j['message'];
    return array(false, 'HTTP ' . $code . ($why !== '' ? ' - ' . substr($why, 0, 160) : '') . ($tries > 1 ? ' (after ' . $tries . ' attempts)' : ''));
  }
  $text = '';
  if (isset($j['content']) && is_array($j['content'])) { foreach ($j['content'] as $b) if (isset($b['text'])) $text .= $b['text']; }
  if ($text === '' && isset($j['choices'][0]['message']['content'])) $text = (string)$j['choices'][0]['message']['content'];
  if ($text === '' && isset($j['candidates'][0]['content']['parts'])) { foreach ($j['candidates'][0]['content']['parts'] as $p) { if (!empty($p['thought'])) continue; if (isset($p['text'])) $text .= $p['text']; } }
  if ($text === '' && isset($j['candidates'][0]['finishReason'])) return array(true, 'reached the service (HTTP 200) but the reply had no text - finishReason ' . $j['candidates'][0]['finishReason'] . ', ' . $ms . ' ms');
  return array(true, 'answered "' . substr(trim($text), 0, 40) . '" in ' . $ms . ' ms');
}

$cfg = cfg_read($CFG_FILE);
$msgs = array(); $errs = array();
$csrf = $_SESSION['csrf'];
$post = ($_SERVER['REQUEST_METHOD'] === 'POST');
$action = $post && isset($_POST['action']) ? (string)$_POST['action'] : '';
if ($post && !csrf_ok()) { $errs[] = 'The form expired - please try again.'; $action = ''; }
$authed = true;

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

$models = null;
if ($authed && $action === 'models') {
  $models = array();
  if (!empty($cfg['key']) && isset($cfg['provider']) && $cfg['provider'] === 'gemini') {
    $ch = curl_init('https://generativelanguage.googleapis.com/v1beta/models?pageSize=200');
    curl_setopt_array($ch, array(CURLOPT_RETURNTRANSFER => true, CURLOPT_HTTPHEADER => array('x-goog-api-key: ' . $cfg['key']), CURLOPT_TIMEOUT => 25));
    $raw = curl_exec($ch); $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE); curl_close($ch);
    $j = json_decode((string)$raw, true);
    if ($code === 200 && isset($j['models']) && is_array($j['models'])) {
      foreach ($j['models'] as $m) {
        $name = isset($m['name']) ? preg_replace('~^models/~', '', $m['name']) : '';
        $gen  = isset($m['supportedGenerationMethods']) && in_array('generateContent', $m['supportedGenerationMethods'], true);
        if ($name !== '' && $gen && stripos($name, 'gemini') !== false) $models[] = $name;
      }
      sort($models);
    } else $errs[] = 'Could not list models: HTTP ' . $code . (isset($j['error']['message']) ? ' - ' . substr($j['error']['message'], 0, 120) : '');
  } else $errs[] = 'Listing models needs a saved Gemini key as the primary provider.';
}

if ($authed && $action === 'reread') {
  $flag = $PRIVATE . '/.hotstory_reread';
  if (!is_writable(dirname($flag))) $flag = $SITE_ROOT . '/.hotstory_reread';
  if (@file_put_contents($flag, gmdate('c')) !== false)
    $msgs[] = 'Re-read requested. The reader picks it up on its next run (within 5 minutes) and re-analyses the current story with the full article; the news row updates within 2 minutes after that.';
  else $errs[] = 'Could not write the re-read flag.';
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
panel_head('AI settings');
panel_tabs('ai');
?>
<div class="card"><h1 style="margin:0 0 .3rem">AI settings</h1><p class="muted" style="margin:0">Keys for the news reader and the chart analysis. Saved outside the web root, never in git.</p></div>
<?php foreach ($errs as $e): ?><div class="msg bad"><?= h($e) ?></div><?php endforeach; ?>
<?php foreach ($msgs as $m): ?><div class="msg ok"><?= h($m) ?></div><?php endforeach; ?>

<div class="card"><h2>Current status</h2><table>
<tr><td>Config file</td><td><code><?= h($CFG_FILE) ?></code> <?= is_readable($CFG_FILE) ? '(found)' : '(not created yet)' ?></td></tr>
<tr><td>Primary</td><td><?= h($curP) ?> &middot; <?= h($curM !== '' ? $curM : '(default model)') ?> &middot; key <?= h(mask(isset($cfg['key']) ? $cfg['key'] : '')) ?></td></tr>
<tr><td>Fallback</td><td><?= $curFP !== '' ? h($curFP) . ' &middot; ' . h($curFM !== '' ? $curFM : '(default model)') . ' &middot; key ' . h(mask($cfg['fallback']['key'])) : '(none)' ?></td></tr>
<tr><td>Checks</td><td><a href="/hotstory.php?selftest=1" target="_blank">news reader self-test</a> &middot; <a href="/ai-analyze.php?selftest=1" target="_blank">chart analysis self-test</a> &middot; <a href="/hotstory.txt" target="_blank">current hot story</a></td></tr>
</table>
<form method="post" style="display:inline"><input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="action" value="test"><button class="btn">Test the saved keys now</button></form>
<form method="post" style="display:inline;margin-left:8px"><input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="action" value="models"><button class="btn alt">Show the Gemini models this key can use</button></form>
<form method="post" style="display:inline;margin-left:8px"><input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="action" value="reread"><button class="btn alt">Re-read the current hot story now</button></form>
<?php if (is_array($models)): ?><div class="msg ok" style="margin-top:10px"><b>Models available to this key</b> (type one into the Model box and Save; the "flash" ones are the fast, free-tier choices):<br><?= $models ? h(implode('  ·  ', $models)) : '(none returned)' ?></div><?php endif; ?>
<?php foreach ($tests as $name => $t): ?><div class="msg <?= $t[0] ? 'ok' : 'bad' ?>" style="margin-top:10px"><b><?= h($name) ?></b>: <?= h($t[1]) ?></div><?php endforeach; ?>
</div>

<div class="card"><h2>Keys</h2>
<form method="post"><input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="action" value="save">
<div class="row"><div><label for="provider">Primary provider</label><select id="provider" name="provider">
<?php foreach ($PROVIDERS as $k => $v): ?><option value="<?= h($k) ?>"<?= $curP === $k ? ' selected' : '' ?>><?= h($v['label']) ?></option><?php endforeach; ?></select>
<div class="hint">Gemini: free key at aistudio.google.com/apikey. Anthropic: console.anthropic.com. OpenAI: platform.openai.com.</div></div>
<div><label for="model">Model</label><input id="model" name="model" value="<?= h($curM) ?>" placeholder="gemini-3.8-flash / claude-sonnet-5 / gpt-4o"><div class="hint">Leave blank for the provider's default.</div></div></div>
<label for="key">Primary API key</label><input id="key" name="key" type="password" autocomplete="off" placeholder="<?= isset($cfg['key']) && $cfg['key'] !== '' ? 'saved - leave blank to keep ' . h(mask($cfg['key'])) : 'paste the key' ?>">
<hr style="border:0;border-top:1px solid var(--border);margin:18px 0">
<div class="row"><div><label for="f_provider">Fallback provider (optional)</label><select id="f_provider" name="f_provider"><option value="">none</option>
<?php foreach ($PROVIDERS as $k => $v): ?><option value="<?= h($k) ?>"<?= $curFP === $k ? ' selected' : '' ?>><?= h($v['label']) ?></option><?php endforeach; ?></select>
<div class="hint">Used only when the primary fails: no credit, rate limit, outage.</div></div>
<div><label for="f_model">Fallback model</label><input id="f_model" name="f_model" value="<?= h($curFM) ?>" placeholder="blank = default"></div></div>
<label for="f_key">Fallback API key</label><input id="f_key" name="f_key" type="password" autocomplete="off" placeholder="<?= $curFP !== '' ? 'saved - leave blank to keep ' . h(mask($cfg['fallback']['key'])) : 'paste the key' ?>">
<button class="btn">Save</button></form></div>

<?php panel_foot();
