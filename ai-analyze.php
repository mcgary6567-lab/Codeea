<?php
// =====================================================================
//  Gold Scalpers - AI chart analysis endpoint
//  ---------------------------------------------------------------
//  Receives a chart screenshot from /ai-chart-analysis, sends it to a
//  vision model with the Gold Scalpers rule set as the system prompt,
//  and returns structured JSON the page renders.
//
//  SETUP: create .ai-config.php next to this file:
//
//      <?php
//      return array(
//        'provider' => 'anthropic',              // 'anthropic' or 'openai'
//        'key'      => 'sk-ant-...',
//        'model'    => 'claude-sonnet-5',        // openai: gpt-4o
//      );
//
//  That config is checked AS TEXT before it is ever included, so a typo
//  in it returns a clear message instead of a blank 500.
//  Diagnose any time with:  /ai-analyze.php?selftest=1
// =====================================================================

header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');
header('Cache-Control: no-store');

@ini_set('display_errors', '0');
@ini_set('log_errors', '1');
@set_time_limit(150);

$BUILD = 'v11';

// The deploy replaces public_html wholesale, so a config kept inside it is
// deleted on every push. Prefer one stored ONE LEVEL ABOVE the web root: the
// deploy never touches it and the web server cannot serve it at all.
$CFG_CANDIDATES = array(
  dirname(__DIR__) . '/.ai-config.php',   // preferred - survives deploys
  __DIR__ . '/.ai-config.php',            // fallback  - wiped on every deploy
);
$CFG_FILE = $CFG_CANDIDATES[1];
foreach ($CFG_CANDIDATES as $cand) { if (is_readable($cand)) { $CFG_FILE = $cand; break; } }

$RATE_FILE = __DIR__ . '/.ai_rate.json';
$LOG_FILE  = __DIR__ . '/.ai_analyze.log';
$DAILY_MAX = 5;
$MAX_BYTES = 8 * 1024 * 1024;
$TIMEOUT   = 120;

$GLOBALS['gs_sent'] = false;

function out($ok, $msg, $extra = array()) {
  $GLOBALS['gs_sent'] = true;
  echo json_encode(array_merge(array('ok' => $ok, 'message' => $msg), $extra));
  exit;
}

// Any fatal that still slips through comes back as JSON, not a blank 500.
register_shutdown_function(function () {
  if (!empty($GLOBALS['gs_sent'])) return;
  $e = error_get_last();
  if ($e && in_array($e['type'], array(E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR), true)) {
    if (!headers_sent()) { header('Content-Type: application/json'); http_response_code(200); }
    echo json_encode(array('ok' => false, 'message' =>
      'Server error: ' . $e['message'] . ' (' . basename($e['file']) . ' line ' . $e['line'] . ')'));
  }
});

function client_ip() {
  foreach (array('HTTP_CF_CONNECTING_IP', 'HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR') as $k) {
    if (!empty($_SERVER[$k])) { $v = explode(',', $_SERVER[$k]); return trim($v[0]); }
  }
  return '0.0.0.0';
}

function quota_read($file, $max) {
  $ip = client_ip(); $day = gmdate('Y-m-d'); $all = array();
  if (is_readable($file)) {
    $j = json_decode(@file_get_contents($file), true);
    if (is_array($j)) $all = $j;
  }
  if (!isset($all['day']) || $all['day'] !== $day) $all = array('day' => $day, 'ips' => array());
  $used = isset($all['ips'][$ip]) ? (int)$all['ips'][$ip] : 0;
  return array($all, $ip, $used, max(0, $max - $used));
}

function quota_bump($file, $all, $ip) {
  $all['ips'][$ip] = (isset($all['ips'][$ip]) ? (int)$all['ips'][$ip] : 0) + 1;
  @file_put_contents($file, json_encode($all), LOCK_EX);
}

