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

$BUILD = 'v15';

$CFG_CANDIDATES = array(
  dirname(__DIR__) . '/.ai-config.php',   // preferred - survives deploys
  __DIR__ . '/.ai-config.php',
);
$CFG_FILE = $CFG_CANDIDATES[1];
foreach ($CFG_CANDIDATES as $cand) { if (is_readable($cand)) { $CFG_FILE = $cand; break; } }

// The two OUTPUT files must sit in the web root - the EA fetches hotstory.txt
// by URL. The state file and the log must NOT: __DIR__ is public_html, which
// the Hostinger deploy replaces wholesale on every push, so keeping them there
// erased the "already classified" memory and the diagnostic log on each
// deploy. Same reasoning as .ai-config.php living one level up.
$PERSIST_DIR = (is_dir(dirname(__DIR__)) && is_writable(dirname(__DIR__)))
             ? dirname(__DIR__) : __DIR__;
$STATE_FILE = $PERSIST_DIR . '/.hotstory_state.json';
$LOG_FILE   = $PERSIST_DIR . '/.hotstory.log';
$OUT_JSON   = __DIR__ . '/hotstory.json';
$OUT_TXT    = __DIR__ . '/hotstory.txt';

$MAX_CLASSIFY_TRIES = 2;   // give up on one story after this many model failures
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

// Config: provider / key / model, plus an optional 'fallback' => array(...)
// with the same three keys, tried when the primary fails. Providers:
// 'gemini' (Google AI Studio key, free tier), 'anthropic', 'openai'.
function cfg_one($c, $defaultProvider) {
  $p = isset($c['provider']) ? strtolower(trim($c['provider'])) : $defaultProvider;
  $m = isset($c['model']) ? trim($c['model']) : '';
  if ($m === '') $m = ($p === 'gemini') ? 'gemini-3.8-flash' : (($p === 'openai') ? 'gpt-4o' : 'claude-sonnet-5');
  return array('provider' => $p, 'key' => trim($c['key']), 'model' => $m);
}
function cfg_load($file) {
  if (!is_readable($file)) return null;
  $c = @include $file;
  if (!is_array($c) || empty($c['key'])) return null;
  $cfg = cfg_one($c, 'anthropic');
  $cfg['fallback'] = (isset($c['fallback']) && is_array($c['fallback']) && !empty($c['fallback']['key']))
                   ? cfg_one($c['fallback'], 'anthropic') : null;
  return $cfg;
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

  // The story block on the ForexFactory page carries a longer excerpt than
  // og:description (several paragraphs, ending in "( full story )") and a link
  // to the source article. Both are taken; the article itself is fetched by
  // fetch_full_story() so the model reads the whole piece, not one sentence.
  $r['excerpt_long'] = '';
  $r['source_url']   = '';
  if (preg_match('~class="[^"]*news__story[^"]*"(.*?)\(\s*full story\s*\)~is', $html, $m)) {
    $t = html_entity_decode(strip_tags($m[1]), ENT_QUOTES, 'UTF-8');
    $t = trim(preg_replace('~\s+~u', ' ', $t));
    if (strlen($t) > strlen($r['excerpt'])) $r['excerpt_long'] = $t;
  }
  if (preg_match_all('~href="(https?://[^"]+)"~i', $html, $mm)) {
    foreach ($mm[1] as $u) {
      $h = strtolower((string)parse_url($u, PHP_URL_HOST));
      if ($h === '' || strpos($h, 'forexfactory') !== false || strpos($h, 'faireconomy') !== false) continue;
      if (preg_match('~facebook|twitter|x\.com|google|apple|microsoft|youtube|instagram|cloudflare|gstatic|jsdelivr|metaquotes|linkedin|telegram~', $h)) continue;
      if (preg_match('~\.(png|jpg|jpeg|gif|webp|css|js|ico|woff2?)(\?|$)~i', $u)) continue;
      $r['source_url'] = html_entity_decode($u, ENT_QUOTES, 'UTF-8');
      break;
    }
  }
  return $r;
}

