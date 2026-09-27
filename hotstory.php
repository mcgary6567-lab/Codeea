<?php
// =====================================================================
//  Gold Scalpers - ForexFactory "Hot Story" reader
//  ---------------------------------------------------------------
//  Takes the hottest story of the last 12 hours, asks the model which
//  instrument it concerns and which way it leans, and writes two files:
//
//      hotstory.txt    key=value, one per line  <- what the EA parses
//      hotstory.json   the same data as JSON    <- site / debugging
//
//  WHY THIS TAKES A POSTED PAGE INSTEAD OF FETCHING ONE
//  forexfactory.com answers this host with a Cloudflare JavaScript
//  challenge ("Just a moment..."), so the server cannot read it at all.
//  A machine on an ordinary connection can. So the PC that already runs
//  MT5 fetches the page and posts it here, and this file does the rest:
//  all the parsing and judgement lives server-side, where it can be
//  fixed without touching anything on that machine.
//
//  PROTOCOL - two phases, so the story page is only fetched when new:
//    1. client POSTs  token + home=<homepage html>
//       -> {"need_story":"<url>"}   a new story, fetch it and post again
//       -> {"message":"unchanged"}  nothing to do, no model call
//    2. client POSTs  token + story=<story page html> + url=<that url>
//       -> {"message":"updated", ...}
//
//  AUTH - nothing to configure on the server. The agent holds a 192-bit
//  token; only its SHA-256 lives here. Publishing the hash of a 192-bit
//  random secret gives an attacker nothing (preimage resistance), so the
//  shared secret never has to be pasted into a file on the host, and the
//  endpoint works the moment it deploys. To rotate: pick a new token,
//  put its sha256 below, and update line 29 of hotstory-agent.ps1.
//
//  Diagnose any time with:  /hotstory.php?selftest=1
// =====================================================================

header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');
header('Cache-Control: no-store');

@ini_set('display_errors', '0');
@ini_set('log_errors', '1');
@set_time_limit(120);

$BUILD = 'v5';

$CFG_CANDIDATES = array(
  dirname(__DIR__) . '/.ai-config.php',   // preferred - survives deploys
  __DIR__ . '/.ai-config.php',
);
$CFG_FILE = $CFG_CANDIDATES[1];
foreach ($CFG_CANDIDATES as $cand) { if (is_readable($cand)) { $CFG_FILE = $cand; break; } }

$STATE_FILE = __DIR__ . '/.hotstory_state.json';
$OUT_JSON   = __DIR__ . '/hotstory.json';
$OUT_TXT    = __DIR__ . '/hotstory.txt';
$LOG_FILE   = __DIR__ . '/.hotstory.log';

$MAX_POST   = 3 * 1024 * 1024;   // a FF page is ~250 KB; this is generous
$TOKEN_SHA  = '624c8bcd45276ba8b25916b2caf78f6f04e9b6cd14cfec5c8ae4e8d8b9a97501';
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

// ---- the Hot Story link, from the homepage --------------------------------
// Anchored on the literal "Hot Story" caption rather than a CSS class, because
// the wording changes far less often than the classes do.
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
  if (isset($j['content'][0]['text']))                   $text = $j['content'][0]['text'];
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

  $reason = isset($r['reason']) ? (string)$r['reason'] : '';
  $reason = trim(preg_replace('~\s+~u', ' ', $reason));
  $reason = function_exists('mb_substr') ? mb_substr($reason, 0, 160, 'UTF-8')
                                         : substr($reason, 0, 160);
  return array('instrument' => $inst, 'direction' => $dir,
               'confidence' => $conf, 'reason' => $reason);
}

// The EA reads this. Flat key=value parses far more safely in MQL5 than JSON,
// and every value is stripped of newlines, '=' and non-ASCII so a line cannot
// split wrongly and the panel font has a glyph for every character.
function to_txt($d) {
  $lines = array();
  foreach ($d as $k => $v) {
    $v = preg_replace('~[\r\n=]+~', ' ', (string)$v);
    $v = preg_replace('~[^\x20-\x7E]~', '', $v);
    $lines[] = $k . '=' . trim($v);
  }
  return implode(chr(10), $lines) . chr(10);
}

