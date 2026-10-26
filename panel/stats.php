<?php
// =====================================================================
//  Gold Scalpers panel - visitor stats dashboard
//  -------------------------------------------------------------------
//  Reads the monthly files written by /t.php (from /assets/t.js) and
//  answers one question: where do visitors come from, and how many of
//  them get to a buy button? Every number on the page comes from those
//  files - nothing is estimated. Only browsers that run JavaScript are
//  counted, so most crawlers never appear.
//  KPI trends compare the chosen period with the one right before it.
// =====================================================================
require __DIR__ . '/_common.php';
panel_require_login();

$days  = isset($_GET['d']) ? max(1, min(90, (int)$_GET['d'])) : 7;
$now   = time();
$since = $now - $days * 86400;
$prevSince = $since - $days * 86400;

// ---------------------------------------------------------------- read
$dirs = array($PRIVATE . '/.gs-stats', $SITE_ROOT . '/licdata');
$rows = array();
$firstSeen = 0;
for ($m = 0; $m <= (int)ceil(2 * $days / 28) + 1; $m++) {
  $ym = date('Y-m', strtotime(date('Y-m-01') . " -$m month"));
  foreach ($dirs as $d) {
    $f = $d . '/stats-' . $ym . '.log';
    if (!is_readable($f)) continue;
    $fh = fopen($f, 'r');
    while (($line = fgets($fh)) !== false) {
      $r = json_decode($line, true);
      if (!is_array($r) || !isset($r['t'])) continue;
      if (!$firstSeen || (int)$r['t'] < $firstSeen) $firstSeen = (int)$r['t'];
      if ((int)$r['t'] >= $prevSince) $rows[] = $r;
    }
    fclose($fh);
  }
}
usort($rows, function ($a, $b) { return $a['t'] - $b['t']; });