// Fetch the source article and return its paragraph text (capped), or "".
// Many news sites answer a plain curl; the ones that do not simply fall back
// to the ForexFactory excerpt. Never more than one attempt, 10 s.
function fetch_full_story($url, $cap = 9000) {
  if ($url === '' || !preg_match('~^https?://~i', $url)) return '';
  $ch = curl_init($url);
  curl_setopt_array($ch, array(
    CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => true, CURLOPT_MAXREDIRS => 4,
    CURLOPT_TIMEOUT => 10, CURLOPT_CONNECTTIMEOUT => 5,
    CURLOPT_USERAGENT => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    CURLOPT_HTTPHEADER => array('Accept: text/html,application/xhtml+xml', 'Accept-Language: en-US,en;q=0.9'),
    CURLOPT_SSL_VERIFYPEER => true,
  ));
  $raw = curl_exec($ch);
  $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
  curl_close($ch);
  if ($raw === false || $code !== 200 || strlen($raw) < 500) return '';
  $raw = substr($raw, 0, 600000);
  $raw = preg_replace('~<(script|style|nav|header|footer|aside|form|svg)\b[^>]*>.*?</\1>~is', ' ', $raw);
  // paragraphs only: that is the article; menus and teasers are not in <p>
  if (!preg_match_all('~<p\b[^>]*>(.*?)</p>~is', $raw, $pm)) return '';
  $out = array();
  foreach ($pm[1] as $p) {
    $t = trim(preg_replace('~\s+~u', ' ', html_entity_decode(strip_tags($p), ENT_QUOTES, 'UTF-8')));
    if (strlen($t) < 40) continue;                                   // captions, buttons
    if (preg_match('~cookie|subscribe|sign up|newsletter|all rights reserved|privacy policy~i', $t)) continue;
    $out[] = $t;
    if (strlen(implode(' ', $out)) > $cap) break;
  }
  $txt = implode("\n", $out);
  return strlen($txt) >= 300 ? substr($txt, 0, $cap) : '';
}

// ---- ask the model --------------------------------------------------------
function classify($cfg, $headline, $excerpt, $instruments, $timeout) {
  $list = implode(', ', $instruments);

  $SYSTEM =
    "You are the macro desk analyst for a short-term gold and FX trading system. You receive ONE news "
  . "story (headline plus the full article when it could be fetched) and must decide what it does to the "
  . "instruments on this list over the next 4 to 24 hours: " . $list . ".\n\n"
  . "Work through these steps BEFORE answering, in order:\n"
  . "1. WHAT IS NEW. Separate new information from what was already expected or already priced. A story "
  . "that confirms what markets expected moves little; a surprise moves a lot.\n"
  . "2. CHANNEL. Name the transmission channel: interest-rate expectations and bond yields, US dollar "
  . "demand, risk appetite (risk-on / risk-off), safe-haven demand, inflation expectations, central-bank "
  . "guidance, geopolitics, energy and commodity prices, or a country-specific fiscal / political shock.\n"
  . "3. MAP TO THE LIST. Decide which listed instrument is most exposed through that channel and which "
  . "way it moves. Use this reference:\n"
  . "   - XAUUSD (gold) RISES on: rate-cut expectations, falling real yields, a weaker dollar, risk-off, "
  . "war or geopolitical escalation, sanctions, bank or credit stress, higher inflation worries, central-bank "
  . "buying. Gold FALLS on: hawkish central banks, rising yields, a stronger dollar, strong US data that "
  . "delays cuts, ceasefires and de-escalation, risk-on relief.\n"
  . "   - EURUSD, GBPUSD, AUDUSD, NZDUSD are quoted with the dollar second: dollar strength = SELL, dollar "
  . "weakness = BUY; strength in the first currency (hawkish ECB / BoE / RBA / RBNZ, better data) = BUY.\n"
  . "   - USDJPY, USDCHF, USDCAD are quoted with the dollar first: dollar strength = BUY. Risk-off and "
  . "safe-haven flows usually mean SELL USDJPY and SELL USDCHF. Oil up usually means SELL USDCAD.\n"
  . "   - A story about one currency against a THIRD currency (for example sterling versus the euro) still "
  . "says something about that currency: a stronger pound leans BUY GBPUSD, with lower confidence.\n"
  . "   - Iran, Middle East, Russia-Ukraine escalation: BUY XAUUSD. A credible ceasefire or peace deal: SELL "
  . "XAUUSD. A US president pressing for rate cuts or attacking the Fed: BUY XAUUSD, SELL the dollar.\n"
  . "4. STRENGTH. confidence 5 = a clear surprise with an obvious channel; 4 = clear channel, modest "
  . "surprise; 3 = a real lean that a desk would act on with small size; 2 = a weak lean; 1 = barely a "
  . "lean. Use 3 or more only when you would actually put the trade on.\n"
  . "5. NONE is for stories with NO plausible channel to any listed instrument (celebrity, sport, local "
  . "crime, product launches), for pure previews with no new information, and for genuinely balanced "
  . "two-sided pieces. Do NOT return none merely because the story is not about a currency pair by "
  . "name: most macro, political and geopolitical news has a channel. Find it.\n\n"
  . "Reply with JSON only, no prose outside it, no code fences, exactly these keys:\n"
  . '{"instrument":"","direction":"","confidence":0,"reason":"","analysis":""}' . "\n"
  . "instrument: one of " . $list . " or \"none\". direction: \"buy\", \"sell\" or \"none\". "
  . "confidence: 1 to 5 (0 with none). reason: ONE sentence under 110 characters, plain English, the "
  . "channel and the direction. analysis: 3 to 6 sentences covering steps 1 to 4, under 700 characters. "
  . "Never invent a level, number or forecast that is not in the text.";

  $USER = "HEADLINE: " . $headline . "\n\nARTICLE:\n" . $excerpt;

  // Primary first, then the fallback. A fallback answer is marked so the log
  // says which service actually read the story.
  $r = classify_once($cfg, $SYSTEM, $USER, $timeout);
  if (isset($r['err']) && !empty($cfg['fallback'])) {
    $r2 = classify_once($cfg['fallback'], $SYSTEM, $USER, $timeout);
    if (!isset($r2['err'])) { $r2['via'] = $cfg['fallback']['provider']; return $r2; }
    $r['err'] .= ' | fallback ' . $cfg['fallback']['provider'] . ': ' . $r2['err'];
  }
  return $r;
}