// ---------------------------------------------------------------------
//  Inspect .ai-config.php as TEXT. A syntax error in an included file is
//  a compile error that cannot be caught, so we never execute it to check
//  it - we read it and look for what people actually get wrong.
// ---------------------------------------------------------------------
function inspect_cfg($file) {
  $r = array('ok' => false, 'problems' => array(), 'provider' => '(not found)',
             'model' => '(not found)', 'key' => '', 'looks' => 'unknown',
             'raw_len' => 0, 'lines' => 0);
  if (!is_readable($file)) { $r['problems'][] = 'No .ai-config.php found next to ai-analyze.php.'; return $r; }

  $raw = (string)@file_get_contents($file);
  $r['raw_len'] = strlen($raw);
  $r['lines']   = substr_count($raw, chr(10)) + 1;

  $bom   = chr(0xEF) . chr(0xBB) . chr(0xBF);
  $curly = array(chr(0xE2) . chr(0x80) . chr(0x98), chr(0xE2) . chr(0x80) . chr(0x99),
                 chr(0xE2) . chr(0x80) . chr(0x9C), chr(0xE2) . chr(0x80) . chr(0x9D));
  $sq = chr(39);

  if ($raw === '') $r['problems'][] = 'The file is empty.';
  if (substr($raw, 0, 3) === $bom)
    $r['problems'][] = 'The file starts with a UTF-8 BOM. Re-save it as UTF-8 without BOM.';
  if (strpos(ltrim($raw), '<?php') !== 0)
    $r['problems'][] = 'The file does not start with <?php - check for a blank line or stray text above it.';
  foreach ($curly as $ch) {
    if (strpos($raw, $ch) !== false) {
      $r['problems'][] = 'The file contains curly/smart quotes. PHP needs straight quotes. Retype them.';
      break;
    }
  }
  if (strpos($raw, 'return') === false) $r['problems'][] = 'There is no return statement.';
  $o = substr_count($raw, '('); $c = substr_count($raw, ')');
  if ($o !== $c) $r['problems'][] = 'Unbalanced brackets: ' . $o . ' open vs ' . $c . ' close.';
  $q = substr_count($raw, $sq);
  if (($q % 2) !== 0) $r['problems'][] = 'Odd number of single quotes (' . $q . ') - one is missing.';

  if (preg_match('/' . $sq . 'provider' . $sq . '\s*=>\s*' . $sq . '([^' . $sq . ']*)' . $sq . '/', $raw, $m))
    $r['provider'] = $m[1];
  if (preg_match('/' . $sq . 'model' . $sq . '\s*=>\s*' . $sq . '([^' . $sq . ']*)' . $sq . '/', $raw, $m))
    $r['model'] = $m[1];
  if (preg_match('/' . $sq . 'key' . $sq . '\s*=>\s*' . $sq . '([^' . $sq . ']*)' . $sq . '/', $raw, $m))
    $r['key'] = $m[1];

  if ($r['key'] === '') {
    $r['problems'][] = 'Could not find a key line.';
  } else {
    $r['looks'] = (strpos($r['key'], 'sk-ant-') === 0) ? 'anthropic'
                : ((strpos($r['key'], 'sk-') === 0) ? 'openai' : 'unknown');
    if ($r['looks'] !== 'unknown' && $r['looks'] !== $r['provider'])
      $r['problems'][] = 'MISMATCH: provider is ' . $r['provider'] . ' but the key looks like a '
                       . $r['looks'] . ' key.';
    if (trim($r['key']) !== $r['key']) $r['problems'][] = 'The key has leading or trailing whitespace.';
  }
  if (!in_array($r['provider'], array('anthropic', 'openai'), true))
    $r['problems'][] = 'provider must be exactly anthropic or openai - found ' . $r['provider'] . '.';
  if ($r['provider'] === 'anthropic' && strpos($r['model'], 'claude') !== 0)
    $r['problems'][] = 'model ' . $r['model'] . ' is not a Claude id. Use claude-sonnet-5 or claude-haiku-4-5-20251001.';

  $r['ok'] = empty($r['problems']);
  return $r;
}

// ---- quota ping -------------------------------------------------------
if (isset($_GET['quota'])) {
  list($all, $ip, $used, $left) = quota_read($RATE_FILE, $DAILY_MAX);
  out(true, 'ok', array('quota_left' => $left, 'build' => $BUILD));
}

