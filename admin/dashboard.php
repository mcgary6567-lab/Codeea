<?php
/** Operations overview — is the engine alive, who is trading, what broke. */
require_once __DIR__ . '/_boot.php';
require_admin();

$lastRun  = qval("SELECT v FROM engine_state WHERE k = 'last_run'", [], null);
$runAgeS  = $lastRun ? (time() - strtotime((string)$lastRun . ' UTC')) : null;
$engineOk = $runAgeS !== null && $runAgeS < 180;

$stats = [
  'users'     => (int)qval('SELECT COUNT(*) FROM users WHERE status = "active"', [], 0),
  'accounts'  => (int)qval('SELECT COUNT(*) FROM broker_accounts WHERE status = "connected"', [], 0),
  'live'      => (int)qval('SELECT COUNT(*) FROM broker_accounts WHERE status = "connected" AND is_demo = 0', [], 0),
  'halted'    => (int)qval('SELECT COUNT(*) FROM broker_accounts WHERE halted = 1', [], 0),
  'open'      => (int)qval('SELECT COUNT(*) FROM trades WHERE status = "open"', [], 0),
  'today'     => (float)qval('SELECT COALESCE(SUM(profit),0) FROM trades WHERE DATE(created_at) = UTC_DATE()', [], 0),
  'today_n'   => (int)qval('SELECT COUNT(*) FROM trades WHERE DATE(created_at) = UTC_DATE()', [], 0),
  'rejects'   => (int)qval('SELECT COUNT(*) FROM trades WHERE status = "rejected" AND created_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY)', [], 0),
];

