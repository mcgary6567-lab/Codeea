<?php
// =====================================================================
//  Gold Scalpers - AI chart analysis endpoint
//  ---------------------------------------------------------------
//  Receives a chart screenshot from /ai-chart-analysis, sends it to a
//  vision model with the Gold Scalpers rule set as the system prompt,
//  and returns structured JSON the page renders.
//
//  SETUP (one step): put your API key in .ai-config.php next to this
//  file. That file is a dotfile, so the root .htaccess already denies
//  it to the web. Example:
//
//      <?php
//      return array(
//        'provider' => 'openai',                 // 'openai' or 'anthropic'
//        'key'      => 'sk-...',
//        'model'    => 'gpt-4o',                 // anthropic: claude-sonnet-4-20250514
//      );
//
//  Until that file exists the endpoint returns a clear "not configured"
//  message - it never invents an analysis.
// =====================================================================

header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');
header('Cache-Control: no-store');

$CFG_FILE   = __DIR__ . '/.ai-config.php';
$RATE_FILE  = __DIR__ . '/.ai_rate.json';   // dotfile -> denied by root .htaccess
$LOG_FILE   = __DIR__ . '/.ai_analyze.log'; // dotfile -> denied
$DAILY_MAX  = 5;                             // analyses per IP per day
$MAX_BYTES  = 8 * 1024 * 1024;               // 8 MB upload ceiling
$TIMEOUT    = 90;

function out($ok, $msg, $extra = array()) {
  echo json_encode(array_merge(array('ok' => $ok, 'message' => $msg), $extra));
  exit;
}

function client_ip() {
  foreach (array('HTTP_CF_CONNECTING_IP','HTTP_X_FORWARDED_FOR','REMOTE_ADDR') as $k) {
    if (!empty($_SERVER[$k])) {
      $v = explode(',', $_SERVER[$k]);
      return trim($v[0]);
    }
  }
  return '0.0.0.0';
}

