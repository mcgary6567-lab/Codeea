<?php
// =====================================================================
//  Gold Scalpers - ForexFactory "Hot Story" reader
//  ---------------------------------------------------------------
//  Reads the single hottest story of the last 12 hours from
//  forexfactory.com, asks the model which instrument it concerns and
//  which way it leans, and writes two small files the EA reads:
//
//      hotstory.txt    key=value, one per line  <- what the EA parses
//      hotstory.json   the same data as JSON    <- for the site / debugging
//
//  The EA never touches forexfactory.com. It reads hotstory.txt from
//  goldscalpers.com, which customers have already whitelisted for the
//  licence check, so this needs no extra setup from them.
//
//  KEY: reuses .ai-config.php - the same file /ai-analyze.php uses.
//  Nothing new to configure and no second secret.
//
//  COST CONTROL: the model is called ONLY when the story id changes, and
//  never more often than MIN_INTERVAL. A fresh page view costs nothing,
//  so this endpoint being public cannot run up a bill.
//
//  Run from cron every ~20 minutes:
//      curl -s https://goldscalpers.com/hotstory.php > /dev/null
//  Diagnose any time with:  /hotstory.php?selftest=1
// =====================================================================

header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');
header('Cache-Control: no-store');

@ini_set('display_errors', '0');
@ini_set('log_errors', '1');
@set_time_limit(120);

$BUILD = 'v2';

// Same config resolution as ai-analyze.php: one level above the web root
// survives the deploy, inside it does not.
$CFG_CANDIDATES = array(
  dirname(__DIR__) . '/.ai-config.php',
  __DIR__ . '/.ai-config.php',
);
$CFG_FILE = $CFG_CANDIDATES[1];
foreach ($CFG_CANDIDATES as $cand) { if (is_readable($cand)) { $CFG_FILE = $cand; break; } }

$STATE_FILE = __DIR__ . '/.hotstory_state.json';
$OUT_JSON   = __DIR__ . '/hotstory.json';
$OUT_TXT    = __DIR__ . '/hotstory.txt';
$LOG_FILE   = __DIR__ . '/.hotstory.log';

$MIN_INTERVAL = 600;          // seconds between real runs, whoever calls us
$TIMEOUT      = 25;           // per HTTP fetch
$UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    . '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';

// Gold plus the majors. Anything else the model must report as "none", so a
// story about oil or an index does not get forced onto a currency.
$INSTRUMENTS = array('XAUUSD','EURUSD','GBPUSD','USDJPY','USDCHF','USDCAD','AUDUSD','NZDUSD');

$GLOBALS['gs_sent'] = false;

function out($ok, $msg, $extra = array()) {
  $GLOBALS['gs_sent'] = true;
  echo json_encode(array_merge(array('ok' => $ok, 'message' => $msg), $extra));
  exit;
}

register_shutdown_function(function () {
  if (!empty($GLOBALS['gs_sent'])) return;
  $e = error_get_last();
  if ($e && in_array($e['type'], array(E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR))) {
    echo json_encode(array('ok' => false, 'message' => 'Server error: ' . $e['message']));
  }
});

function logline($file, $s) {
  @file_put_contents($file, gmdate('c') . '  ' . $s . chr(10), FILE_APPEND);
}

function fetch($url, $ua, $timeout) {
  $ch = curl_init($url);
  curl_setopt_array($ch, array(
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => true,
    CURLOPT_MAXREDIRS      => 3,
    CURLOPT_TIMEOUT        => $timeout,
    CURLOPT_USERAGENT      => $ua,
    CURLOPT_ENCODING       => '',            // accept gzip/br like a browser
    CURLOPT_HTTPHEADER     => array(
      'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
      'Accept-Language: en-US,en;q=0.9',
      'Upgrade-Insecure-Requests: 1',
      'Sec-Fetch-Dest: document',
      'Sec-Fetch-Mode: navigate',
      'Sec-Fetch-Site: none',
      'Sec-Fetch-User: ?1',
      'Cache-Control: max-age=0',
    ),
  ));
  $body = curl_exec($ch);
  $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
  $err  = curl_error($ch);
  curl_close($ch);
  return array('code' => $code, 'body' => (string)$body, 'err' => $err);
}

// ---- the Hot Story link, from the homepage --------------------------------
// The block is marked by the literal "Hot Story" caption; the first /news/
// link after it is the featured story. Deliberately anchored on that caption
// rather than a CSS class, because the classes change more often than the
// wording does.
function hot_story_link($html) {
  $i = stripos($html, 'Hot Story');
  if ($i === false) return null;
  $seg = substr($html, $i, 30000);
  if (!preg_match('~<a href="(/news/(\d+)[^"]*)"~', $seg, $m)) return null;
  return array('url' => 'https://www.forexfactory.com' . $m[1], 'id' => $m[2]);
}