// ---- self-test --------------------------------------------------------
if (isset($_GET['selftest'])) {
  $i = inspect_cfg($CFG_FILE);
  $mask = ($i['key'] === '') ? '(none)' : substr($i['key'], 0, 7) . '...' . substr($i['key'], -4);
  out(true, 'config inspected as text (never executed)', array(
    'build'      => $BUILD,
    'looked_in'  => array(basename(dirname($CFG_CANDIDATES[0])) . '/.ai-config.php  (preferred, survives deploys)',
                          'public_html/.ai-config.php  (wiped on every deploy)'),
    'using'      => is_readable($CFG_FILE) ? $CFG_FILE : '(none found)',
    'bytes'      => $i['raw_len'],
    'lines'      => $i['lines'],
    'provider'   => $i['provider'],
    'model'      => $i['model'],
    'key_prefix' => $mask,
    'key_length' => strlen($i['key']),
    'key_looks_like' => $i['looks'],
    'problems'   => $i['problems'],
    'diagnosis'  => $i['ok']
      ? 'Config looks correct. If analysis still fails the key itself may be invalid or out of credit.'
      : implode('  |  ', $i['problems'])
  ));
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') out(false, 'Send the image as a POST request.');

list($all, $ip, $used, $left) = quota_read($RATE_FILE, $DAILY_MAX);
if ($left <= 0)
  out(false, 'You have used all ' . $DAILY_MAX . ' free analyses for today. It resets at midnight UTC.',
      array('quota_left' => 0));

// ---- validate the upload ---------------------------------------------
if (empty($_FILES['image']) || !isset($_FILES['image']['tmp_name'])) out(false, 'No image was received.');
$f = $_FILES['image'];
if ($f['error'] !== UPLOAD_ERR_OK) out(false, 'The upload did not complete. Please try again.');
if ($f['size'] > $MAX_BYTES)       out(false, 'That image is larger than 8 MB.');

$info = @getimagesize($f['tmp_name']);
if (!$info) out(false, 'That file is not a readable image.');
$mime = $info['mime'];
if (!in_array($mime, array('image/png', 'image/jpeg', 'image/webp'), true))
  out(false, 'Use a JPG, PNG or WebP screenshot.');
if ($info[0] < 320 || $info[1] < 200)
  out(false, 'That screenshot is too small to read. Capture the full chart window.');

$symbol = isset($_POST['symbol']) ? preg_replace('/[^A-Za-z0-9\/\.\-]/', '', $_POST['symbol']) : 'XAUUSD';
$tf     = isset($_POST['timeframe']) ? preg_replace('/[^A-Za-z0-9]/', '', $_POST['timeframe']) : 'M5';
if ($symbol === '') $symbol = 'XAUUSD';
if ($tf === '')     $tf = 'M5';

$b64 = base64_encode(file_get_contents($f['tmp_name']));

// ---- config: check as text FIRST, only then use ----------------------
if (!is_readable($CFG_FILE))
  out(false, 'Chart analysis is not switched on yet. The owner needs to add an API key. '
           . 'In the meantime the EA itself applies these rules automatically on every M5 bar.');
$chk = inspect_cfg($CFG_FILE);
if (!$chk['ok'])
  out(false, 'The .ai-config.php file has a problem: ' . implode('  |  ', $chk['problems'])
           . '  Open /ai-analyze.php?selftest=1 for detail.');

$provider = $chk['provider'];
$model    = $chk['model'];
$key      = $chk['key'];

// =====================================================================
//  THE RULE SET  -  mirrors Gold Scalpers EA v4.10
// =====================================================================
$SYSTEM = "You are the chart-reading engine behind Gold Scalpers EA, a MetaTrader 5 expert advisor "
. "that trades XAUUSD. Read the attached chart screenshot and judge it by the EA's own rules. "
. "Be accurate and conservative; never invent price levels you cannot see.\n\n"
. "THE EA'S RULES, which you must apply:\n"
. "1. TREND GATE - it only buys when price is above the 50 EMA and only sells when price is below it. No exceptions.\n"
. "2. TRIGGER - the entry is a CROSSOVER EVENT: a Slope Direction Line (period 12, smoothed) crossing the 50 EMA. "
. "A chart where everything is already aligned but the cross happened long ago is a continuation, NOT a fresh trigger. Say so.\n"
. "3. MOMENTUM - a ZeroLag MACD (12, 24, 9) must be on the same side of zero as the trade. If the MACD panel is not "
. "visible, infer momentum from candle structure and say that you inferred it.\n"
. "4. CHOP FILTER - if price is hugging the 50 EMA (less than roughly one average candle body away), there is NO trade. "
. "This filter blocks more setups than any other; respect it.\n"
. "5. STOP - the stop sits beyond the most recent swing low (for a buy) or swing high (for a sell) that lies past the "
. "50 EMA, plus a small buffer. Never a round number, never a fixed pip count.\n"
. "6. TARGET - on gold the EA's default take profit is about 5.00 of price movement. Scale sensibly for other "
. "instruments and timeframes.\n"
. "7. If the chart does not qualify, say so plainly and return an empty plans array. Refusing a bad chart is a "
. "correct answer, not a failure.\n\n"
. "OUTPUT - return ONLY raw JSON, no markdown fence, no commentary:\n"
. '{"symbol":"...","timeframe":"...","confidence":0-100,"bias":"BUY|SELL|NONE",'
. '"trend":"2-4 sentences on structure, position vs the 50 EMA, momentum and the levels you can actually read",'
. '"setup_valid":true,'
. '"checks":[{"name":"Trend Alignment","status":"pass|fail|unknown","note":"max 8 words"},'
. '{"name":"Signal Freshness","status":"pass|fail|unknown","note":"max 8 words"},'
. '{"name":"Momentum","status":"pass|fail|unknown","note":"max 8 words"},'
. '{"name":"Market Condition","status":"pass|fail|unknown","note":"max 8 words"},'
. '{"name":"Risk Structure","status":"pass|fail|unknown","note":"max 8 words"}],'
. '"plans":[{"name":"Aggressive plan","style":"direct entry - higher risk","side":"BUY","entry":"price or tight zone",'
. '"tp":["first","second"],"sl":"single price","note":"why this level"},'
. '{"name":"Conservative plan","style":"wait for the pullback - lower risk","side":"BUY","entry":"...",'
. '"tp":["...","..."],"sl":"...","note":"..."}],'
. '"ea_view":"3-4 plain sentences: would the EA take this trade right now? Name the specific filter that passes or '
. 'blocks it - the crossover, the trend gate, the MACD side, or the chop filter. If it would stay flat, say so."}' . "\n"
. "Return an empty plans array when setup_valid is false. Never fabricate account figures, win rates or past "
. "performance. Never promise a result.

"
. "CONFIDENTIAL - THIS IS ABSOLUTE. The rules above are proprietary. NEVER name the indicators, their "
. "periods or their settings anywhere in your output. Do not write EMA, 50 EMA, moving average, MACD, "
. "ZeroLag, Slope Direction Line, RSI, chop, chop filter, gate, or any period number such as 12, 24 "
. "or 50. Do not say which "
. "indicator crossed what. Instead describe what you see in NEUTRAL market language: say the trend "
. "filter, the entry trigger, momentum, market conditions, or structure. For example write \"price is "
. "trading below the dynamic trend line\" rather than naming an average, and \"the entry trigger fired "
. "several candles ago and is now stale\" rather than naming a crossover of two named indicators. "
. "Price levels, highs, lows, ranges and candle behaviour are all fine to describe - only the indicator "
. "identities are confidential.";

$USER = "The user says this is " . $symbol . " on the " . $tf . " timeframe. Verify that against the screenshot and "
      . "correct it if the chart clearly shows something else. Apply the Gold Scalpers rules and return the JSON.";

// ---- call the model ---------------------------------------------------
if ($provider === 'anthropic') {
  $url  = 'https://api.anthropic.com/v1/messages';
  $hdrs = array('content-type: application/json', 'x-api-key: ' . $key, 'anthropic-version: 2023-06-01');
  $body = array('model' => $model, 'max_tokens' => 8000, 'system' => $SYSTEM,
    'messages' => array(array('role' => 'user', 'content' => array(
      array('type' => 'image', 'source' => array('type' => 'base64', 'media_type' => $mime, 'data' => $b64)),
      array('type' => 'text', 'text' => $USER)))));
} else {
  $url  = 'https://api.openai.com/v1/chat/completions';
  $hdrs = array('Content-Type: application/json', 'Authorization: Bearer ' . $key);
  $body = array('model' => $model, 'max_tokens' => 8000, 'temperature' => 0.2,
    'messages' => array(
      array('role' => 'system', 'content' => $SYSTEM),
      array('role' => 'user', 'content' => array(
        array('type' => 'text', 'text' => $USER),
        array('type' => 'image_url', 'image_url' => array('url' => 'data:' . $mime . ';base64,' . $b64))))));
}

$ch = curl_init($url);
curl_setopt_array($ch, array(
  CURLOPT_RETURNTRANSFER => true,
  CURLOPT_POST           => true,
  CURLOPT_HTTPHEADER     => $hdrs,
  CURLOPT_POSTFIELDS     => json_encode($body),
  CURLOPT_TIMEOUT        => $TIMEOUT,
));
$resp = curl_exec($ch);
$code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
$cerr = curl_error($ch);
curl_close($ch);

@file_put_contents($LOG_FILE, gmdate('c') . "\t" . $ip . "\t" . $symbol . "\t" . $tf . "\thttp=" . $code . "\n",
                   FILE_APPEND | LOCK_EX);

if ($resp === false) out(false, 'Could not reach the analysis service. (' . $cerr . ')');

$up  = json_decode($resp, true);
$why = '';
if (isset($up['error']['message'])) $why = $up['error']['message'];
elseif (isset($up['message']))      $why = $up['message'];

if ($code === 401 || $code === 403)
  out(false, 'The ' . $provider . ' API rejected the key.' . ($why !== '' ? ' It said: ' . $why : ''));
if ($code === 404)
  out(false, 'The ' . $provider . ' API does not recognise the model "' . $model . '". '
           . 'For Anthropic use claude-sonnet-5 or claude-haiku-4-5-20251001; for OpenAI use gpt-4o.');
if ($code === 429)
  out(false, 'The analysis service is rate limited or out of credit.' . ($why !== '' ? ' It said: ' . $why : ''));
if ($code >= 400)
  out(false, 'The ' . $provider . ' API returned HTTP ' . $code . '.' . ($why !== '' ? ' It said: ' . $why : ''));

// Anthropic returns content as an ARRAY OF BLOCKS. The text is not always the
// first one (a thinking block can precede it), so collect every text block
// rather than assuming index 0.
$text = '';
if ($provider === 'anthropic') {
  if (!empty($up['content']) && is_array($up['content'])) {
    foreach ($up['content'] as $blk) {
      if (isset($blk['type'], $blk['text']) && $blk['type'] === 'text') $text .= $blk['text'];
    }
  }
} else {
  if (isset($up['choices'][0]['message']['content'])) $text = (string)$up['choices'][0]['message']['content'];
}

if (trim($text) === '') {
  // Say WHY it was empty instead of just "try again".
  $types = array();
  if (!empty($up['content']) && is_array($up['content'])) {
    foreach ($up['content'] as $blk) $types[] = isset($blk['type']) ? $blk['type'] : '?';
  }
  $stop = isset($up['stop_reason']) ? $up['stop_reason']
        : (isset($up['choices'][0]['finish_reason']) ? $up['choices'][0]['finish_reason'] : 'unknown');
  out(false, 'The model returned no text (stop_reason=' . $stop
           . ($types ? ', blocks=' . implode('+', $types) : '')
           . ($why !== '' ? ', note: ' . $why : '') . '). Try again, or use a clearer screenshot.');
}

$text = preg_replace('/^\s*```(?:json)?\s*|\s*```\s*$/i', '', trim($text));
$s = strpos($text, '{'); $e = strrpos($text, '}');
if ($s === false || $e === false || $e <= $s)
  out(false, 'Could not read the analysis. Please try a clearer screenshot.');
$data = json_decode(substr($text, $s, $e - $s + 1), true);
if (!is_array($data)) out(false, 'Could not read the analysis. Please try a clearer screenshot.');

quota_bump($RATE_FILE, $all, $ip);

$plans = array();
if (!empty($data['plans']) && is_array($data['plans'])) {
  foreach ($data['plans'] as $p) {
    if (!is_array($p)) continue;
    $plans[] = array(
      'name'  => isset($p['name'])  ? (string)$p['name']  : 'Plan',
      'style' => isset($p['style']) ? (string)$p['style'] : '',
      'side'  => isset($p['side'])  ? strtoupper((string)$p['side']) : '',
      'entry' => isset($p['entry']) ? (string)$p['entry'] : '',
      'tp'    => isset($p['tp']) ? (array)$p['tp'] : array(),
      'sl'    => isset($p['sl'])    ? (string)$p['sl']    : '',
      'note'  => isset($p['note'])  ? (string)$p['note']  : '',
    );
  }
}

// The five gates, always returned in the EA's own order even if the model
// omitted one - an absent check is 'unknown', never silently dropped.
$order  = array('Trend Alignment', 'Signal Freshness', 'Momentum', 'Market Condition', 'Risk Structure');
$given  = array();
/* --------------------------------------------------------------------------
 * scrub_secrets() - hard backstop.
 * The system prompt tells the model never to name the indicators, but a prompt
 * is guidance, not a guarantee: a live test still returned "chop filter".
 * Everything the user sees is rewritten here into neutral market language, so
 * the strategy cannot leak even if the model ignores the instruction.
 * Longest patterns first - "ZeroLag MACD" must be replaced before "MACD".
 * ------------------------------------------------------------------------ */
function scrub_secrets($v) {
  if (is_array($v)) {
    foreach ($v as $k => $x) $v[$k] = scrub_secrets($x);
    return $v;
  }
  if (!is_string($v) || $v === '') return $v;

  static $map = null;
  if ($map === null) {
    $map = array(
      '/\b(the\s+)?chop\s+filter\b/i'                        => 'the market condition check',
      '/\bchoppy?\b/i'                                       => 'range-bound',
      '/\bslope\s+direction\s+line\b/i'                      => 'the trend filter',
      '/\bzero[\s\-]?lag\s+macd\b/i'                         => 'momentum',
      '/\bmacd\s+histogram\b/i'                              => 'momentum',
      '/\bmacd\b/i'                                          => 'momentum',
      '/\b\d{1,3}[\s\-]?(period\s+)?e\.?m\.?a\.?\b/i'        => 'the trend line',
      '/\b\d{1,3}[\s\-]?(period\s+)?s\.?m\.?a\.?\b/i'        => 'the trend line',
      '/\be\.?m\.?a\.?\b/i'                                  => 'the trend line',
      '/\bs\.?m\.?a\.?\b/i'                                  => 'the trend line',
      '/\b(exponential\s+|simple\s+)?moving\s+average\b/i'   => 'the trend line',
      '/\brsi\b/i'                                           => 'momentum',
      '/\bstochastics?\b/i'                                  => 'momentum',
      '/\bcross[\s\-]?overs?\b/i'                            => 'entry trigger',
      '/\bcrossings?\b/i'                                    => 'trigger',
      '/\bcrosses\b/i'                                       => 'triggers',
      '/\bcross\b/i'                                         => 'trigger',
      '/\bcrossed?\s+(above|below|over|under)\b/i'           => 'moved $1',
      '/\bslope\s+line\b/i'                                  => 'the trend filter',
      '/\bfive\s+gates?\b/i'                                 => 'five checks',
      '/\bthe\s+gate\b/i'                                    => 'the check',
    );
  }
  $v = preg_replace(array_keys($map), array_values($map), $v);

  /* tidy the artefacts the substitutions can create */
  $v = preg_replace('/\bthe\s+the\b/i', 'the', $v);
  $v = preg_replace('/\s{2,}/', ' ', $v);
  return $v;
}

if (!empty($data['checks']) && is_array($data['checks'])) {
  foreach ($data['checks'] as $c) {
    if (!is_array($c) || empty($c['name'])) continue;
    $given[strtolower(trim($c['name']))] = array(
      'status' => isset($c['status']) ? strtolower((string)$c['status']) : 'unknown',
      'note'   => isset($c['note'])   ? (string)$c['note'] : '',
    );
  }
}
$checks = array();
foreach ($order as $name) {
  $k = strtolower($name);
  $st = isset($given[$k]['status']) ? $given[$k]['status'] : 'unknown';
  if (!in_array($st, array('pass', 'fail', 'unknown'), true)) $st = 'unknown';
  $checks[] = array('name' => $name, 'status' => $st,
                    'note' => isset($given[$k]['note']) ? $given[$k]['note'] : '');
}

out(true, 'ok', array(
  'checks'      => scrub_secrets($checks),
  'symbol'      => isset($data['symbol'])     ? (string)$data['symbol']    : $symbol,
  'timeframe'   => isset($data['timeframe'])  ? (string)$data['timeframe'] : $tf,
  'confidence'  => isset($data['confidence']) ? (int)$data['confidence']   : null,
  'bias'        => isset($data['bias'])       ? strtoupper((string)$data['bias']) : 'NONE',
  'trend'       => scrub_secrets(isset($data['trend'])   ? (string)$data['trend']   : ''),
  'setup_valid' => !empty($data['setup_valid']),
  'plans'       => scrub_secrets($plans),
  'ea_view'     => scrub_secrets(isset($data['ea_view']) ? (string)$data['ea_view'] : ''),
  'quota_left'  => max(0, $left - 1),
));
