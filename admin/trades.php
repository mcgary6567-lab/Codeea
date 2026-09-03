<?php
/** Full trade log with filters, plus the audit trail. Both lists paginate. */
require_once __DIR__ . '/_boot.php';
$me = require_admin();

/* ---------------- actions ---------------- */
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    csrf_check();
    if (($_POST['action'] ?? '') === 'purge_audit') {
        require_admin('owner');
        $days = max(7, min(365, (int)($_POST['days'] ?? 30)));
        $n = q('DELETE FROM audit_log WHERE created_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL ? DAY)', [$days])->rowCount();
        gs_audit('admin', (int)$me['id'], 'audit_purged', ['older_than_days' => $days, 'rows' => $n]);
        flash("Removed $n audit entries older than $days days.");
        header('Location: trades.php'); exit;
    }
}

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

$auditTotal = (int)qval('SELECT COUNT(*) FROM audit_log', [], 0);
[$apage, $aoffset, $aper, $apager] = paginate($auditTotal, 25, 'apage');
$audit = qall("SELECT * FROM audit_log ORDER BY id DESC LIMIT $aper OFFSET $aoffset");

layout_head('Trades');
?>
<h1>Trades</h1>
<p class="sub"><?= number_format($total) ?> matching · net <span class="<?= $sum >= 0 ? 'pos' : 'neg' ?>"><?= money($sum) ?></span></p>

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

<div class="panel">
  <div class="panel-head">
    <h2>Audit trail</h2>
    <?php if ($me['role'] === 'owner'): ?>
    <form method="post" class="actions" onsubmit="return confirm('Delete audit entries older than the chosen number of days?')">
      <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
      <input type="hidden" name="action" value="purge_audit">
      <select name="days" style="width:auto;padding:.35rem .5rem">
        <option value="30">older than 30 days</option>
        <option value="90">older than 90 days</option>
        <option value="180">older than 180 days</option>
      </select>
      <button class="btn danger sm">Clear</button>
    </form>
    <?php endif; ?>
  </div>
  <div class="tw"><table>
    <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Detail</th><th class="hide-sm">IP</th></tr></thead>
    <tbody>
    <?php foreach ($audit as $a): ?>
      <tr>
        <td class="note"><?= h(substr((string)$a['created_at'], 5, 11)) ?></td>
        <td><?= h($a['actor_type']) ?><?= $a['actor_id'] ? ' #' . (int)$a['actor_id'] : '' ?></td>
        <td><?= h(str_replace('_', ' ', (string)$a['action'])) ?></td>
        <td class="note"><?= $a['detail'] !== null && $a['detail'] !== 'null' ? h(substr((string)$a['detail'], 0, 110)) : '' ?></td>
        <td class="note hide-sm"><?= h($a['ip']) ?></td>
      </tr>
    <?php endforeach; ?>
    <?php if (!$audit): ?><tr><td colspan="5" class="empty">Nothing logged yet.</td></tr><?php endif; ?>
    </tbody>
  </table></div>
  <?= $apager ?>
</div>
<?php layout_foot();