// ---- headline + excerpt, from the story page ------------------------------
// og:title and og:description are stable meta tags - no markup to parse.
function story_meta($html) {
  $r = array('headline' => '', 'excerpt' => '', 'source' => '');
  if (preg_match('~property="og:title"\s+content="([^"]*)"~i', $html, $m))
    $r['headline'] = html_entity_decode($m[1], ENT_QUOTES, 'UTF-8');
  if (preg_match('~property="og:description"\s+content="([^"]*)"~i', $html, $m))
    $r['excerpt'] = html_entity_decode($m[1], ENT_QUOTES, 'UTF-8');
  if ($r['headline'] === '' && preg_match('~<title>(.*?)</title>~is', $html, $m))
    $r['headline'] = trim(preg_replace('~\s*\|\s*Forex Factory\s*$~i', '',
                     html_entity_decode($m[1], ENT_QUOTES, 'UTF-8')));
  if (preg_match('~From\s+([a-z0-9@.\-]+\.[a-z]{2,}|@[A-Za-z0-9_]+)~', $html, $m))
    $r['source'] = $m[1];
  return $r;
}

function cfg_load($file) {
  if (!is_readable($file)) return null;
  $c = @include $file;
  if (!is_array($c) || empty($c['key'])) return null;
  return array(
    'provider' => isset($c['provider']) ? strtolower(trim($c['provider'])) : 'anthropic',
    'key'      => trim($c['key']),
    'model'    => isset($c['model']) ? trim($c['model']) : 'claude-sonnet-5',
  );
}

// ---- ask the model --------------------------------------------------------
function classify($cfg, $headline, $excerpt, $instruments, $timeout) {
  $list = implode(', ', $instruments);

  $SYSTEM =
    "You read one market news story and report which instrument it concerns and which way it leans. "
  . "Reply with JSON only, no prose, no code fences, exactly these keys: "
  . '{"instrument":"","direction":"","confidence":0,"reason":""}' . " "
  . "instrument must be one of: " . $list . " - or \"none\" if the story is not clearly about one of them. "
  . "direction must be \"buy\", \"sell\" or \"none\". "
  . "confidence is 1 to 5.\n\n"
  . "RULES THAT MATTER MORE THAN GIVING AN ANSWER:\n"
  . "1. Return direction \"none\" whenever the story does not actually take a side. Headlines that are "
  . "questions, previews, week-aheads, or two-sided analysis are NOT directional. Most stories are not "
  . "directional - saying so is the correct answer, not a failure.\n"
  . "2. Judge from the body text, not the headline. A headline can sound bearish while the body argues "
  . "the level held.\n"
  . "3. Direction is about the named instrument as written. \"Dollar strength\" means sell XAUUSD and "
  . "sell EURUSD, but buy USDJPY - be careful which side of the pair the dollar is on.\n"
  . "4. Never invent a level, a number or a forecast that is not in the text.\n"
  . "5. reason is ONE sentence, under 110 characters, plain English, no jargon.";

  $USER = "HEADLINE: " . $headline . "\n\nBODY: " . $excerpt;

  if ($cfg['provider'] === 'anthropic') {
    $url  = 'https://api.anthropic.com/v1/messages';
    $hdrs = array('content-type: application/json', 'x-api-key: ' . $cfg['key'],
                  'anthropic-version: 2023-06-01');
    $body = array('model' => $cfg['model'], 'max_tokens' => 400, 'system' => $SYSTEM,
      'messages' => array(array('role' => 'user', 'content' => $USER)));
  } else {
    $url  = 'https://api.openai.com/v1/chat/completions';
    $hdrs = array('Content-Type: application/json', 'Authorization: Bearer ' . $cfg['key']);
    $body = array('model' => $cfg['model'], 'max_tokens' => 400, 'temperature' => 0.1,
      'messages' => array(
        array('role' => 'system', 'content' => $SYSTEM),
        array('role' => 'user',   'content' => $USER)));
  }

  $ch = curl_init($url);
  curl_setopt_array($ch, array(
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_POST           => true,
    CURLOPT_HTTPHEADER     => $hdrs,
    CURLOPT_POSTFIELDS     => json_encode($body),
    CURLOPT_TIMEOUT        => $timeout,
  ));
  $raw  = curl_exec($ch);
  $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
  $err  = curl_error($ch);
  curl_close($ch);

  if ($raw === false || $code < 200 || $code > 299)
    return array('err' => 'model HTTP ' . $code . ' ' . $err);

  $j = json_decode($raw, true);
  $text = '';
  if (isset($j['content'][0]['text']))                 $text = $j['content'][0]['text'];
  elseif (isset($j['choices'][0]['message']['content'])) $text = $j['choices'][0]['message']['content'];
  if ($text === '') return array('err' => 'empty model reply');

  if (preg_match('~\{.*\}~s', $text, $m)) $text = $m[0];
  $r = json_decode($text, true);
  if (!is_array($r)) return array('err' => 'model did not return JSON');
  return $r;
}