// ---- daily per-IP quota -------------------------------------------------
function quota_read($file, $max) {
  $ip  = client_ip();
  $day = gmdate('Y-m-d');
  $all = array();
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

// ---- quota-only ping from the page ------------------------------------
if (isset($_GET['quota'])) {
  list($all, $ip, $used, $left) = quota_read($RATE_FILE, $DAILY_MAX);
  out(true, 'ok', array('quota_left' => $left));
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') out(false, 'Send the image as a POST request.');

list($all, $ip, $used, $left) = quota_read($RATE_FILE, $DAILY_MAX);
if ($left <= 0) {
  out(false, 'You have used all ' . $DAILY_MAX . ' free analyses for today. It resets at midnight UTC.',
      array('quota_left' => 0));
}

// ---- validate the upload ------------------------------------------------
if (empty($_FILES['image']) || !isset($_FILES['image']['tmp_name'])) out(false, 'No image was received.');
$f = $_FILES['image'];
if ($f['error'] !== UPLOAD_ERR_OK)  out(false, 'The upload did not complete. Please try again.');
if ($f['size'] > $MAX_BYTES)        out(false, 'That image is larger than 8 MB.');

$info = @getimagesize($f['tmp_name']);
if (!$info) out(false, 'That file is not a readable image.');
$mime = $info['mime'];
if (!in_array($mime, array('image/png','image/jpeg','image/webp'), true))
  out(false, 'Use a JPG, PNG or WebP screenshot.');
if ($info[0] < 320 || $info[1] < 200)
  out(false, 'That screenshot is too small to read. Capture the full chart window.');

$symbol = isset($_POST['symbol']) ? preg_replace('/[^A-Za-z0-9\/\.\-]/', '', $_POST['symbol']) : 'XAUUSD';
$tf     = isset($_POST['timeframe']) ? preg_replace('/[^A-Za-z0-9]/', '', $_POST['timeframe']) : 'M5';
if ($symbol === '') $symbol = 'XAUUSD';
if ($tf === '')     $tf     = 'M5';

$b64 = base64_encode(file_get_contents($f['tmp_name']));

// ---- config -------------------------------------------------------------
if (!is_readable($CFG_FILE)) {
  out(false, 'Chart analysis is not switched on yet. The owner needs to add an API key. '
           . 'In the meantime the EA itself applies these rules automatically on every M5 bar.');
}
$cfg = include $CFG_FILE;
if (!is_array($cfg) || empty($cfg['key'])) out(false, 'Chart analysis is not configured correctly.');
$provider = isset($cfg['provider']) ? $cfg['provider'] : 'openai';
$model    = isset($cfg['model']) ? $cfg['model'] : ($provider === 'anthropic' ? 'claude-sonnet-4-20250514' : 'gpt-4o');

// =====================================================================
//  THE RULE SET  -  mirrors Gold Scalpers EA v4.10
// =====================================================================
$SYSTEM = <<<TXT
You are the chart-reading engine behind Gold Scalpers EA, a MetaTrader 5 expert
advisor that trades XAUUSD. Read the attached chart screenshot and judge it by
the EA's own rules. Be accurate and conservative; never invent price levels you
cannot see.

THE EA'S RULES, which you must apply:
1. TREND GATE - it only buys when price is above the 50 EMA and only sells when
   price is below it. No exceptions.
2. TRIGGER - the entry is a CROSSOVER EVENT: a Slope Direction Line (period 12,
   smoothed) crossing the 50 EMA. A chart where everything is already aligned but
   the cross happened long ago is a continuation, NOT a fresh trigger. Say so.
3. MOMENTUM - a ZeroLag MACD (12, 24, 9) must be on the same side of zero as the
   trade. If the MACD panel is not visible, infer momentum from candle structure
   and say that you inferred it.
4. CHOP FILTER - if price is hugging the 50 EMA (less than roughly one average
   candle body away), there is NO trade. This filter blocks more setups than any
   other; respect it.
5. STOP - the stop sits beyond the most recent swing low (for a buy) or swing
   high (for a sell) that lies past the 50 EMA, plus a small buffer. Never a
   round number, never a fixed pip count.
6. TARGET - on gold the EA's default take profit is about \$5.00 of price
   movement. Scale sensibly for other instruments and timeframes.
7. If the chart does not qualify, say so plainly and return an empty plans array.
   Refusing a bad chart is a correct answer, not a failure.

OUTPUT - return ONLY raw JSON, no markdown fence, no commentary:
{
  "symbol": "the instrument you actually see, or the one supplied",
  "timeframe": "the timeframe you actually see, or the one supplied",
  "confidence": 0-100 integer - how clearly you can read the chart and how clean the setup is,
  "bias": "BUY" | "SELL" | "NONE",
  "trend": "2-4 sentences: structure, position vs the 50 EMA, momentum, and the levels you can actually read",
  "setup_valid": true or false,
  "plans": [
    {
      "name": "Aggressive plan",
      "style": "direct entry - higher risk",
      "side": "BUY" or "SELL",
      "entry": "a price or a tight zone, e.g. 4601 - 4604",
      "tp": ["first target", "second target"],
      "sl": "single stop price",
      "note": "one or two sentences on why this level"
    },
    {
      "name": "Conservative plan",
      "style": "wait for the pullback - lower risk",
      "side": "BUY" or "SELL",
      "entry": "...", "tp": ["...","..."], "sl": "...", "note": "..."
    }
  ],
  "ea_view": "3-4 sentences in plain English: would the EA actually take this trade right now? Name the specific filter that passes or blocks it - the crossover, the trend gate, the MACD side, or the chop filter. If it would stay flat, say so clearly."
}
Return an empty plans array when setup_valid is false. Never fabricate account
figures, win rates or past performance. Never promise a result.
TXT;

$USER = "The user says this is {$symbol} on the {$tf} timeframe. Verify that against the "
      . "screenshot and correct it if the chart clearly shows something else. Apply the "
      . "Gold Scalpers rules and return the JSON.";

// ---- call the model -----------------------------------------------------
if ($provider === 'anthropic') {
  $url  = 'https://api.anthropic.com/v1/messages';
  $hdrs = array('content-type: application/json',
                'x-api-key: ' . $cfg['key'],
                'anthropic-version: 2023-06-01');
  $body = array(
    'model' => $model, 'max_tokens' => 1600, 'system' => $SYSTEM,
    'messages' => array(array('role' => 'user', 'content' => array(
      array('type' => 'image', 'source' => array('type' => 'base64', 'media_type' => $mime, 'data' => $b64)),
      array('type' => 'text',  'text' => $USER)
    )))
  );
} else {
  $base = isset($cfg['base_url']) ? rtrim($cfg['base_url'], '/') : 'https://api.openai.com/v1';
  $url  = $base . '/chat/completions';
  $hdrs = array('Content-Type: application/json', 'Authorization: Bearer ' . $cfg['key']);
  $body = array(
    'model' => $model, 'max_tokens' => 1600, 'temperature' => 0.2,
    'messages' => array(
      array('role' => 'system', 'content' => $SYSTEM),
      array('role' => 'user', 'content' => array(
        array('type' => 'text', 'text' => $USER),
        array('type' => 'image_url', 'image_url' => array('url' => 'data:' . $mime . ';base64,' . $b64))
      ))
    )
  );
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
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$cerr = curl_error($ch);
curl_close($ch);

@file_put_contents($LOG_FILE, gmdate('c') . "\t$ip\t$symbol\t$tf\thttp=$code\n", FILE_APPEND | LOCK_EX);

if ($resp === false)  out(false, 'Could not reach the analysis service. Please try again. (' . $cerr . ')');
if ($code === 401 || $code === 403) out(false, 'The analysis service rejected the API key.');
if ($code === 429)    out(false, 'The analysis service is rate limited right now. Try again in a minute.');
if ($code >= 400)     out(false, 'The analysis service returned an error (HTTP ' . $code . ').');

$j = json_decode($resp, true);
$text = '';
if ($provider === 'anthropic') {
  if (!empty($j['content'][0]['text'])) $text = $j['content'][0]['text'];
} else {
  if (!empty($j['choices'][0]['message']['content'])) $text = $j['choices'][0]['message']['content'];
}
if ($text === '') out(false, 'The analysis service sent an empty reply. Please try again.');

// strip a ```json fence if the model added one, then take the outermost object
$text = preg_replace('/^\s*```(?:json)?\s*|\s*```\s*$/i', '', trim($text));
$s = strpos($text, '{'); $e = strrpos($text, '}');
if ($s === false || $e === false || $e <= $s) out(false, 'Could not read the analysis. Please try a clearer screenshot.');
$data = json_decode(substr($text, $s, $e - $s + 1), true);
if (!is_array($data)) out(false, 'Could not read the analysis. Please try a clearer screenshot.');

// ---- normalise + return -------------------------------------------------
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

out(true, 'ok', array(
  'symbol'      => isset($data['symbol'])    ? (string)$data['symbol']    : $symbol,
  'timeframe'   => isset($data['timeframe']) ? (string)$data['timeframe'] : $tf,
  'confidence'  => isset($data['confidence'])? (int)$data['confidence']   : null,
  'bias'        => isset($data['bias'])      ? strtoupper((string)$data['bias']) : 'NONE',
  'trend'       => isset($data['trend'])     ? (string)$data['trend']     : '',
  'setup_valid' => !empty($data['setup_valid']),
  'plans'       => $plans,
  'ea_view'     => isset($data['ea_view'])   ? (string)$data['ea_view']   : '',
  'quota_left'  => max(0, $left - 1),
));