// ---------------------------------------------------------------- model
function source_of($ref, $us) {
  $us = strtolower(trim((string)$us));
  if ($us !== '') return 'utm: ' . $us;
  if ($ref === '' || $ref === null) return 'Direct / unknown';
  $host = preg_replace('/^www\./', '', strtolower((string)parse_url($ref, PHP_URL_HOST)));
  if ($host === '') return 'Direct / unknown';
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

// Build the sessions of one window [from, to).
function build($rows, $from, $to, $BUY) {
  $S = array(); $bots = 0; $pv = 0; $events = array(); $vis = array(); $daily = array();
  foreach ($rows as $r) {
    $t = (int)$r['t'];
    if ($t < $from || $t >= $to) continue;
    if (!empty($r['bot'])) { $bots++; continue; }
    $sid = (isset($r['sid']) && $r['sid'] !== '') ? $r['sid'] : ('v' . $r['v']);
    $day = date('Y-m-d', $t);
    if (!isset($S[$sid])) $S[$sid] = array('t0' => $t, 'land' => '', 'src' => '', 'pv' => 0, 'ms' => 0,
                                         'checkout' => false, 'buy' => false, 'thanks' => false, 'm' => (int)($r['m'] ?? 0));
    $s = &$S[$sid];
    $vis[$r['v']] = 1;
    if (!isset($daily[$day])) $daily[$day] = array('v' => array(), 'pv' => 0, 'buy' => 0);
    $daily[$day]['v'][$r['v']] = 1;
    $e = (string)$r['e'];
    $events[$e] = ($events[$e] ?? 0) + 1;
    if ($e === 'pv') {
      $pv++; $s['pv']++; $daily[$day]['pv']++;
      $p = (string)($r['p'] ?? '');
      if ($s['land'] === '') { $s['land'] = $p; $s['src'] = source_of($r['r'] ?? '', $r['us'] ?? ''); }
      if (preg_match('#^/checkout#', $p)) $s['checkout'] = true;
      if (preg_match('#^/thank-you#', $p)) $s['thanks'] = true;
    } elseif ($e === 'leave') {
      $s['ms'] += (int)($r['ms'] ?? 0);
    } else {
      if (in_array($e, $BUY, true)) { $s['buy'] = true; $daily[$day]['buy']++; }
      if ($e === 'go_checkout') $s['checkout'] = true;
    }
    unset($s);
  }
  $k = array('visitors' => count($vis), 'sessions' => count($S), 'pv' => $pv, 'bots' => $bots,
             'checkout' => 0, 'buy' => 0, 'thanks' => 0, 'quick' => 0, 'mobile' => 0, 'median_s' => 0);
  $times = array();
  foreach ($S as $s) {
    $k['checkout'] += $s['checkout'] ? 1 : 0; $k['buy'] += $s['buy'] ? 1 : 0; $k['thanks'] += $s['thanks'] ? 1 : 0;
    $k['quick'] += ($s['pv'] <= 1 && $s['ms'] < 10000) ? 1 : 0; $k['mobile'] += $s['m'];
    if ($s['ms'] > 0) $times[] = $s['ms'];
  }
  sort($times);
  $k['median_s'] = $times ? (int)round($times[(int)floor(count($times) / 2)] / 1000) : 0;
  return array('S' => $S, 'k' => $k, 'events' => $events, 'daily' => $daily);
}
function agg($S, $key) {
  $o = array();
  foreach ($S as $s) {
    $k = $s[$key] === '' ? '(none)' : $s[$key];
    if (!isset($o[$k])) $o[$k] = array('name' => $k, 'n' => 0, 'quick' => 0, 'co' => 0, 'buy' => 0, 'ty' => 0, 'ms' => 0);
    $o[$k]['n']++; $o[$k]['co'] += $s['checkout'] ? 1 : 0; $o[$k]['buy'] += $s['buy'] ? 1 : 0;
    $o[$k]['ty'] += $s['thanks'] ? 1 : 0; $o[$k]['quick'] += ($s['pv'] <= 1 && $s['ms'] < 10000) ? 1 : 0; $o[$k]['ms'] += $s['ms'];
  }
  uasort($o, function ($a, $b) { return $b['n'] - $a['n']; });
  return array_values($o);
}

$cur  = build($rows, $since, $now + 1, $BUY);
$prev = build($rows, $prevSince, $since, $BUY);
$K = $cur['k']; $P = $prev['k'];
$bySrc  = agg($cur['S'], 'src');
$byLand = agg($cur['S'], 'land');

// CSV export of the sessions in the chosen period - real rows only.
if (isset($_GET['export'])) {
  header('Content-Type: text/csv; charset=utf-8');
  header('Content-Disposition: attachment; filename="goldscalpers-sessions-' . date('Y-m-d') . '-' . $days . 'd.csv"');
  $out = fopen('php://output', 'w');
  fputcsv($out, array('started', 'source', 'landing_page', 'pages', 'seconds_on_site', 'reached_checkout', 'clicked_buy', 'thank_you_page', 'mobile'));
  foreach ($cur['S'] as $s)
    fputcsv($out, array(date('Y-m-d H:i', $s['t0']), $s['src'], $s['land'], $s['pv'], (int)round($s['ms'] / 1000),
                        $s['checkout'] ? 1 : 0, $s['buy'] ? 1 : 0, $s['thanks'] ? 1 : 0, $s['m']));
  fclose($out);
  exit;
}

// Daily series for the chart: every day in the window, empty days included.
$series = array('labels' => array(), 'visitors' => array(), 'pv' => array(), 'buy' => array());
for ($t = strtotime(date('Y-m-d', $since)); $t <= $now; $t += 86400) {
  $d = date('Y-m-d', $t);
  $series['labels'][]   = date('j M', $t);
  $series['visitors'][] = isset($cur['daily'][$d]) ? count($cur['daily'][$d]['v']) : 0;
  $series['pv'][]       = isset($cur['daily'][$d]) ? $cur['daily'][$d]['pv'] : 0;
  $series['buy'][]      = isset($cur['daily'][$d]) ? $cur['daily'][$d]['buy'] : 0;
}

// ---------------------------------------------------------------- insights
// Rules fire on real counts only. Below 30 sessions nothing is judged.
$alerts = array(); $steps = array();
$n = $K['sessions'];
$rate = function ($a, $b) { return $b > 0 ? 100 * $a / $b : 0; };
if ($n < 30) {
  $alerts[] = array('info', 'Not enough data yet', $n . ' session' . ($n === 1 ? '' : 's') . ' in this period. Rates are not meaningful until there are a few hundred; the counter started ' . ($firstSeen ? date('j M Y', $firstSeen) : 'recently') . '.');
} else {
  $coRate = $rate($K['checkout'], $n);
  if ($coRate < 2) {
    $alerts[] = array('red', 'Few visitors reach checkout', number_format($coRate, 1) . '% of sessions reached /checkout (' . $K['checkout'] . ' of ' . $n . '). Most visitors read and leave without looking at the offer.');
    $steps[] = 'Put the price, the guarantee and a link to /results near the top of the pages that bring the most sessions (see "Landing pages"), not only on the home page.';
  }
  if ($K['checkout'] >= 5 && $K['buy'] === 0) {
    $alerts[] = array('red', 'Checkout drop-off', $K['checkout'] . ' sessions reached checkout and none clicked a payment button. Something on /checkout stops them.');
    $steps[] = 'Open the checkout page on a phone and on a desktop: the price, the payment buttons and the activation steps must be visible without scrolling.';
  } elseif ($K['checkout'] >= 5) {
    $buyRate = $rate($K['buy'], $K['checkout']);
    if ($buyRate < 15) $alerts[] = array('amber', 'Weak checkout click-through', number_format($buyRate, 1) . '% of sessions that reached checkout clicked a payment button (' . $K['buy'] . ' of ' . $K['checkout'] . ').');
  }
  $qRate = $rate($K['quick'], $n);
  if ($qRate >= 60) {
    $alerts[] = array('amber', 'Most visitors leave within 10 seconds', number_format($qRate, 1) . '% of sessions viewed one page for under 10 seconds - the page did not match what they were looking for.');
    $steps[] = 'Check the search queries in Google Search Console for the top landing pages and retitle or rewrite the ones that attract the wrong searches.';
  }
  foreach ($bySrc as $s) {
    if ($s['n'] >= 20 && $s['n'] >= 0.2 * $n && $s['co'] === 0) {
      $alerts[] = array('amber', $s['name'] . ' sends traffic that never reaches checkout', $s['n'] . ' sessions (' . number_format($rate($s['n'], $n), 0) . '% of all) and 0 reached checkout.');
      $steps[] = 'Treat ' . $s['name'] . ' visitors as readers, not buyers: offer them the free MQL5 edition or the backtest instead of the $140 button.';
      break;
    }
  }
  foreach ($byLand as $l) {
    if ($l['n'] >= 15 && $rate($l['quick'], $l['n']) >= 80) {
      $alerts[] = array('amber', 'Landing page with almost no engagement', $l['name'] . ': ' . number_format($rate($l['quick'], $l['n']), 0) . '% of its ' . $l['n'] . ' sessions left within 10 seconds.');
      break;
    }
  }
  if ($K['buy'] > 0 && $K['thanks'] === 0)
    $alerts[] = array('amber', 'Buy clicks but no thank-you visits', $K['buy'] . ' payment-button clicks and no visit to /thank-you. Compare with the sales count in Gumroad: buyers who paid should open the activation page.');
  if ($P['sessions'] >= 30 && $n < 0.6 * $P['sessions'])
    $alerts[] = array('amber', 'Traffic fell', 'Sessions dropped from ' . $P['sessions'] . ' to ' . $n . ' compared with the previous ' . $days . ' days.');
}
if (!$steps) {
  if ($n < 30) {
    $steps[] = 'Let the counter run for one to two weeks before changing pages because of these numbers.';
    $steps[] = 'Compare the hosting panel\'s visit count with the visitors here: the difference is bots and crawlers.';
  } else {
    $steps[] = 'No drop-off crosses a threshold: keep the pages as they are and compare the next ' . $days . ' days with this period.';
  }
}
$steps = array_slice(array_values(array_unique($steps)), 0, 3);

function trend($cur, $prev, $higherIsBetter = true) {
  if ($prev <= 0) return array('n/a', 'flat', 'no data for the previous period');
  $ch = 100 * ($cur - $prev) / $prev;
  $good = $higherIsBetter ? $ch >= 0 : $ch <= 0;
  return array(($ch >= 0 ? '+' : '') . number_format($ch, 1) . '%', abs($ch) < 0.05 ? 'flat' : ($good ? 'up' : 'down'), 'vs previous period (' . $prev . ')');
}
function pct($a, $b) { return $b > 0 ? number_format(100 * $a / $b, 1) . '%' : '-'; }

$kpis = array(
  array('Visitors', number_format($K['visitors']), trend($K['visitors'], $P['visitors']), 'unique per day'),
  array('Sessions', number_format($K['sessions']), trend($K['sessions'], $P['sessions']), number_format($K['pv']) . ' page views'),
  array('Reached checkout', pct($K['checkout'], $K['sessions']), trend($K['checkout'], $P['checkout']), $K['checkout'] . ' sessions'),
  array('Buy-button clicks', number_format($K['buy']), trend($K['buy'], $P['buy']), $K['thanks'] . ' thank-you page visits'),
);
$payload = array(
  'series'  => $series,
  'sources' => array_map(function ($s) { return array($s['name'], $s['n']); }, array_slice($bySrc, 0, 8)),
  'funnel'  => array($K['sessions'], $K['checkout'], $K['buy'], $K['thanks']),
  'land'    => $byLand, 'src' => $bySrc, 'events' => (object)$cur['events'],
);

panel_head('Visitor stats');
panel_tabs('stats');
?>
<style>
  .dash-top{display:flex;justify-content:space-between;align-items:center;gap:.8rem;flex-wrap:wrap;margin-bottom:1rem}
  .dash-top h1{margin:0}.live{display:inline-flex;align-items:center;gap:.4rem;font-size:.78rem;color:#9aa3ad;background:#171b21;border:1px solid #262c35;border-radius:999px;padding:.3rem .7rem}
  .live i{width:7px;height:7px;border-radius:50%;background:#34d399;display:inline-block}
  .ctl{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center}.ctl .btn{margin:0}.ctl a.btn{text-decoration:none}
  .seg{display:inline-flex}.seg a{padding:.35rem .7rem;border:1px solid #2b323c;color:#e8eaed;text-decoration:none;font-size:.8rem}
  .seg a:first-child{border-radius:8px 0 0 8px}.seg a:last-child{border-radius:0 8px 8px 0}.seg a+a{border-left:0}.seg a.cur{background:#F59E0B;color:#111;border-color:#F59E0B}
  .kpi4{display:grid;grid-template-columns:repeat(4,1fr);gap:.8rem;margin-bottom:1.2rem}
  .kp{background:#171b21;border:1px solid #262c35;border-radius:12px;padding:1rem 1.1rem}
  .kp .l{font-size:.75rem;color:#9aa3ad;text-transform:uppercase;letter-spacing:.04em;font-weight:600}
  .kp .v{font-size:2rem;font-weight:800;color:#fff;margin:.25rem 0 .35rem;font-variant-numeric:tabular-nums}
  .kp .s{font-size:.76rem;color:#9aa3ad}
  .tb{display:inline-block;font-size:.72rem;font-weight:700;border-radius:999px;padding:.12rem .5rem;margin-right:.35rem}
  .tb.up{background:#064e3b;color:#a7f3d0}.tb.down{background:#7f1d1d;color:#fecaca}.tb.flat{background:#2b323c;color:#cbd2da}
  .grid2{display:grid;grid-template-columns:2fr 1fr;gap:1.2rem}.grid2b{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem}
  .chart{position:relative;height:260px}
  .mini{display:grid;grid-template-columns:repeat(5,1fr);gap:.6rem;margin-top:.8rem}
  .mini div{background:#0f1216;border:1px solid #262c35;border-radius:8px;padding:.5rem .6rem;font-size:.74rem;color:#9aa3ad}.mini b{display:block;color:#fff;font-size:1.05rem}
  .al{border-radius:10px;padding:.7rem .9rem;margin-bottom:.6rem;font-size:.86rem;border:1px solid}
  .al b{display:block;margin-bottom:.15rem}.al.red{background:rgba(127,29,29,.35);border-color:#7f1d1d;color:#fecaca}
  .al.amber{background:rgba(120,72,6,.35);border-color:#92400e;color:#fde68a}.al.info{background:#0f1216;border-color:#2b323c;color:#cbd2da}
  .steps{margin:0;padding-left:1.2rem;line-height:1.6;font-size:.9rem}
  .ttabs{display:flex;gap:.4rem;margin-bottom:.6rem;flex-wrap:wrap;align-items:center}
  .ttabs button{background:#0f1216;border:1px solid #2b323c;color:#e8eaed;border-radius:8px;padding:.35rem .75rem;cursor:pointer;font-size:.82rem}
  .ttabs button.cur{background:#F59E0B;color:#111;border-color:#F59E0B}
  .ttabs input{max-width:260px;margin-left:auto}
  .tbox{max-height:440px;overflow:auto;border:1px solid #262c35;border-radius:10px}
  .tbox table{margin:0}.tbox th{position:sticky;top:0;background:#171b21;cursor:pointer;user-select:none;white-space:nowrap}
  .tbox th.sorted::after{content:" \25BE"}.tbox th.sorted.asc::after{content:" \25B4"}
  .num{text-align:right}.empty{color:#9aa3ad;font-size:.86rem;padding:1rem}
  @media(max-width:900px){.kpi4{grid-template-columns:repeat(2,1fr)}.grid2,.grid2b{grid-template-columns:1fr}.mini{grid-template-columns:repeat(3,1fr)}}
  @media(max-width:520px){.kpi4{grid-template-columns:1fr}.mini{grid-template-columns:repeat(2,1fr)}.ttabs input{max-width:none;margin-left:0}}
</style>

<div class="dash-top">
  <div><h1>Visitor stats</h1><div class="muted" style="margin-top:.3rem">Last <?= $days ?> day<?= $days > 1 ? 's' : '' ?> &middot; <?= h(date('j M', $since)) ?> &ndash; <?= h(date('j M Y')) ?></div></div>
  <div class="ctl">
    <span class="live"><i></i>Live &middot; read <?= h(date('H:i')) ?> server time</span>
    <span class="seg"><?php foreach (array(1, 7, 30, 90) as $d): ?><a href="?d=<?= $d ?>"<?= $d === $days ? ' class="cur"' : '' ?>><?= $d ?>d</a><?php endforeach; ?></span>
    <a class="btn gray sm" href="?d=<?= $days ?>">Refresh</a>
    <a class="btn gray sm" href="?d=<?= $days ?>&amp;export=csv">Export CSV</a>
  </div>
</div>

<div class="kpi4">
<?php foreach ($kpis as $k): ?>
  <div class="kp"><div class="l"><?= h($k[0]) ?></div><div class="v"><?= h($k[1]) ?></div>
    <div class="s"><span class="tb <?= h($k[2][1]) ?>"><?= h($k[2][0]) ?></span><?= h($k[2][2]) ?></div>
    <div class="s" style="margin-top:.3rem"><?= h($k[3]) ?></div></div>
<?php endforeach; ?>
</div>

<div class="grid2">
  <div class="card"><h2>Visitors per day</h2><div class="chart"><canvas id="cDaily"></canvas></div>
    <div class="mini">
      <div><b><?= pct($K['quick'], $K['sessions']) ?></b>left within 10 s</div>
      <div><b><?= $K['median_s'] ? $K['median_s'] . ' s' : '-' ?></b>median time on site</div>
      <div><b><?= pct($K['mobile'], $K['sessions']) ?></b>on mobile</div>
      <div><b><?= $K['sessions'] ? number_format($K['pv'] / $K['sessions'], 1) : '-' ?></b>pages per session</div>
      <div><b><?= number_format($K['bots']) ?></b>bot hits dropped</div>
    </div></div>
  <div class="card"><h2>Where sessions come from</h2><div class="chart"><canvas id="cSrc"></canvas></div></div>
</div>

<div class="grid2b">
  <div class="card"><h2>Sales funnel</h2><div class="chart"><canvas id="cFunnel"></canvas></div>
    <p class="muted" style="margin:.6rem 0 0">A buy click is not a sale &mdash; Gumroad, PayPal and NOWPayments hold the actual sales.</p></div>
  <div class="card"><h2>Insights &amp; alerts</h2>
    <?php if (!$alerts): ?><div class="al info"><b>Nothing flagged</b>No drop-off crosses an alert threshold in this period.</div><?php endif; ?>
    <?php foreach ($alerts as $a): ?><div class="al <?= h($a[0]) ?>"><b><?= h($a[1]) ?></b><?= h($a[2]) ?></div><?php endforeach; ?>
    <h2 style="margin-top:1rem">Recommended next steps</h2>
    <ol class="steps"><?php foreach ($steps as $s): ?><li><?= h($s) ?></li><?php endforeach; ?></ol>
  </div>
</div>

<div class="card">
  <div class="ttabs">
    <button type="button" data-t="land" class="cur">Landing pages</button>
    <button type="button" data-t="src">Sources</button>
    <button type="button" data-t="events">Clicks</button>
    <input type="search" id="tFilter" placeholder="Filter rows...">
  </div>
  <div class="tbox"><table id="tbl"></table></div>
  <p class="muted" style="margin:.6rem 0 0">Click a column heading to sort. &ldquo;Left &lt;10 s&rdquo; = one page viewed for under 10 seconds.</p>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
(function () {
  var D = <?= json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_HEX_TAG | JSON_HEX_AMP | JSON_INVALID_UTF8_SUBSTITUTE) ?>;
  var GOLD = '#F59E0B', TEAL = '#34d399', BLUE = '#60a5fa', GRID = '#262c35', TXT = '#9aa3ad';
  if (window.Chart) {
    Chart.defaults.color = TXT; Chart.defaults.borderColor = GRID; Chart.defaults.font.family = '-apple-system,Segoe UI,Roboto,sans-serif';
    new Chart(document.getElementById('cDaily'), {
      type: 'line',
      data: { labels: D.series.labels, datasets: [
        { label: 'Visitors', data: D.series.visitors, borderColor: GOLD, backgroundColor: 'rgba(245,158,11,.15)', fill: true, tension: .3, pointRadius: 2 },
        { label: 'Page views', data: D.series.pv, borderColor: BLUE, tension: .3, pointRadius: 2 },
        { label: 'Buy clicks (right axis)', data: D.series.buy, borderColor: TEAL, tension: .3, pointRadius: 3, yAxisID: 'y2' } ] },
      options: { maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } }, y2: { beginAtZero: true, position: 'right', grid: { display: false }, ticks: { precision: 0 } } } }
    });
    if (D.sources.length) {
      new Chart(document.getElementById('cSrc'), {
        type: 'doughnut',
        data: { labels: D.sources.map(function (s) { return s[0]; }),
                datasets: [{ data: D.sources.map(function (s) { return s[1]; }), borderColor: '#171b21',
                             backgroundColor: ['#F59E0B', '#60a5fa', '#34d399', '#f472b6', '#a78bfa', '#f87171', '#fbbf24', '#94a3b8'] }] },
        options: { maintainAspectRatio: false, cutout: '62%', plugins: { legend: { position: 'bottom', labels: { boxWidth: 10 } } } }
      });
    } else document.getElementById('cSrc').parentNode.innerHTML = '<p class="empty">No sessions in this period yet.</p>';
    new Chart(document.getElementById('cFunnel'), {
      type: 'bar',
      data: { labels: ['Sessions', 'Reached checkout', 'Clicked buy', 'Thank-you page'],
              datasets: [{ data: D.funnel, backgroundColor: [BLUE, GOLD, TEAL, '#a78bfa'], borderRadius: 6 }] },
      options: { indexAxis: 'y', maintainAspectRatio: false, plugins: { legend: { display: false },
        tooltip: { callbacks: { label: function (c) { var base = D.funnel[0] || 0; return c.raw + (base ? '  (' + (100 * c.raw / base).toFixed(1) + '% of sessions)' : ''); } } } },
        scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } }
    });
  } else {
    ['cDaily', 'cSrc', 'cFunnel'].forEach(function (id) { document.getElementById(id).parentNode.innerHTML = '<p class="empty">The chart library did not load; the table below has every number.</p>'; });
  }

  // ---- sortable, filterable table ----
  function pc(a, b) { return b ? (100 * a / b) : 0; }
  var VIEWS = {
    land:   { cols: [['Landing page', 's'], ['Sessions', 'n'], ['Left <10 s', 'p'], ['Avg time', 't'], ['Reached checkout', 'n'], ['Clicked buy', 'n'], ['Thank-you', 'n']],
              rows: D.land.map(function (r) { return [r.name, r.n, pc(r.quick, r.n), r.n ? r.ms / r.n / 1000 : 0, r.co, r.buy, r.ty]; }) },
    src:    { cols: [['Source', 's'], ['Sessions', 'n'], ['Left <10 s', 'p'], ['Avg time', 't'], ['Reached checkout', 'n'], ['Clicked buy', 'n'], ['Thank-you', 'n']],
              rows: D.src.map(function (r) { return [r.name, r.n, pc(r.quick, r.n), r.n ? r.ms / r.n / 1000 : 0, r.co, r.buy, r.ty]; }) },
    events: { cols: [['Event', 's'], ['Count', 'n']],
              rows: Object.keys(D.events).map(function (k) { return [k, D.events[k]]; }) }
  };
  var view = 'land', sortCol = 1, asc = false, tbl = document.getElementById('tbl'), filt = document.getElementById('tFilter');
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function fmt(v, t) { return t === 'p' ? v.toFixed(1) + '%' : t === 't' ? Math.round(v) + ' s' : t === 'n' ? Number(v).toLocaleString() : esc(v); }
  function render() {
    var V = VIEWS[view], q = filt.value.toLowerCase();
    var rows = V.rows.filter(function (r) { return !q || String(r[0]).toLowerCase().indexOf(q) >= 0; });
    rows.sort(function (a, b) { var x = a[sortCol], y = b[sortCol]; var c = typeof x === 'string' ? x.localeCompare(y) : x - y; return asc ? c : -c; });
    var h = '<tr>' + V.cols.map(function (c, i) { return '<th data-i="' + i + '" class="' + (c[1] !== 's' ? 'num ' : '') + (i === sortCol ? 'sorted' + (asc ? ' asc' : '') : '') + '">' + esc(c[0]) + '</th>'; }).join('') + '</tr>';
    if (!rows.length) h += '<tr><td colspan="' + V.cols.length + '" class="empty">' + (V.rows.length ? 'No rows match the filter.' : 'No data in this period yet.') + '</td></tr>';
    rows.forEach(function (r) { h += '<tr>' + r.map(function (v, i) { return '<td class="' + (V.cols[i][1] !== 's' ? 'num' : '') + '">' + fmt(v, V.cols[i][1]) + '</td>'; }).join('') + '</tr>'; });
    tbl.innerHTML = h;
  }
  tbl.addEventListener('click', function (e) {
    var th = e.target.closest('th'); if (!th) return;
    var i = +th.getAttribute('data-i'); if (i === sortCol) asc = !asc; else { sortCol = i; asc = VIEWS[view].cols[i][1] === 's'; }
    render();
  });
  document.querySelectorAll('.ttabs button').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('.ttabs button').forEach(function (x) { x.classList.remove('cur'); });
      b.classList.add('cur'); view = b.getAttribute('data-t'); sortCol = 1; asc = false; render();
    });
  });
  filt.addEventListener('input', render);
  render();
})();
</script>
<?php panel_foot();