// ---- sanitise whatever came back ------------------------------------------
function clean_result($r, $instruments) {
  $inst = isset($r['instrument']) ? strtoupper(trim($r['instrument'])) : 'NONE';
  if (!in_array($inst, $instruments)) $inst = 'NONE';

  $dir = isset($r['direction']) ? strtolower(trim($r['direction'])) : 'none';
  if (!in_array($dir, array('buy', 'sell', 'none'))) $dir = 'none';
  if ($inst === 'NONE') $dir = 'none';            // no instrument, no direction

  $conf = isset($r['confidence']) ? (int)$r['confidence'] : 0;
  if ($conf < 0) $conf = 0;
  if ($conf > 5) $conf = 5;
  if ($dir === 'none') $conf = 0;

  // one line, no control characters, and short enough for a tooltip
  $reason = isset($r['reason']) ? (string)$r['reason'] : '';
  $reason = trim(preg_replace('~\s+~u', ' ', $reason));
  if (function_exists('mb_substr')) $reason = mb_substr($reason, 0, 160, 'UTF-8');
  else                              $reason = substr($reason, 0, 160);

  return array('instrument' => $inst, 'direction' => $dir,
               'confidence' => $conf, 'reason' => $reason);
}

// The EA reads this. Flat key=value is far easier to parse in MQL5 than JSON,
// and every value is stripped of newlines and '=' so a line can never split
// wrongly. ASCII only - the panel font has no glyphs for smart quotes.
function to_txt($d) {
  $lines = array();
  foreach ($d as $k => $v) {
    $v = (string)$v;
    $v = preg_replace('~[\r\n=]+~', ' ', $v);
    $v = preg_replace('~[^\x20-\x7E]~', '', $v);   // drop non-ASCII
    $lines[] = $k . '=' . trim($v);
  }
  return implode(chr(10), $lines) . chr(10);
}

function write_out($json_file, $txt_file, $data) {
  @file_put_contents($json_file, json_encode($data));
  @file_put_contents($txt_file,  to_txt($data));
}

// =====================================================================
//  main
// =====================================================================
$cfg = cfg_load($CFG_FILE);

if (isset($_GET['selftest'])) {
  $home = fetch('https://www.forexfactory.com/', $UA, $TIMEOUT);
  $link = $home['code'] === 200 ? hot_story_link($home['body']) : null;
  out(true, 'selftest', array(
    'build'        => $BUILD,
    'config_file'  => $CFG_FILE,
    'config_found' => ($cfg !== null),
    'provider'     => $cfg ? $cfg['provider'] : null,
    'model'        => $cfg ? $cfg['model'] : null,
    'ff_http'      => $home['code'],
    'ff_bytes'     => strlen($home['body']),
    'ff_err'       => $home['err'],
    'ff_snippet'   => $home['code'] === 200 ? '' :
                      substr(preg_replace('~\s+~', ' ', strip_tags($home['body'])), 0, 300),
    'hot_story'    => $link,
    'state_exists' => is_readable($STATE_FILE),
    'out_exists'   => is_readable($OUT_TXT),
  ));
}

$state = array('id' => '', 'checked' => 0);
if (is_readable($STATE_FILE)) {
  $s = json_decode((string)@file_get_contents($STATE_FILE), true);
  if (is_array($s)) $state = array_merge($state, $s);
}

$force = isset($_GET['force']);
$age   = time() - (int)$state['checked'];
if (!$force && $age < $MIN_INTERVAL)
  out(true, 'skipped - checked ' . $age . 's ago', array('next_in' => $MIN_INTERVAL - $age));

$home = fetch('https://www.forexfactory.com/', $UA, $TIMEOUT);
if ($home['code'] !== 200)
  out(false, 'forexfactory homepage returned HTTP ' . $home['code']);

$link = hot_story_link($home['body']);
if (!$link) out(false, 'could not find the Hot Story block on the homepage');

// Record the check even when nothing changed, so a quiet period does not
// re-fetch every single call.
$state['checked'] = time();
@file_put_contents($STATE_FILE, json_encode($state));

if (!$force && $link['id'] === $state['id'] && is_readable($OUT_TXT))
  out(true, 'unchanged', array('id' => $link['id']));

$page = fetch($link['url'], $UA, $TIMEOUT);
if ($page['code'] !== 200) out(false, 'story page returned HTTP ' . $page['code']);

$meta = story_meta($page['body']);
if ($meta['headline'] === '') out(false, 'could not read the headline from the story page');

if ($cfg === null)
  out(false, 'no usable .ai-config.php found at ' . $CFG_FILE);

$res = classify($cfg, $meta['headline'], $meta['excerpt'], $INSTRUMENTS, 60);
if (isset($res['err'])) {
  logline($LOG_FILE, 'classify failed: ' . $res['err']);
  out(false, 'classify failed: ' . $res['err']);
}
$res = clean_result($res, $INSTRUMENTS);

$data = array(
  'ok'         => 1,
  'id'         => $link['id'],
  'updated'    => gmdate('c'),
  'headline'   => $meta['headline'],
  'source'     => $meta['source'],
  'url'        => $link['url'],
  'instrument' => $res['instrument'],
  'direction'  => $res['direction'],
  'confidence' => $res['confidence'],
  'reason'     => $res['reason'],
);
write_out($OUT_JSON, $OUT_TXT, $data);

$state['id'] = $link['id'];
@file_put_contents($STATE_FILE, json_encode($state));
logline($LOG_FILE, 'id=' . $link['id'] . ' ' . $res['instrument'] . ' ' . $res['direction']);

out(true, 'updated', $data);
