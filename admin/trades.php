<?php
/** Full trade log with filters. Paginated. (The activity trail lives under Logs.) */
require_once __DIR__ . '/_boot.php';
$me = require_admin();

$status = (string)($_GET['status'] ?? '');
$email  = trim((string)($_GET['email'] ?? ''));

$w = []; $args = [];
if (in_array($status, ['open','closed','rejected','sending'], true)) {
    $w[] = 't.status = ?'; $args[] = $status;
}
if ($email !== '') { $w[] = 'u.email LIKE ?'; $args[] = "%$email%"; }
$where = $w ? 'WHERE ' . implode(' AND ', $w) : '';

$total = (int)qval("SELECT COUNT(*) FROM trades t JOIN users u ON u.id = t.user_id $where", $args, 0);
$sum   = (float)qval("SELECT COALESCE(SUM(t.profit),0) FROM trades t JOIN users u ON u.id = t.user_id $where", $args, 0);
[$page, $offset, $per, $pager] = paginate($total, 50, 'page');

$trades = qall(
  "SELECT t.*, u.email FROM trades t JOIN users u ON u.id = t.user_id
   $where ORDER BY t.id DESC LIMIT $per OFFSET $offset", $args);

layout_head('Trades');
?>
<div class="dash-head">
  <div>
    <h1>Trades</h1>
    <p class="sub" style="margin:0"><?= number_format($total) ?> matching · net <span class="<?= $sum >= 0 ? 'pos' : 'neg' ?>"><?= money($sum) ?></span></p>
  </div>
  <div class="dash-actions">
    <?php if ($me['role'] !== 'readonly'): ?><a class="btn ghost sm" href="logs.php?tab=activity">Activity log</a><?php endif; ?>
  </div>
</div>

<div class="panel">
  <form method="get" class="row">
    <div class="field"><label>Status</label>
      <select name="status">
        <option value="">any</option>
        <?php foreach (['open','closed','rejected','sending'] as $s): ?>
          <option <?= $status === $s ? 'selected' : '' ?>><?= $s ?></option>
        <?php endforeach; ?>
      </select></div>
    <div class="field"><label>User email</label>
      <input type="text" name="email" value="<?= h($email) ?>"></div>
    <div style="align-self:end"><button class="btn ghost">Filter</button></div>
  </form>
</div>

<div class="panel">
<div class="tw"><table>
  <thead><tr><th class="hide-sm">#</th><th>When</th><th>User</th><th>Sym</th><th>Side</th><th class="num">Lot</th>
    <th class="num">Entry</th><th class="num hide-sm">SL</th><th class="num hide-sm">TP</th><th class="num">P/L</th><th>Status</th></tr></thead>
  <tbody>
  <?php foreach ($trades as $t): ?>
    <tr>
      <td class="hide-sm"><?= (int)$t['id'] ?></td>
      <td class="note"><?= h(substr((string)$t['created_at'], 5, 11)) ?></td>
      <td><?= h($t['email']) ?></td>
      <td><?= h($t['symbol']) ?></td>
      <td><?= $t['side'] === 'buy' ? '<span class="pill ok">BUY</span>'
                                   : '<span class="pill no">SELL</span>' ?></td>
      <td class="num"><?= number_format((float)$t['lot'], 2) ?></td>
      <td class="num"><?= number_format((float)$t['entry_price'], 2) ?></td>
      <td class="num hide-sm"><?= number_format((float)$t['sl'], 2) ?></td>
      <td class="num hide-sm"><?= number_format((float)$t['tp'], 2) ?></td>
      <td class="num <?= (float)$t['profit'] >= 0 ? 'pos' : 'neg' ?>"><?= money((float)$t['profit']) ?></td>
      <td><?php
        $m = ['open'=>'ok','closed'=>'dim','rejected'=>'no','sending'=>'mid'];
        echo '<span class="pill ' . ($m[$t['status']] ?? 'dim') . '">' . h($t['status']) . '</span>';
        if ($t['reject_reason']) echo '<br><span class="note">' . h($t['reject_reason']) . '</span>';
      ?></td>
    </tr>
  <?php endforeach; ?>
  <?php if (!$trades): ?><tr><td colspan="11" class="empty">Nothing matches.</td></tr><?php endif; ?>
  </tbody>
</table></div>
<?= $pager ?>
</div>
<?php layout_foot();
