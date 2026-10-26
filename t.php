<?php
// =====================================================================
//  Gold Scalpers - first-party visit counter (endpoint for /assets/t.js)
//  -------------------------------------------------------------------
//  One JSON line per event, appended to a monthly file ABOVE public_html
//  (survives deploys, never served). No cookies and no IP addresses are
//  stored: a visitor is a daily-salted hash of IP + browser, so the same
//  person counts once per day and cannot be traced back.
//  Read it in the panel: /panel/stats.php.
// =====================================================================
header('Content-Type: text/plain');
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex');
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') { http_response_code(405); exit; }

$raw = (string)file_get_contents('php://input', false, null, 0, 2048);
$in  = json_decode($raw, true);
if (!is_array($in)) { http_response_code(400); exit; }

$EVENTS = array('pv', 'leave', 'buy_gumroad', 'buy_paypal', 'buy_crypto', 'crypto_copy_address',
                'mql5_pro', 'mql5_free', 'download_zip', 'go_checkout', 'email');
$e = (string)($in['e'] ?? '');
if (!in_array($e, $EVENTS, true)) { http_response_code(400); exit; }

$ua = (string)($_SERVER['HTTP_USER_AGENT'] ?? '');
$bot = ($ua === '' || preg_match('/bot|crawl|spider|slurp|headless|lighthouse|preview|python|curl|wget|scrapy|httpclient|phantom|selenium|puppeteer|playwright/i', $ua)) ? 1 : 0;

function stats_dir() {
  $d = dirname(__DIR__) . '/.gs-stats';
  if (is_dir($d) || @mkdir($d, 0750, true)) return $d;
  $d = __DIR__ . '/licdata';                     // fallback: files end in .log, denied by .htaccess
  @mkdir($d, 0755, true);
  return $d;
}
function client_ip() {
  foreach (array('HTTP_CF_CONNECTING_IP', 'HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR') as $k) {
    if (!empty($_SERVER[$k])) { $ip = trim(explode(',', $_SERVER[$k])[0]); if (filter_var($ip, FILTER_VALIDATE_IP)) return $ip; }
  }
  return '0.0.0.0';
}
$dir = stats_dir();
$saltFile = $dir . '/salt.log';
$salt = is_file($saltFile) ? trim((string)file_get_contents($saltFile)) : '';
if ($salt === '') { $salt = bin2hex(random_bytes(16)); @file_put_contents($saltFile, $salt, LOCK_EX); }

$clip = function ($v, $n) { return mb_substr(preg_replace('/[\x00-\x1F]/', '', (string)$v), 0, $n); };
$row = array(
  't'   => time(),
  'e'   => $e,
  'p'   => $clip($in['p'] ?? '', 160),
  'sid' => preg_replace('/[^a-z0-9]/', '', $clip($in['sid'] ?? '', 16)),
  'v'   => substr(hash('sha256', date('Y-m-d') . '|' . client_ip() . '|' . $ua . '|' . $salt), 0, 16),
  'm'   => preg_match('/Mobi|Android|iPhone|iPad/i', $ua) ? 1 : 0,
);
if ($bot) $row['bot'] = 1;
if ($e === 'pv') {
  $row['r']  = $clip($in['r'] ?? '', 300);
  $row['us'] = $clip($in['us'] ?? '', 60);
  $row['uc'] = $clip($in['uc'] ?? '', 60);
  $row['w']  = (int)($in['w'] ?? 0);
  if (!empty($_SERVER['HTTP_CF_IPCOUNTRY'])) $row['cc'] = substr(preg_replace('/[^A-Z]/', '', strtoupper($_SERVER['HTTP_CF_IPCOUNTRY'])), 0, 2);
} elseif ($e === 'leave') {
  $row['ms'] = max(0, min(3600000, (int)($in['ms'] ?? 0)));
  $row['sd'] = max(0, min(100, (int)($in['sd'] ?? 0)));
} else {
  $row['h'] = $clip($in['h'] ?? '', 160);
}
@file_put_contents($dir . '/stats-' . date('Y-m') . '.log', json_encode($row, JSON_UNESCAPED_SLASHES) . "\n", FILE_APPEND | LOCK_EX);
http_response_code(204);
