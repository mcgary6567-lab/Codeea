<?php
/**
 * Operations overview: is the system ready, is the engine alive, who is
 * trading, what broke, and how the book is doing. Server-rendered, no
 * third-party assets; the chart is inline SVG. Reloads itself every minute.
 */
require_once __DIR__ . '/_boot.php';
$me  = require_admin();
$cfg = gs_config();

/* ---------------- actions ---------------- */
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    csrf_check();
    if (($_POST['action'] ?? '') === 'provision') {
        require_admin('support');
        require_once __DIR__ . '/../lib/provisioning.php';
        set_time_limit(120);
        $pr = gs_provision_run();
        gs_audit('admin', (int)$me['id'], 'provisioning_run_manually', $pr);
        flash(sprintf('Provisioning pass finished: %d provisioned, %d connected, %d error(s).%s',
            $pr['provisioned'], $pr['connected'], $pr['errors'],
            $pr['note'] ? ' ' . $pr['note'] . '.' : ''), $pr['errors'] ? 'err' : 'ok');
        header('Location: dashboard.php'); exit;
    }
}

/* ---------------- health ---------------- */
$lastRun    = qval("SELECT v FROM engine_state WHERE k = 'last_run'", [], null);
$runAgeS    = $lastRun ? (time() - strtotime((string)$lastRun . ' UTC')) : null;
$engineOk   = $runAgeS !== null && $runAgeS < 180;

$provRun    = qval("SELECT v FROM engine_state WHERE k = 'provision_last_run'", [], null);
$provAgeS   = $provRun ? (time() - strtotime((string)$provRun . ' UTC')) : null;
$provOk     = $provAgeS !== null && $provAgeS < 900;

$maTok      = (string)($cfg['metaapi']['token'] ?? '');
$maSet      = $maTok !== '' && $maTok !== 'CHANGEME';
$maOn       = !empty($cfg['metaapi']['enabled']);
$liveOn     = !empty($cfg['engine']['allow_live']);
$pushOn     = !empty($cfg['fcm']['enabled']);
$global     = gs_cfg_row('global', '');
$tradingOn  = !empty($global['trading_enabled']);

function ago(?int $s): string
{
    if ($s === null) return 'never';
    if ($s < 60) return $s . 's ago';
    if ($s < 3600) return (int)floor($s / 60) . ' min ago';
    if ($s < 86400) return (int)floor($s / 3600) . ' h ago';
    return (int)floor($s / 86400) . ' d ago';
}

/* ---------------- KPIs ---------------- */
$k = [
  'users'     => (int)qval('SELECT COUNT(*) FROM users WHERE status = "active"', [], 0),
  'users_new' => (int)qval('SELECT COUNT(*) FROM users WHERE created_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 7 DAY)', [], 0),
  'accounts'  => (int)qval('SELECT COUNT(*) FROM broker_accounts WHERE status = "connected"', [], 0),
  'live'      => (int)qval('SELECT COUNT(*) FROM broker_accounts WHERE status = "connected" AND is_demo = 0', [], 0),
  'settling'  => (int)qval('SELECT COUNT(*) FROM broker_accounts WHERE status IN ("pending","deploying")', [], 0),
  'halted'    => (int)qval('SELECT COUNT(*) FROM broker_accounts WHERE halted = 1', [], 0),
  'errors'    => (int)qval('SELECT COUNT(*) FROM broker_accounts WHERE status = "error"', [], 0),
  'open'      => (int)qval('SELECT COUNT(*) FROM trades WHERE status = "open"', [], 0),
  'floating'  => (float)qval('SELECT COALESCE(SUM(profit),0) FROM trades WHERE status = "open"', [], 0),
  'today'     => (float)qval('SELECT COALESCE(SUM(profit),0) FROM trades WHERE status <> "rejected" AND DATE(created_at) = UTC_DATE()', [], 0),
  'today_n'   => (int)qval('SELECT COUNT(*) FROM trades WHERE status <> "rejected" AND DATE(created_at) = UTC_DATE()', [], 0),
  'week'      => (float)qval('SELECT COALESCE(SUM(profit),0) FROM trades WHERE status <> "rejected" AND created_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 7 DAY)', [], 0),
  'closed30'  => (int)qval('SELECT COUNT(*) FROM trades WHERE status = "closed" AND created_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY)', [], 0),
  'wins30'    => (int)qval('SELECT COUNT(*) FROM trades WHERE status = "closed" AND profit > 0 AND created_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY)', [], 0),
  'rejects'   => (int)qval('SELECT COUNT(*) FROM trades WHERE status = "rejected" AND created_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY)', [], 0),
  'equity'    => (float)qval('SELECT COALESCE(SUM(equity),0) FROM broker_accounts WHERE status = "connected"', [], 0),
  'signals24' => (int)qval('SELECT COUNT(*) FROM signals WHERE direction <> 0 AND created_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY)', [], 0),
];
$winRate = $k['closed30'] > 0 ? $k['wins30'] * 100 / $k['closed30'] : null;