$recentSignals = qall(
  'SELECT symbol, timeframe, direction, close_price, trigger_type, blocked_by, created_at
     FROM signals ORDER BY id DESC LIMIT 12');

$problemAccounts = qall(
  'SELECT a.id, a.broker_login, a.broker_server, a.status, a.status_detail,
          a.halted, a.halt_reason, a.is_demo, a.equity, u.email
     FROM broker_accounts a JOIN users u ON u.id = a.user_id
    WHERE a.halted = 1 OR a.status IN ("error","pending","deploying")
    ORDER BY a.id DESC LIMIT 15');

$recentTrades = qall(
  'SELECT t.id, t.symbol, t.side, t.lot, t.entry_price, t.profit, t.status,
          t.reject_reason, t.created_at, u.email
     FROM trades t JOIN users u ON u.id = t.user_id
    ORDER BY t.id DESC LIMIT 12');

layout_head('Dashboard');
?>
<h1>Dashboard</h1>
<p class="sub">Live state of the trading system.</p>

<div class="panel" style="<?= $engineOk ? '' : 'border-color:rgba(248,113,113,.4)' ?>">
  <h2>Engine</h2>
  <?php if ($engineOk): ?>
    <p><span class="pill ok">RUNNING</span>
       last tick <?= (int)$runAgeS ?>s ago (<?= h((string)$lastRun) ?> UTC)</p>
  <?php elseif ($lastRun): ?>
    <p><span class="pill no">STALLED</span>
       last tick <?= (int)round($runAgeS / 60) ?> min ago.
       Check the cron entry and <code>gs-engine.log</code>.</p>
  <?php else: ?>
    <p><span class="pill no">NEVER RUN</span>
       Add the cron job — see the README.</p>
  <?php endif; ?>
</div>

<?php
$maTok = (string)(gs_config()['metaapi']['token'] ?? '');
if ($maTok === '' || $maTok === 'CHANGEME'): ?>
<div class="panel" style="border-color:rgba(251,191,36,.4)">
  <h2>Execution</h2>
  <p><span class="pill mid">NOT CONFIGURED</span>
     No MetaApi token is set, so accounts cannot be provisioned and no orders can be placed.
     <?php if (admin_user()['role'] === 'owner'): ?>
       Add it under <a href="settings.php">Settings</a>.
     <?php else: ?>
       An owner can add it under Settings.
     <?php endif; ?></p>
</div>
<?php endif; ?>

<div class="grid c4" style="margin-bottom:1.2rem">
  <div class="stat"><div class="k">Active users</div><div class="v"><?= $stats['users'] ?></div></div>
  <div class="stat"><div class="k">Connected accounts</div><div class="v"><?= $stats['accounts'] ?></div>
    <div class="n"><?= $stats['live'] ?> live · <?= $stats['accounts'] - $stats['live'] ?> demo</div></div>
  <div class="stat"><div class="k">Open positions</div><div class="v"><?= $stats['open'] ?></div></div>
  <div class="stat"><div class="k">P/L today</div>
    <div class="v <?= $stats['today'] >= 0 ? 'pos' : 'neg' ?>"><?= money($stats['today']) ?></div>
    <div class="n"><?= $stats['today_n'] ?> trades</div></div>
</div>

<?php if ($stats['halted'] || $stats['rejects']): ?>
<div class="panel" style="border-color:rgba(251,191,36,.35)">
  <h2>Attention</h2>
  <?php if ($stats['halted']): ?>
    <p><span class="pill mid"><?= $stats['halted'] ?></span> account(s) halted on a risk limit.</p>
  <?php endif; ?>
  <?php if ($stats['rejects']): ?>
    <p><span class="pill mid"><?= $stats['rejects'] ?></span> order rejection(s) in the last 24h.</p>
  <?php endif; ?>
</div>
<?php endif; ?>

<div class="grid c2">
  <div class="panel">
    <h2>Recent signal evaluations</h2>
    <div class="tw"><table>
      <thead><tr><th>When</th><th>Sym</th><th>Dir</th><th>Close</th><th>Note</th></tr></thead>
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
          <td><?= number_format((float)$s['close_price'], 2) ?></td>
          <td><?= h($s['trigger_type'] ?: '') ?><?= $s['blocked_by']
                ? ' <span class="pill dim">' . h($s['blocked_by']) . '</span>' : '' ?></td>
        </tr>
      <?php endforeach; ?>
      <?php if (!$recentSignals): ?>
        <tr><td colspan="5" class="empty">No evaluations yet.</td></tr>
      <?php endif; ?>
      </tbody>
    </table></div>
  </div>

  <div class="panel">
    <h2>Accounts needing a look</h2>
    <div class="tw"><table>
      <thead><tr><th>User</th><th>Login</th><th>State</th></tr></thead>
      <tbody>
      <?php foreach ($problemAccounts as $a): ?>
        <tr>
          <td><?= h($a['email']) ?></td>
          <td><?= h($a['broker_login']) ?>
              <?= $a['is_demo'] ? '<span class="pill dim">demo</span>'
                                : '<span class="pill mid">LIVE</span>' ?></td>
          <td><?php
            if ($a['halted']) {
                echo '<span class="pill no">halted</span> ' . h($a['halt_reason']);
            } else {
                echo '<span class="pill mid">' . h($a['status']) . '</span> '
                   . h($a['status_detail']);
            }
          ?></td>
        </tr>
      <?php endforeach; ?>
      <?php if (!$problemAccounts): ?>
        <tr><td colspan="3" class="empty">Nothing needs attention.</td></tr>
      <?php endif; ?>
      </tbody>
    </table></div>
  </div>
</div>

<div class="panel">
  <h2>Latest trades</h2>
  <div class="tw"><table>
    <thead><tr><th>When</th><th>User</th><th>Sym</th><th>Side</th><th>Lot</th>
      <th>Entry</th><th>P/L</th><th>Status</th></tr></thead>
    <tbody>
    <?php foreach ($recentTrades as $t): ?>
      <tr>
        <td><?= h(substr((string)$t['created_at'], 5, 11)) ?></td>
        <td><?= h($t['email']) ?></td>
        <td><?= h($t['symbol']) ?></td>
        <td><?= $t['side'] === 'buy' ? '<span class="pill ok">BUY</span>'
                                     : '<span class="pill no">SELL</span>' ?></td>
        <td><?= number_format((float)$t['lot'], 2) ?></td>
        <td><?= number_format((float)$t['entry_price'], 2) ?></td>
        <td class="<?= (float)$t['profit'] >= 0 ? 'pos' : 'neg' ?>"><?= money((float)$t['profit']) ?></td>
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
<?php layout_foot();
