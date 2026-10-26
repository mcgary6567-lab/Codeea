<?php
// =====================================================================
//  Gold Scalpers panel - visitor stats
//  -------------------------------------------------------------------
//  Reads the monthly files written by /t.php (from /assets/t.js) and
//  answers one question: where do visitors come from, and how many of
//  them get to a buy button? Only browsers that run JavaScript are
//  counted, so most crawlers never appear.
// =====================================================================
require __DIR__ . '/_common.php';
panel_require_login();

$days = isset($_GET['d']) ? max(1, min(90, (int)$_GET['d'])) : 7;
$since = time() - $days * 86400;

$dirs = array($PRIVATE . '/.gs-stats', $SITE_ROOT . '/licdata');
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
panel_head('Visitor stats');
panel_tabs('stats');
?>
<div class="card">
<h1>Visitor stats &mdash; last <?= $days ?> day<?= $days > 1 ? 's' : '' ?></h1>
<div class="toolbar"><a class="btn ghost sm" href="?d=1">1 day</a><a class="btn ghost sm" href="?d=7">7 days</a><a class="btn ghost sm" href="?d=30">30 days</a><a class="btn ghost sm" href="?d=90">90 days</a></div>
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
<div class="scroll"><?php table($bySrc, 'Source'); ?></div>

<h2>Landing pages</h2>
<div class="scroll"><?php table($byLand, 'First page of the session', 40); ?></div>

<h2>Clicks</h2>
<div class="scroll"><table><tr><th>Event</th><th>Count</th></tr>
<?php foreach ($events as $k => $v) echo '<tr><td>' . h($k) . '</td><td>' . $v . '</td></tr>'; ?>
</table></div>
<p class="note">buy_gumroad / buy_paypal / buy_crypto = clicks on a payment button. go_checkout = clicks to /checkout. mql5_free / mql5_pro = clicks to the MQL5 listings. A click is not a sale: compare with Gumroad's own sales count.</p>
</div>
<?php panel_foot();
