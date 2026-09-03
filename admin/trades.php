<?php
/** Full trade log with filters, plus the audit trail. */
require_once __DIR__ . '/_boot.php';
require_admin();

$status = (string)($_GET['status'] ?? '');
$email  = trim((string)($_GET['email'] ?? ''));

$w = []; $args = [];
if (in_array($status, ['open','closed','rejected','sending'], true)) {
    $w[] = 't.status = ?'; $args[] = $status;
}
if ($email !== '') { $w[] = 'u.email LIKE ?'; $args[] = "%$email%"; }
$where = $w ? 'WHERE ' . implode(' AND ', $w) : '';

$trades = qall(
  "SELECT t.*, u.email FROM trades t JOIN users u ON u.id = t.user_id
   $where ORDER BY t.id DESC LIMIT 300", $args);

$sum = 0.0;
foreach ($trades as $t) $sum += (float)$t['profit'];

$audit = qall('SELECT * FROM audit_log ORDER BY id DESC LIMIT 25');

layout_head('Trades');
?>
<h1>Trades</h1>
<p class="sub"><?= count($trades) ?> shown · net <span class="<?= $sum >= 0 ? 'pos' : 'neg' ?>"><?= money($sum) ?></span></p>

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
  <thead><tr><th>#</th><th>When</th><th>User</th><th>Sym</th><th>Side</th><th>Lot</th>
    <th>Entry</th><th>SL</th><th>TP</th><th>P/L</th><th>Status</th></tr></thead>
  <tbody>
  <?php foreach ($trades as $t): ?>
    <tr>
      <td><?= (int)$t['id'] ?></td>
      <td class="note"><?= h(substr((string)$t['created_at'], 5, 11)) ?></td>
      <td><?= h($t['email']) ?></td>
      <td><?= h($t['symbol']) ?></td>
      <td><?= $t['side'] === 'buy' ? '<span class="pill ok">BUY</span>'
                                   : '<span class="pill no">SELL</span>' ?></td>
      <td><?= number_format((float)$t['lot'], 2) ?></td>
      <td><?= number_format((float)$t['entry_price'], 2) ?></td>
      <td><?= number_format((float)$t['sl'], 2) ?></td>
      <td><?= number_format((float)$t['tp'], 2) ?></td>
      <td class="<?= (float)$t['profit'] >= 0 ? 'pos' : 'neg' ?>"><?= money((float)$t['profit']) ?></td>
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
</div>

<div class="panel">
  <h2>Audit trail</h2>
  <div class="tw"><table>
    <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Detail</th><th>IP</th></tr></thead>
    <tbody>
    <?php foreach ($audit as $a): ?>
      <tr>
        <td class="note"><?= h(substr((string)$a['created_at'], 5, 11)) ?></td>
        <td><?= h($a['actor_type']) ?><?= $a['actor_id'] ? ' #' . (int)$a['actor_id'] : '' ?></td>
        <td><?= h($a['action']) ?></td>
        <td class="note"><?= h(substr((string)$a['detail'], 0, 110)) ?></td>
        <td class="note"><?= h($a['ip']) ?></td>
      </tr>
    <?php endforeach; ?>
    </tbody>
  </table></div>
</div>
<?php layout_foot();