function state_read($f) {
  $s = is_readable($f) ? json_decode((string)@file_get_contents($f), true) : null;
  return is_array($s) ? array_merge(array('id' => '', 'updated' => 0), $s)
                      : array('id' => '', 'updated' => 0);
}

// =====================================================================
//  main
// =====================================================================
$cfg   = cfg_load($CFG_FILE);
$state = state_read($STATE_FILE);

if (isset($_GET['selftest'])) {
  out(true, 'selftest', array(
    'build'         => $BUILD,
    'config_file'   => $CFG_FILE,
    'config_found'  => ($cfg !== null),
    'provider'      => $cfg ? $cfg['provider'] : null,
    'model'         => $cfg ? $cfg['model'] : null,
    'token_sha'     => substr($TOKEN_SHA, 0, 12) . '...',
    'known_id'      => $state['id'],
    'last_updated'  => $state['updated'] ? gmdate('c', $state['updated']) : null,
    'out_exists'    => is_readable($OUT_TXT),
  ));
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST')
  out(false, 'POST the page here, or use ?selftest=1. This endpoint no longer fetches: '
           . 'forexfactory.com answers this host with a Cloudflare challenge.');

if ($cfg === null) out(false, 'no usable .ai-config.php at ' . $CFG_FILE);

// Compare hashes, so the secret itself is never stored on the server.
$tok = isset($_POST['token']) ? (string)$_POST['token'] : '';
if (!hash_equals($TOKEN_SHA, hash('sha256', $tok))) { http_response_code(403); out(false, 'bad token'); }

// ---- phase 1: the homepage, to find out whether anything changed ----------
if (isset($_POST['home'])) {
  $home = (string)$_POST['home'];
  if (strlen($home) > $MAX_POST) out(false, 'homepage payload too large');

  $link = hot_story_link($home);
  if (!$link) out(false, 'could not find the Hot Story block in the posted homepage');

  if ($link['id'] === $state['id'] && is_readable($OUT_TXT))
    out(true, 'unchanged', array('id' => $link['id']));

  out(true, 'need_story', array('id' => $link['id'], 'need_story' => $link['url']));
}

// ---- phase 2: the story page ----------------------------------------------
if (!isset($_POST['story'])) out(false, 'post home= or story=');

$story = (string)$_POST['story'];
if (strlen($story) > $MAX_POST) out(false, 'story payload too large');

$url = isset($_POST['url']) ? trim((string)$_POST['url']) : '';
if (!preg_match('~^https://www\.forexfactory\.com/news/(\d+)~', $url, $um))
  out(false, 'url must be a forexfactory.com/news/<id> address');
$id = $um[1];

$meta = story_meta($story);
if ($meta['headline'] === '') out(false, 'could not read a headline from the posted story page');

$res = classify($cfg, $meta['headline'], $meta['excerpt'], $INSTRUMENTS, 60);
if (isset($res['err'])) {
  logline($LOG_FILE, 'classify failed: ' . $res['err']);
  out(false, 'classify failed: ' . $res['err']);
}
$res = clean_result($res, $INSTRUMENTS);

$data = array(
  'ok'         => 1,
  'id'         => $id,
  'updated'    => gmdate('c'),
  'headline'   => $meta['headline'],
  'source'     => $meta['source'],
  'url'        => $url,
  'instrument' => $res['instrument'],
  'direction'  => $res['direction'],
  'confidence' => $res['confidence'],
  'reason'     => $res['reason'],
);
@file_put_contents($OUT_JSON, json_encode($data));
@file_put_contents($OUT_TXT,  to_txt($data));
@file_put_contents($STATE_FILE, json_encode(array('id' => $id, 'updated' => time())));
logline($LOG_FILE, 'id=' . $id . ' ' . $res['instrument'] . ' ' . $res['direction']);

out(true, 'updated', $data);