/* ---------------- 14-day P/L series ---------------- */
$days = [];
for ($i = 13; $i >= 0; $i--) $days[gmdate('Y-m-d', strtotime("-$i days"))] = ['p' => 0.0, 'n' => 0];
foreach (qall('SELECT DATE(created_at) AS d, COALESCE(SUM(profit),0) AS p, COUNT(*) AS n
                 FROM trades
                WHERE status <> "rejected" AND created_at > DATE_SUB(UTC_DATE(), INTERVAL 14 DAY)
                GROUP BY DATE(created_at)') as $r) {
    if (isset($days[$r['d']])) $days[$r['d']] = ['p' => (float)$r['p'], 'n' => (int)$r['n']];
}
$maxAbs = 0.0;
foreach ($days as $d) $maxAbs = max($maxAbs, abs($d['p']));

/* ---------------- tables ---------------- */
$accounts = qall(
  'SELECT a.id, a.label, a.broker_login, a.broker_server, a.status, a.status_detail,
          a.halted, a.halt_reason, a.is_demo, a.live_approved, a.balance, a.equity,
          a.day_start_equity, a.day_trades, a.last_sync, u.email,
          (SELECT COUNT(*) FROM trades t WHERE t.account_id = a.id AND t.status = "open") AS open_n
     FROM broker_accounts a JOIN users u ON u.id = a.user_id
    ORDER BY (a.halted = 1 OR a.status = "error") DESC, a.status = "connected" DESC, a.id DESC
    LIMIT 25');

$recentSignals = qall(
  'SELECT symbol, timeframe, direction, close_price, trigger_type, blocked_by, created_at
     FROM signals ORDER BY id DESC LIMIT 10');

$recentTrades = qall(
  'SELECT t.id, t.symbol, t.side, t.lot, t.entry_price, t.profit, t.status,
          t.reject_reason, t.created_at, u.email
     FROM trades t JOIN users u ON u.id = t.user_id
    ORDER BY t.id DESC LIMIT 10');

$activity = qall(
  'SELECT l.actor_type, l.actor_id, l.action, l.detail, l.created_at,
          CASE l.actor_type WHEN "admin" THEN (SELECT email FROM admins WHERE id = l.actor_id)
                            WHEN "user"  THEN (SELECT email FROM users  WHERE id = l.actor_id)
                            ELSE "system" END AS who
     FROM audit_log l ORDER BY l.id DESC LIMIT 12');

$pill = static function (string $status, int $halted): string {
    if ($halted) return '<span class="pill no">HALTED</span>';
    return match ($status) {
        'connected' => '<span class="pill ok">CONNECTED</span>',
        'pending', 'deploying' => '<span class="pill mid">CONNECTING</span>',
        'error' => '<span class="pill no">ERROR</span>',
        'disabled' => '<span class="pill dim">OFF</span>',
        default => '<span class="pill dim">' . h($status) . '</span>',
    };
};

layout_head('Dashboard');
?>
<div class="dash-head">
  <div>
    <h1>Operations</h1>
    <p class="sub" style="margin:0">Live state of the trading system · <?= gmdate('D d M, H:i') ?> UTC · refreshes every minute</p>
  </div>
  <div class="dash-actions">
    <a class="btn ghost sm" href="control.php">Control</a>
    <a class="btn ghost sm" href="accounts.php">Accounts</a>
    <?php if ($me['role'] === 'owner'): ?><a class="btn ghost sm" href="settings.php">Settings</a><?php endif; ?>
  </div>
</div>

<!-- ---------- readiness strip ---------- -->
<div class="health">
  <div class="h-item <?= $engineOk ? 'ok' : 'bad' ?>">
    <div class="h-k">Engine</div>
    <div class="h-v"><?= $engineOk ? 'Running' : ($lastRun ? 'Stalled' : 'Never run') ?></div>
    <div class="h-n">tick <?= ago($runAgeS) ?></div>
  </div>
  <div class="h-item <?= $provOk ? 'ok' : ($provRun ? 'warn' : 'dim') ?>">
    <div class="h-k">Provisioning</div>
    <div class="h-v"><?= $provOk ? 'Running' : ($provRun ? 'Stalled' : 'No data') ?></div>
    <div class="h-n">run <?= ago($provAgeS) ?>
      <?php if ($me['role'] !== 'readonly'): ?>
      · <form method="post" style="display:inline"><input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
        <input type="hidden" name="action" value="provision">
        <button class="linkbtn" type="submit">run now</button></form>
      <?php endif; ?></div>
  </div>
  <div class="h-item <?= !$maSet ? 'bad' : ($maOn ? 'ok' : 'warn') ?>">
    <div class="h-k">Execution</div>
    <div class="h-v"><?= !$maSet ? 'No MetaApi token' : ($maOn ? 'Armed' : 'Token set · off') ?></div>
    <div class="h-n"><?= !$maSet ? '<a href="settings.php">add it in Settings</a>' : ($liveOn ? 'live allowed' : 'demo only') ?></div>
  </div>
  <div class="h-item <?= $tradingOn ? 'ok' : 'warn' ?>">
    <div class="h-k">Global trading</div>
    <div class="h-v"><?= $tradingOn ? 'Enabled' : 'Monitor mode' ?></div>
    <div class="h-n"><?= $tradingOn ? 'orders allowed' : 'signals only · <a href="control.php">Control</a>' ?></div>
  </div>
  <div class="h-item <?= $pushOn ? 'ok' : 'dim' ?>">
    <div class="h-k">Push</div>
    <div class="h-v"><?= $pushOn ? 'Firebase on' : 'Polling only' ?></div>
    <div class="h-n"><?= $pushOn ? 'instant alerts' : 'app polls every minute' ?></div>
  </div>
</div>

<?php if ($k['halted'] || $k['errors'] || $k['rejects'] || (!$engineOk && $lastRun)): ?>
<div class="panel attention">
  <h2>Needs attention</h2>
  <ul class="att-list">
    <?php if (!$engineOk && $lastRun): ?><li><span class="pill no">engine</span> no tick for <?= ago($runAgeS) ?> — check the cron entry and <code>gs-engine.log</code></li><?php endif; ?>
    <?php if ($k['halted']): ?><li><span class="pill no"><?= $k['halted'] ?></span> account(s) halted on a risk rule — <a href="accounts.php">review</a></li><?php endif; ?>
    <?php if ($k['errors']): ?><li><span class="pill no"><?= $k['errors'] ?></span> account(s) failed to connect — see the table below</li><?php endif; ?>
    <?php if ($k['rejects']): ?><li><span class="pill mid"><?= $k['rejects'] ?></span> order rejection(s) in the last 24 h — see latest trades</li><?php endif; ?>
  </ul>
</div>
<?php endif; ?>

<!-- ---------- KPI tiles ---------- -->
<div class="grid kpi">
  <div class="stat">
    <div class="k">P/L today</div>
    <div class="v <?= $k['today'] >= 0 ? 'pos' : 'neg' ?>"><?= money($k['today']) ?></div>
    <div class="n"><?= $k['today_n'] ?> trade<?= $k['today_n'] === 1 ? '' : 's' ?> · <?= $k['signals24'] ?> signal<?= $k['signals24'] === 1 ? '' : 's' ?> in 24 h</div>
  </div>
  <div class="stat">
    <div class="k">P/L 7 days</div>
    <div class="v <?= $k['week'] >= 0 ? 'pos' : 'neg' ?>"><?= money($k['week']) ?></div>
    <div class="n">win rate 30 d: <?= $winRate === null ? '—' : number_format($winRate, 0) . '%' ?>
      <?= $k['closed30'] ? '(' . $k['wins30'] . '/' . $k['closed30'] . ')' : '' ?></div>
  </div>
  <div class="stat">
    <div class="k">Open positions</div>
    <div class="v"><?= $k['open'] ?></div>
    <div class="n <?= $k['floating'] >= 0 ? 'pos' : 'neg' ?>">floating <?= money($k['floating']) ?></div>
  </div>
  <div class="stat">
    <div class="k">Equity under management</div>
    <div class="v"><?= money($k['equity']) ?></div>
    <div class="n"><?= $k['accounts'] ?> connected · <?= $k['live'] ?> live · <?= $k['accounts'] - $k['live'] ?> demo</div>
  </div>
  <div class="stat">
    <div class="k">Active users</div>
    <div class="v"><?= $k['users'] ?></div>
    <div class="n">+<?= $k['users_new'] ?> this week</div>
  </div>
  <div class="stat">
    <div class="k">Connecting</div>
    <div class="v <?= $k['settling'] ? 'warn' : '' ?>"><?= $k['settling'] ?></div>
    <div class="n"><?= $k['errors'] ? '<span class="neg">' . $k['errors'] . ' in error</span>' : 'accounts being set up' ?></div>
  </div>
</div>

<!-- ---------- P/L chart ---------- -->
<div class="panel">
  <div class="panel-head">
    <h2>Daily P/L · last 14 days</h2>
    <span class="note" style="margin:0">bars = net P/L per day · label = trades</span>
  </div>
  <?php
    $w = 840; $hgt = 180; $pad = 28; $n = count($days);
    $bw = ($w - 2 * $pad) / $n; $mid = $hgt / 2;
    $scale = $maxAbs > 0 ? ($mid - 22) / $maxAbs : 0;
  ?>
  <div class="tw"><svg class="chart" viewBox="0 0 <?= $w ?> <?= $hgt + 26 ?>" preserveAspectRatio="none" role="img" aria-label="Daily profit and loss">
    <line x1="<?= $pad ?>" y1="<?= $mid ?>" x2="<?= $w - $pad ?>" y2="<?= $mid ?>" stroke="#1F2937" stroke-width="1"/>
    <?php $i = 0; foreach ($days as $date => $d):
        $x = $pad + $i * $bw + 4; $bwid = max(4, $bw - 8);
        $hh = abs($d['p']) * $scale;
        $y = $d['p'] >= 0 ? $mid - $hh : $mid;
        $col = $d['n'] === 0 ? '#1F2937' : ($d['p'] >= 0 ? '#34D399' : '#F87171');
        if ($d['n'] === 0) { $hh = 2; $y = $mid - 1; }
    ?>
      <rect x="<?= round($x, 1) ?>" y="<?= round($y, 1) ?>" width="<?= round($bwid, 1) ?>" height="<?= round(max($hh, 2), 1) ?>" rx="3" fill="<?= $col ?>">
        <title><?= $date ?>: <?= money($d['p']) ?> over <?= $d['n'] ?> trade(s)</title>
      </rect>
      <?php if ($d['n'] > 0): ?>
        <text x="<?= round($x + $bwid / 2, 1) ?>" y="<?= $d['p'] >= 0 ? round($y - 5, 1) : round($y + $hh + 12, 1) ?>" text-anchor="middle" font-size="10" fill="#94A3B8"><?= $d['n'] ?></text>
      <?php endif; ?>
      <text x="<?= round($x + $bwid / 2, 1) ?>" y="<?= $hgt + 18 ?>" text-anchor="middle" font-size="10" fill="#64748B"><?= substr($date, 5) ?></text>
    <?php $i++; endforeach; ?>
  </svg></div>
</div>

<!-- ---------- accounts ---------- -->
<div class="panel">
  <div class="panel-head">
    <h2>Accounts</h2>
    <a class="btn ghost sm" href="accounts.php">Manage</a>
  </div>
  <div class="tw"><table class="acc">
    <thead><tr><th>User</th><th>Login / server</th><th>Mode</th><th>State</th>
      <th class="num">Equity</th><th class="num">Day</th><th class="num">Open</th><th>Sync</th></tr></thead>
    <tbody>
    <?php foreach ($accounts as $a):
        $dayPl = (float)$a['equity'] - (float)($a['day_start_equity'] ?: $a['equity']);
        $syncAge = $a['last_sync'] ? time() - strtotime((string)$a['last_sync'] . ' UTC') : null;
        $rowCls = $a['halted'] || $a['status'] === 'error' ? 'row-bad'
                : (in_array($a['status'], ['pending', 'deploying'], true) ? 'row-warn' : '');
    ?>
      <tr class="<?= $rowCls ?>">
        <td><?= h($a['email']) ?><?= $a['label'] && $a['label'] !== 'MT5' ? '<div class="sub2">' . h($a['label']) . '</div>' : '' ?></td>
        <td><strong><?= h($a['broker_login']) ?></strong><div class="sub2"><?= h($a['broker_server']) ?></div></td>
        <td><?= $a['is_demo'] ? '<span class="pill dim">demo</span>'
              : '<span class="pill mid">LIVE</span>' . ($a['live_approved'] ? '' : ' <span class="pill dim">unapproved</span>') ?></td>
        <td><?= $pill((string)$a['status'], (int)$a['halted']) ?>
            <?php $detail = $a['halted'] ? $a['halt_reason'] : $a['status_detail']; ?>
            <?= $detail ? '<div class="sub2">' . h(str_replace('_', ' ', (string)$detail)) . '</div>' : '' ?></td>
        <td class="num"><?= $a['status'] === 'connected' ? money((float)$a['equity']) : '—' ?></td>
        <td class="num <?= $dayPl >= 0 ? 'pos' : 'neg' ?>"><?= $a['status'] === 'connected' ? money($dayPl) : '—' ?>
            <div class="sub2"><?= (int)$a['day_trades'] ?> trades</div></td>
        <td class="num"><?= (int)$a['open_n'] ?></td>
        <td><?= ago($syncAge) ?></td>
      </tr>
    <?php endforeach; ?>
    <?php if (!$accounts): ?><tr><td colspan="8" class="empty">No accounts linked yet.</td></tr><?php endif; ?>
    </tbody>
  </table></div>
</div>

<div class="grid c2">
  <div class="panel">
    <h2>Recent signal evaluations</h2>
    <div class="tw"><table>
      <thead><tr><th>When</th><th>Sym</th><th>Dir</th><th class="num">Close</th><th>Note</th></tr></thead>
      <tbody>
      <?php foreach ($recentSignals as $s): ?>
        <tr>
          <td><?= h(substr((string)$s['created_at'], 5, 11)) ?></td>
          <td><?= h($s['symbol']) ?> <span class="pill dim"><?= h($s['timeframe']) ?></span></td>
          <td><?php
            $d = (int)$s['direction'];
            echo $d === 1 ? '<span class="pill ok">BUY</span>'
               : ($d === -1 ? '<span class="pill no">SELL</span>'
               : '<span class="pill dim">—</span>');
          ?></td>
          <td class="num"><?= number_format((float)$s['close_price'], 2) ?></td>
          <td><?= h(str_replace('_', ' ', (string)($s['trigger_type'] ?: ''))) ?><?= $s['blocked_by']
                ? ' <span class="pill mid">' . h(str_replace('_', ' ', (string)$s['blocked_by'])) . '</span>' : '' ?></td>
        </tr>
      <?php endforeach; ?>
      <?php if (!$recentSignals): ?>
        <tr><td colspan="5" class="empty">No evaluations yet — the engine records one per closed bar.</td></tr>
      <?php endif; ?>
      </tbody>
    </table></div>
  </div>

  <div class="panel">
    <h2>Activity</h2>
    <ul class="feed">
      <?php foreach ($activity as $ev):
          $bad = (bool)preg_match('/kill|halt|fail|error|reject|suspend/i', (string)$ev['action']);
          $dt = (string)$ev['detail'];
          if ($dt === 'null') $dt = '';
          if ($dt !== '' && $dt[0] === '{') { $j = json_decode($dt, true); $dt = is_array($j) ? implode(' · ', array_map(static fn($k2, $v2) => $k2 . '=' . (is_scalar($v2) ? $v2 : json_encode($v2)), array_keys($j), $j)) : $dt; }
      ?>
        <li class="<?= $bad ? 'bad' : '' ?>">
          <span class="f-when"><?= h(substr((string)$ev['created_at'], 5, 11)) ?></span>
          <span class="f-what"><?= h(str_replace('_', ' ', (string)$ev['action'])) ?></span>
          <span class="f-who"><?= h((string)$ev['who']) ?></span>
          <?= $dt !== '' ? '<div class="sub2">' . h(substr($dt, 0, 140)) . '</div>' : '' ?>
        </li>
      <?php endforeach; ?>
      <?php if (!$activity): ?><li class="empty">Nothing logged yet.</li><?php endif; ?>
    </ul>
  </div>
</div>

<div class="panel">
  <div class="panel-head">
    <h2>Latest trades</h2>
    <a class="btn ghost sm" href="trades.php">All trades</a>
  </div>
  <div class="tw"><table>
    <thead><tr><th>When</th><th>User</th><th>Sym</th><th>Side</th><th class="num">Lot</th>
      <th class="num">Entry</th><th class="num">P/L</th><th>Status</th></tr></thead>
    <tbody>
    <?php foreach ($recentTrades as $t): ?>
      <tr>
        <td><?= h(substr((string)$t['created_at'], 5, 11)) ?></td>
        <td><?= h($t['email']) ?></td>
        <td><?= h($t['symbol']) ?></td>
        <td><?= $t['side'] === 'buy' ? '<span class="pill ok">BUY</span>'
                                     : '<span class="pill no">SELL</span>' ?></td>
        <td class="num"><?= number_format((float)$t['lot'], 2) ?></td>
        <td class="num"><?= number_format((float)$t['entry_price'], 2) ?></td>
        <td class="num <?= (float)$t['profit'] >= 0 ? 'pos' : 'neg' ?>"><?= money((float)$t['profit']) ?></td>
        <td><?php
          $cls = ['open' => 'ok', 'closed' => 'dim', 'rejected' => 'no', 'sending' => 'mid'];
          echo '<span class="pill ' . ($cls[$t['status']] ?? 'dim') . '">'
             . h($t['status']) . '</span>';
          if ($t['reject_reason']) echo ' <span class="note">' . h($t['reject_reason']) . '</span>';
        ?></td>
      </tr>
    <?php endforeach; ?>
    <?php if (!$recentTrades): ?>
      <tr><td colspan="8" class="empty">No trades yet.</td></tr>
    <?php endif; ?>
    </tbody>
  </table></div>
</div>

<script>
// Keep the picture current without anyone pressing F5. Pauses while the tab
// is hidden so a background window does not hammer the server.
setInterval(function () { if (!document.hidden) location.reload(); }, 60000);
</script>
<?php layout_foot();
