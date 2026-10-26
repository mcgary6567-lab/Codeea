<?php
// =====================================================================
//  Gold Scalpers - visitor stats (owner only)
//  -------------------------------------------------------------------
//  Reads the monthly files written by t.php and answers one question:
//  where do visitors come from, and how many of them get to a buy button?
//  Login is the same admin session as /ai-settings.php: sign in there
//  first, then open this page.
//  Only browsers that run JavaScript are counted, so most crawlers never
//  appear; the "bots" line counts the ones that ran the script anyway.
// =====================================================================
header('X-Robots-Tag: noindex, nofollow');
header('X-Frame-Options: DENY');
header('Cache-Control: no-store');
session_start();
function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }

if (empty($_SESSION['auth'])) {
  echo '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Stats</title>'
     . '<body style="font-family:system-ui;max-width:520px;margin:4rem auto;padding:0 1rem">'
     . '<h1>Visitor stats</h1><p>Sign in on the <a href="/ai-settings.php">admin page</a> with the admin password, then come back here.</p></body>';
  exit;
}

$days = isset($_GET['d']) ? max(1, min(90, (int)$_GET['d'])) : 7;
$since = time() - $days * 86400;

$dirs = array(dirname(__DIR__) . '/.gs-stats', __DIR__ . '/licdata');
$rows = array();
for ($m = 0; $m <= (int)ceil($days / 28) + 1; $m++) {
  $ym = date('Y-m', strtotime(date('Y-m-01') . " -$m month"));
  foreach ($dirs as $d) {
    $f = $d . '/stats-' . $ym . '.log';
    if (!is_readable($f)) continue;
    $fh = fopen($f, 'r');
    while (($line = fgets($fh)) !== false) {
      $r = json_decode($line, true);
      if (is_array($r) && (int)$r['t'] >= $since) $rows[] = $r;
    }
    fclose($fh);
  }
}

// ---------------------------------------------------------------- sessions
function source_of($ref, $us) {
  $us = strtolower(trim($us));
  if ($us !== '') return 'utm: ' . $us;
  if ($ref === '') return 'Direct / unknown';
  $host = strtolower((string)parse_url($ref, PHP_URL_HOST));
  $host = preg_replace('/^www\./', '', $host);
  if ($host === '' ) return 'Direct / unknown';
  if ($host === 'goldscalpers.com') return 'Internal';
  $map = array(
    'Google' => '/(^|\.)google\./', 'Bing' => '/(^|\.)bing\.com$/', 'DuckDuckGo' => '/duckduckgo\.com$/',
    'Yandex' => '/yandex\./', 'Yahoo' => '/yahoo\./', 'Brave search' => '/search\.brave\.com$/',
    'ChatGPT' => '/(chatgpt\.com|openai\.com)$/', 'Perplexity' => '/perplexity\.ai$/', 'Claude' => '/claude\.ai$/',
    'Gemini' => '/gemini\.google\.com$/', 'Copilot' => '/copilot\.microsoft\.com$/',
    'Reddit' => '/reddit\.com$/', 'Quora' => '/quora\.com$/', 'YouTube' => '/(youtube\.com|youtu\.be)$/',
    'Facebook / Instagram' => '/(facebook\.com|fb\.com|instagram\.com)$/', 'X / Twitter' => '/(t\.co|x\.com|twitter\.com)$/',
    'Telegram' => '/(t\.me|telegram\.org)$/', 'MQL5' => '/mql5\.com$/', 'Gumroad' => '/gumroad\.com$/',
    'TradingView' => '/tradingview\.com$/', 'ForexFactory' => '/forexfactory\.com$/',
  );
  foreach ($map as $name => $re) if (preg_match($re, $host)) return $name;
  return $host;
}
$BUY = array('buy_gumroad', 'buy_paypal', 'buy_crypto', 'crypto_copy_address');
$S = array(); $bots = 0; $pvTotal = 0; $events = array(); $visitors = array();
foreach ($rows as $r) {
  if (!empty($r['bot'])) { $bots++; continue; }
  $sid = $r['sid'] !== '' ? $r['sid'] : ('v' . $r['v']);
  if (!isset($S[$sid])) $S[$sid] = array('t0' => $r['t'], 'land' => '', 'src' => '', 'pv' => 0, 'ms' => 0,
                                       'checkout' => false, 'buy' => false, 'thanks' => false, 'm' => (int)$r['m'], 'ev' => array());
  $s = &$S[$sid];
  $visitors[$r['v']] = 1;
  $events[$r['e']] = ($events[$r['e']] ?? 0) + 1;
  if ($r['e'] === 'pv') {
    $pvTotal++; $s['pv']++;
    if ($s['land'] === '') { $s['land'] = $r['p']; $s['src'] = source_of($r['r'] ?? '', $r['us'] ?? ''); }
    if (preg_match('#^/checkout#', $r['p'])) $s['checkout'] = true;
    if (preg_match('#^/thank-you#', $r['p'])) $s['thanks'] = true;
  } elseif ($r['e'] === 'leave') {
    $s['ms'] += (int)$r['ms'];
  } else {
    if (in_array($r['e'], $BUY, true)) $s['buy'] = true;
    if ($r['e'] === 'go_checkout') $s['checkout'] = true;
  }
  unset($s);
}
$nS = count($S);
function pct($a, $b) { return $b > 0 ? number_format(100 * $a / $b, 1) . '%' : '-'; }
function agg($S, $key) {
  $o = array();
  foreach ($S as $s) {
    $k = $s[$key] === '' ? '(none)' : $s[$key];
    if (!isset($o[$k])) $o[$k] = array('n' => 0, 'co' => 0, 'buy' => 0, 'ty' => 0, 'quick' => 0);
    $o[$k]['n']++; $o[$k]['co'] += $s['checkout'] ? 1 : 0; $o[$k]['buy'] += $s['buy'] ? 1 : 0;
    $o[$k]['ty'] += $s['thanks'] ? 1 : 0; $o[$k]['quick'] += ($s['pv'] <= 1 && $s['ms'] < 10000) ? 1 : 0;
  }
  uasort($o, function ($a, $b) { return $b['n'] - $a['n']; });
  return $o;
}
$bySrc = agg($S, 'src'); $byLand = agg($S, 'land');
$co = 0; $buy = 0; $ty = 0; $mob = 0; $quick = 0; $times = array();
foreach ($S as $s) { $co += $s['checkout']; $buy += $s['buy']; $ty += $s['thanks']; $mob += $s['m']; $quick += ($s['pv'] <= 1 && $s['ms'] < 10000); if ($s['ms'] > 0) $times[] = $s['ms']; }
sort($times); $med = $times ? $times[(int)floor(count($times) / 2)] : 0;
arsort($events);

