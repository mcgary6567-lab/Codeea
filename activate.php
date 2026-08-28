<?php
// =====================================================================
//  Gold Scalpers - self-serve activation endpoint  (real account only)
//  -------------------------------------------------------------------
//  The thank-you page form POSTs the customer's MT5 *real* account
//  number here. It is written into licenses.txt (the file the EA reads)
//  so activation is automatic, AND full metadata (name / email / IP /
//  country / date) is written into .accounts.json so the /panel shows
//  exactly who every self-serve account belongs to.
//
//  Guardrails: digits-only validation, duplicate-skip, honeypot,
//  per-IP rate limit, private logs (dotfiles, denied by root .htaccess).
//  NOTE: this authorizes ANY submitted number (no payment check) - by
//  design per owner request. Review the panel / log and remove abuse.
// =====================================================================
header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');

$LICENSE_FILE = __DIR__ . '/licenses.txt';
$META_FILE    = __DIR__ . '/.accounts.json';      // dotfile -> denied by root .htaccess
$LOG_FILE     = __DIR__ . '/.activations.log';    // dotfile -> denied
$RATE_FILE    = __DIR__ . '/.activate_rate.json'; // dotfile -> denied
$RATE_MAX     = 8;        // max submissions per IP ...
$RATE_WINDOW  = 3600;     // ... per hour

function out($ok, $msg, $extra = array()) {
  echo json_encode(array_merge(array('success' => $ok, 'message' => $msg), $extra));
  exit;
}

// Real visitor IP, even behind Cloudflare / a reverse proxy. Falls back safely.
function client_ip() {
  foreach (array('HTTP_CF_CONNECTING_IP', 'HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR') as $k) {
    if (!empty($_SERVER[$k])) {
      $ip = trim(explode(',', $_SERVER[$k])[0]);
      if (filter_var($ip, FILTER_VALIDATE_IP)) return $ip;
    }
  }
  return '0.0.0.0';
}

// Best-effort 2-letter country code from the IP. Never blocks activation:
// uses Cloudflare's free header first, then a short-timeout lookup, else ''.
function geo_cc($ip) {
  if (!empty($_SERVER['HTTP_CF_IPCOUNTRY'])) {
    $cc = strtoupper(substr($_SERVER['HTTP_CF_IPCOUNTRY'], 0, 2));
    if (ctype_alpha($cc) && $cc !== 'XX' && $cc !== 'T1') return $cc;
  }
  if (!filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE)) return '';
  $ctx = stream_context_create(array('http' => array('timeout' => 1.2, 'ignore_errors' => true)));
  $r = @file_get_contents('http://ip-api.com/json/' . urlencode($ip) . '?fields=countryCode', false, $ctx);
  if ($r) {
    $j = json_decode($r, true);
    if (!empty($j['countryCode']) && ctype_alpha($j['countryCode'])) return strtoupper(substr($j['countryCode'], 0, 2));
  }
  return '';
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') out(false, 'Method not allowed.');

// Accept a JSON body (the form posts JSON) or normal form-encoded.
$raw = file_get_contents('php://input');
$in  = json_decode($raw, true);
if (!is_array($in)) $in = $_POST;

// Honeypot: a bot filled the hidden field -> look successful, do nothing.
if (trim((string)($in['website_url_extra'] ?? '')) !== '') out(true, 'Received.');

$name  = trim((string)($in['Name'] ?? $in['name'] ?? ''));
$email = trim((string)($in['Email'] ?? $in['email'] ?? ''));
$real  = preg_replace('/\D/', '', (string)($in['Real Account'] ?? $in['real'] ?? $in['account'] ?? ''));

if (!preg_match('/^\d{5,12}$/', $real)) out(false, 'Please enter a valid MT5 account number (5-12 digits).');
if ($email !== '' && !filter_var($email, FILTER_VALIDATE_EMAIL)) out(false, 'Please enter a valid email address.');

// ---- per-IP rate limit ----
$ip  = client_ip();
$now = time();
$rate = is_file($RATE_FILE) ? (json_decode((string)file_get_contents($RATE_FILE), true) ?: array()) : array();
$hits = array_values(array_filter($rate[$ip] ?? array(), function($t) use ($now, $RATE_WINDOW) { return ($now - $t) < $RATE_WINDOW; }));
if (count($hits) >= $RATE_MAX) out(false, 'Too many submissions from your connection. Please email support@goldscalpers.com.');
$hits[] = $now;
$rate[$ip] = $hits;
foreach ($rate as $k => $ts) {
  $rate[$k] = array_values(array_filter($ts, function($t) use ($now, $RATE_WINDOW) { return ($now - $t) < $RATE_WINDOW; }));
  if (!$rate[$k]) unset($rate[$k]);
}
@file_put_contents($RATE_FILE, json_encode($rate), LOCK_EX);

// ---- build a safe label for the public licence file ----
$labelName = trim(mb_substr(preg_replace('/[#\r\n]/', '', $name), 0, 40));
$label = 'web ' . date('Y-m-d') . ($labelName !== '' ? ' ' . $labelName : '');

// ---- append the account to licenses.txt (dedupe / re-enable) ----
$content = is_file($LICENSE_FILE) ? (string)file_get_contents($LICENSE_FILE) : '';
if (trim($content) === '') $content = "# Gold Scalpers EA - Licence File\n\n";
$alreadyActive = (bool)preg_match('/^\s*' . preg_quote($real, '/') . '\b/m', $content);
if (!$alreadyActive) {
  $content = preg_replace('/^\s*#\s*' . preg_quote($real, '/') . '\b.*$/m', '', $content); // drop disabled dup
  $content = rtrim($content, "\r\n") . "\n" . $real . '   # ' . $label . "\n";
  if (@file_put_contents($LICENSE_FILE, $content, LOCK_EX) === false) {
    out(false, 'Could not save right now. Please email support@goldscalpers.com.');
  }
}

// ---- metadata for the /panel (name / email / IP / country / date) ----
// Non-fatal: licence already saved above; never fail activation over this.
$cc   = geo_cc($ip);
$meta = is_file($META_FILE) ? (json_decode((string)file_get_contents($META_FILE), true) ?: array()) : array();
$meta[$real] = array(
  'name'   => mb_substr($name, 0, 60),
  'email'  => mb_substr($email, 0, 80),
  'ip'     => $ip,
  'cc'     => $cc,
  'added'  => ($meta[$real]['added'] ?? date('c')),
  'source' => 'web',
);
@file_put_contents($META_FILE, json_encode($meta, JSON_PRETTY_PRINT), LOCK_EX);

// ---- private log for the owner ----
@file_put_contents($LOG_FILE, sprintf(
  "%s\tip=%s\tcc=%s\tname=%s\temail=%s\treal=%s\tstatus=%s\n",
  date('c'), $ip, $cc, str_replace("\t", ' ', $name), str_replace("\t", ' ', $email), $real,
  $alreadyActive ? 'already-active' : 'added'
), FILE_APPEND | LOCK_EX);

out(
  true,
  $alreadyActive
    ? 'This account is already activated - you\'re all set.'
    : 'Activated. Your EA will recognize the account within a few minutes.',
  array('account' => $real, 'already' => $alreadyActive)
);