function classify_once($cfg, $SYSTEM, $USER, $timeout) {
  if ($cfg['provider'] === 'gemini') {
    $url  = 'https://generativelanguage.googleapis.com/v1beta/models/' . rawurlencode($cfg['model']) . ':generateContent';
    $hdrs = array('Content-Type: application/json', 'x-goog-api-key: ' . $cfg['key']);
    $body = array(
      'system_instruction' => array('parts' => array(array('text' => $SYSTEM))),
      'contents' => array(array('role' => 'user', 'parts' => array(array('text' => $USER)))),
      'generationConfig' => array('temperature' => 0.2, 'maxOutputTokens' => 4000,
                                  'responseMimeType' => 'application/json'));
  } elseif ($cfg['provider'] === 'anthropic') {
    $url  = 'https://api.anthropic.com/v1/messages';
    $hdrs = array('content-type: application/json', 'x-api-key: ' . $cfg['key'],
                  'anthropic-version: 2023-06-01');
    $body = array('model' => $cfg['model'], 'max_tokens' => 2500, 'system' => $SYSTEM,
      'messages' => array(array('role' => 'user', 'content' => $USER)));
  } else {
    $url  = 'https://api.openai.com/v1/chat/completions';
    $hdrs = array('Content-Type: application/json', 'Authorization: Bearer ' . $cfg['key']);
    $body = array('model' => $cfg['model'], 'max_tokens' => 2500, 'temperature' => 0.2,
      'messages' => array(
        array('role' => 'system', 'content' => $SYSTEM),
        array('role' => 'user',   'content' => $USER)));
  }

  // Up to three attempts: 429 / 5xx are capacity or rate messages ("high
  // demand"), not a bad request, so wait and try again before giving up.
  $raw = false; $code = 0; $err = '';
  for ($try = 1; $try <= 3; $try++) {
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
    if ($raw !== false && $code >= 200 && $code <= 299) break;
    if ($raw !== false && $code !== 429 && $code < 500) break;    // a real error: no point retrying
    if ($try < 3) sleep(3 * $try);
  }

  if ($raw === false || $code < 200 || $code > 299)
    return array('err' => 'model HTTP ' . $code . ' ' . $err, 'raw' => (string)$raw,
                 'transient' => ($raw === false || $code === 429 || $code >= 500));

  $j = json_decode($raw, true);
  $text = '';
  // Anthropic: the reply is a LIST of content blocks and the text is not
  // necessarily the first one - a thinking block can precede it. Reading only
  // content[0] reported "empty model reply" for perfectly good answers (the
  // same story classified fine an hour earlier, then "empty" on re-read).
  if (isset($j['content']) && is_array($j['content'])) {
    foreach ($j['content'] as $blk) {
      if (isset($blk['type']) && $blk['type'] === 'text' && isset($blk['text']) && $blk['text'] !== '')
      { $text .= $blk['text']; }
    }
  }
  if ($text === '' && isset($j['choices'][0]['message']['content']))
    $text = (string)$j['choices'][0]['message']['content'];
  // Gemini: candidates[0].content.parts[].text
  if ($text === '' && isset($j['candidates'][0]['content']['parts']) && is_array($j['candidates'][0]['content']['parts'])) {
    foreach ($j['candidates'][0]['content']['parts'] as $pt) {
      if (!empty($pt['thought'])) continue;                 // a thinking part is not the answer
      if (isset($pt['text'])) $text .= (string)$pt['text'];
    }
  }
  if ($text === '') return array('err' => 'empty model reply', 'raw' => (string)$raw);

  if (preg_match('~\{.*\}~s', $text, $m)) $text = $m[0];
  $r = json_decode($text, true);
  if (!is_array($r)) return array('err' => 'model did not return JSON', 'raw' => $text);
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
  $analysis = isset($r['analysis']) ? (string)$r['analysis'] : '';
  $analysis = trim(preg_replace('~\s+~u', ' ', $analysis));
  $analysis = function_exists('mb_substr') ? mb_substr($analysis, 0, 900, 'UTF-8') : substr($analysis, 0, 900);
  return array('instrument' => $inst, 'direction' => $dir,
               'confidence' => $conf, 'reason' => $reason, 'analysis' => $analysis);
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

// Put the last good read back as the output files, if it is for this story.
function restore_last($lastFile, $id, $outJson, $outTxt) {
  if (!is_readable($lastFile)) return false;
  $d = json_decode((string)@file_get_contents($lastFile), true);
  if (!is_array($d) || !isset($d['id']) || (string)$d['id'] !== (string)$id) return false;
  @file_put_contents($outJson, json_encode($d));
  @file_put_contents($outTxt,  to_txt($d));
  return is_readable($outTxt);
}

function state_read($f) {
  $d = array('id' => '', 'updated' => 0, 'fail_id' => '', 'fail_n' => 0);
  $s = is_readable($f) ? json_decode((string)@file_get_contents($f), true) : null;
  return is_array($s) ? array_merge($d, $s) : $d;
}

function state_write($f, $s) { @file_put_contents($f, json_encode($s)); }

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
    'persist_dir'   => $PERSIST_DIR,
    'persist_ok'    => (strpos($PERSIST_DIR, __DIR__) !== 0),
    'provider'      => $cfg ? $cfg['provider'] : null,
    'model'         => $cfg ? $cfg['model'] : null,
    'fallback'      => ($cfg && !empty($cfg['fallback'])) ? $cfg['fallback']['provider'] . ' / ' . $cfg['fallback']['model'] : '(none)',
    'token_sha'     => substr($TOKEN_SHA, 0, 12) . '...',
    'known_id'      => $state['id'],
    'log_tail'      => (function($f) { if (!is_readable($f)) return array();
                          $l = @file($f, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
                          return is_array($l) ? array_slice($l, -4) : array(); })($LOG_FILE),
    'failed_id'     => $state['fail_id'],
    'failed_tries'  => (int)$state['fail_n'],
    'max_tries'     => $MAX_CLASSIFY_TRIES,
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

  $REREAD = $PERSIST_DIR . '/.hotstory_reread';
  if (is_readable($REREAD)) {                      // owner pressed "re-read now" on the settings panel
    @unlink($REREAD);
    logline($LOG_FILE, 're-read requested from the settings panel for id=' . $link['id']);
    out(true, 'need_story', array('id' => $link['id'], 'need_story' => $link['url'], 'note' => 're-read requested'));
  }
  if ($link['id'] === $state['id'] && is_readable($OUT_TXT))
    out(true, 'unchanged', array('id' => $link['id']));
  if ($link['id'] === $state['id'] && restore_last($PERSIST_DIR . '/.hotstory_last.json', $link['id'], $OUT_JSON, $OUT_TXT)) {
    logline($LOG_FILE, 'restored the last good read for id=' . $link['id'] . ' after the output file went missing');
    out(true, 'unchanged', array('id' => $link['id'], 'note' => 'output restored from the last good read'));
  }

  // A story the model has already failed on MAX_CLASSIFY_TRIES times is never
  // asked for again. Without this the client re-posts the same page every 20
  // minutes and the model is re-billed for the same failure until
  // ForexFactory happens to promote something else. Measured on 2026-09-28:
  // 34 consecutive failures, 11h40m, panel frozen the whole time.
  // ... but only while an output file exists. A deploy wipes hotstory.txt, and
  // a given-up story must never leave the EA reading a 404 (2026-10-11: the
  // model key had no credit, every story failed, the file stayed missing).
  if ($link['id'] !== '' && $link['id'] === $state['fail_id']
      && (int)$state['fail_n'] >= $MAX_CLASSIFY_TRIES && is_readable($OUT_TXT))
    out(true, 'unchanged', array('id' => $link['id'],
        'note' => 'gave up on this story after ' . (int)$state['fail_n'] . ' model failures'));

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

$article = fetch_full_story($meta['source_url']);
$textUsed = ($article !== '') ? 'full article (' . strlen($article) . ' chars from ' . parse_url($meta['source_url'], PHP_URL_HOST) . ')'
          : (($meta['excerpt_long'] !== '') ? 'ForexFactory excerpt (' . strlen($meta['excerpt_long']) . ' chars)' : 'og:description only');
$bodyText = ($article !== '') ? $article : (($meta['excerpt_long'] !== '') ? $meta['excerpt_long'] : $meta['excerpt']);
if ($article !== '' && $meta['excerpt_long'] !== '' && stripos($article, substr($meta['excerpt_long'], 0, 60)) === false)
  $bodyText = $meta['excerpt_long'] . "\n\n" . $article;       // keep the FF summary in front of the source piece
$res = classify($cfg, $meta['headline'], $bodyText, $INSTRUMENTS, 75);
if (isset($res['err'])) {
  // A transient failure (busy, rate limit, outage) is not a strike against
  // the story: the next 5-minute run simply tries again.
  $transient = !empty($res['transient']);
  $n = $transient ? 0 : (($state['fail_id'] === $id) ? ((int)$state['fail_n'] + 1) : 1);
  state_write($STATE_FILE, array('id' => $state['id'], 'updated' => (int)$state['updated'],
                                 'fail_id' => ($transient ? '' : $id), 'fail_n' => $n));
  $rawNote = '';
  if (isset($res['raw']) && $res['raw'] !== '')
    $rawNote = ' | raw: ' . substr(preg_replace('~\s+~', ' ', (string)$res['raw']), 0, 400);
  logline($LOG_FILE, 'classify failed (' . ($transient ? 'transient, not counted' : $n . '/' . $MAX_CLASSIFY_TRIES) . ') id=' . $id
                   . ': ' . $res['err'] . $rawNote);
  // Fallback output: the headline with no side, so the panel shows the story
  // and the file never goes missing. Written when giving up on the story and
  // whenever there is no output file at all (after a deploy).
  if (($n >= $MAX_CLASSIFY_TRIES || !is_readable($OUT_TXT))
      && !restore_last($PERSIST_DIR . '/.hotstory_last.json', $id, $OUT_JSON, $OUT_TXT)) {
    $fb = array(
      'ok'         => 1,
      'id'         => $id,
      'updated'    => gmdate('c'),
      'headline'   => $meta['headline'],
      'source'     => $meta['source'],
      'url'        => $url,
      'instrument' => 'NONE',
      'direction'  => 'none',
      'confidence' => 0,
      'reason'     => 'AI read unavailable (' . substr(preg_replace('~\s+~', ' ', (string)$res['err']), 0, 60) . ') - headline only',
    );
    @file_put_contents($OUT_JSON, json_encode($fb));
    @file_put_contents($OUT_TXT,  to_txt($fb));
    logline($LOG_FILE, 'fallback written for id=' . $id . ' (headline only)');
  }
  out(false, 'classify failed: ' . $res['err']
           . ($n >= $MAX_CLASSIFY_TRIES ? ' - giving up on this story' : ''));
}
$via = isset($res['via']) ? $res['via'] : null;
$res = clean_result($res, $INSTRUMENTS);
if ($via !== null) $res['via'] = $via;

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
  'analysis'   => $res['analysis'],
  'text_used'  => $textUsed,
);
@file_put_contents($OUT_JSON, json_encode($data));
@file_put_contents($OUT_TXT,  to_txt($data));
@file_put_contents($PERSIST_DIR . '/.hotstory_last.json', json_encode($data));   // survives a deploy
state_write($STATE_FILE, array('id' => $id, 'updated' => time(),
                               'fail_id' => '', 'fail_n' => 0));
logline($LOG_FILE, 'id=' . $id . ' ' . $res['instrument'] . ' ' . $res['direction'] . ' conf ' . $res['confidence']
                 . ' | ' . $textUsed . (isset($res['via']) ? ' (via fallback ' . $res['via'] . ')' : '')
                 . ' | ' . substr($res['analysis'], 0, 160));

out(true, 'updated', $data);