function table($o, $label, $limit = 25) {
  echo '<table><tr><th>' . h($label) . '</th><th>Sessions</th><th>Left in &lt;10 s</th><th>Reached checkout</th><th>Clicked buy</th><th>Thank-you page</th></tr>';
  $i = 0;
  foreach ($o as $k => $v) {
    if ($i++ >= $limit) break;
    echo '<tr><td>' . h($k) . '</td><td>' . $v['n'] . '</td><td>' . pct($v['quick'], $v['n']) . '</td><td>' . $v['co'] . ' (' . pct($v['co'], $v['n']) . ')</td><td>'
       . $v['buy'] . '</td><td>' . $v['ty'] . '</td></tr>';
  }
  echo '</table>';
}
?><!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visitor stats - Gold Scalpers</title>
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#F6F7F9;color:#0F172A}
  main{max-width:1100px;margin:0 auto;padding:1.2rem 1rem 3rem}
  h1{font-size:1.4rem;margin:.4rem 0 1rem} h2{font-size:1.05rem;margin:1.8rem 0 .6rem}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.7rem}
  .k{background:#fff;border:1px solid #E5E7EB;border-radius:10px;padding:.8rem}
  .k b{display:block;font-size:1.5rem;font-variant-numeric:tabular-nums} .k span{font-size:.78rem;color:#64748B}
  table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #E5E7EB;border-radius:10px;overflow:hidden;font-size:.86rem}
  th,td{padding:.45rem .6rem;border-bottom:1px solid #F1F5F9;text-align:left;font-variant-numeric:tabular-nums} th{background:#F8FAFC;font-weight:600}
  .wrap{overflow-x:auto} .note{font-size:.82rem;color:#64748B;max-width:780px}
  nav a{margin-right:.6rem}
</style></head><body><main>
<h1>Visitor stats &mdash; last <?= $days ?> day<?= $days > 1 ? 's' : '' ?></h1>
<nav><a href="?d=1">1 day</a><a href="?d=7">7 days</a><a href="?d=30">30 days</a><a href="?d=90">90 days</a></nav>
<p class="note">Counted from <?= h(date('j M Y', $since)) ?>. Only browsers that run JavaScript are counted, so crawlers and AI scrapers that inflate the hosting panel's numbers are not in these figures. Counting started when this page was installed.</p>

<div class="kpis">
  <div class="k"><b><?= count($visitors) ?></b><span>visitors (unique per day)</span></div>
  <div class="k"><b><?= $nS ?></b><span>sessions</span></div>
  <div class="k"><b><?= $pvTotal ?></b><span>page views</span></div>
  <div class="k"><b><?= pct($quick, $nS) ?></b><span>left within 10 s, 1 page</span></div>
  <div class="k"><b><?= $co ?></b><span>sessions reached checkout (<?= pct($co, $nS) ?>)</span></div>
  <div class="k"><b><?= $buy ?></b><span>clicked a buy button (<?= pct($buy, $nS) ?>)</span></div>
  <div class="k"><b><?= $ty ?></b><span>reached the thank-you page</span></div>
  <div class="k"><b><?= $med ? round($med / 1000) . ' s' : '-' ?></b><span>median time on site</span></div>
  <div class="k"><b><?= pct($mob, $nS) ?></b><span>on mobile</span></div>
  <div class="k"><b><?= $bots ?></b><span>bot hits dropped</span></div>
</div>

<h2>Where sessions come from</h2>
<div class="wrap"><?php table($bySrc, 'Source'); ?></div>

<h2>Landing pages</h2>
<div class="wrap"><?php table($byLand, 'First page of the session', 40); ?></div>

<h2>Clicks</h2>
<div class="wrap"><table><tr><th>Event</th><th>Count</th></tr>
<?php foreach ($events as $k => $v) echo '<tr><td>' . h($k) . '</td><td>' . $v . '</td></tr>'; ?>
</table></div>
<p class="note">buy_gumroad / buy_paypal / buy_crypto = clicks on a payment button. go_checkout = clicks to /checkout. mql5_free / mql5_pro = clicks to the MQL5 listings. A click is not a sale: compare with Gumroad's own sales count.</p>
</main></body></html>
