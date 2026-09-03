<?php
/**
 * Linked broker accounts. This is where a demo account is promoted to live —
 * deliberately a separate, owner-only, audited action.
 */
require_once __DIR__ . '/_boot.php';
$me = require_admin();

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    csrf_check();
    require_admin('support');
    $id  = (int)($_POST['account_id'] ?? 0);
    $act = (string)($_POST['action'] ?? '');
    $acc = q1('SELECT a.*, u.email FROM broker_accounts a
               JOIN users u ON u.id = a.user_id WHERE a.id = ?', [$id]);
    if (!$acc) { flash('Account not found.', 'err'); header('Location: accounts.php'); exit; }

    switch ($act) {
        case 'halt':
            q('UPDATE broker_accounts SET halted = 1, halt_reason = "admin" WHERE id = ?', [$id]);
            gs_audit('admin', (int)$me['id'], 'account_halted_manual', ['account' => $id]);
            flash('Account halted.');
            break;

        case 'unhalt':
            q("UPDATE broker_accounts SET halted = 0, halt_reason = '' WHERE id = ?", [$id]);
            gs_audit('admin', (int)$me['id'], 'account_unhalted', ['account' => $id]);
            flash('Halt cleared.');
            break;

        case 'approve_live':
            require_admin('owner');
            if ((int)$acc['is_demo'] === 1) {
                flash('That account is flagged demo — nothing to approve.', 'err');
                break;
            }
            q('UPDATE broker_accounts SET live_approved = 1 WHERE id = ?', [$id]);
            gs_audit('admin', (int)$me['id'], 'LIVE_APPROVED',
                     ['account' => $id, 'user' => $acc['email'], 'login' => $acc['broker_login']]);
            flash('LIVE trading approved for account #' . $id . '. This is logged.');
            break;

        case 'revoke_live':
            q('UPDATE broker_accounts SET live_approved = 0 WHERE id = ?', [$id]);
            gs_audit('admin', (int)$me['id'], 'live_revoked', ['account' => $id]);
            flash('Live approval revoked.');
            break;

        case 'disable':
            q('UPDATE broker_accounts SET status = "disabled", halted = 1 WHERE id = ?', [$id]);
            gs_audit('admin', (int)$me['id'], 'account_disabled', ['account' => $id]);
            flash('Account disabled.');
            break;
    }
    header('Location: accounts.php'); exit;
}

$accounts = qall(
  'SELECT a.*, u.email, u.plan,
          (SELECT COUNT(*) FROM trades t WHERE t.account_id = a.id AND t.status = "open") AS n_open
     FROM broker_accounts a JOIN users u ON u.id = a.user_id
    ORDER BY a.id DESC LIMIT 200');

layout_head('Accounts');
?>
<h1>Broker accounts</h1>
<p class="sub">Execution runs through MetaApi. Live order flow needs three things at
   once: the global <code>allow_live</code> flag, the account marked non-demo, and
   the per-account approval below.</p>

<div class="panel">
<div class="tw"><table>
  <thead><tr>
    <th>#</th><th>User</th><th>Login / server</th><th>Mode</th><th>State</th>
    <th>Balance</th><th>Equity</th><th>Open</th><th>Day</th><th>Sync</th><th>Actions</th>
  </tr></thead>
  <tbody>
  <?php foreach ($accounts as $a): ?>
    <tr>
      <td><?= (int)$a['id'] ?></td>
      <td><?= h($a['email']) ?></td>
      <td><?= h($a['broker_login']) ?><br><span class="note"><?= h($a['broker_server']) ?></span></td>
      <td><?php
        if ($a['is_demo']) echo '<span class="pill dim">demo</span>';
        elseif ($a['live_approved']) echo '<span class="pill no">LIVE ✓</span>';
        else echo '<span class="pill mid">live (unapproved)</span>';
      ?></td>
      <td><?php
        if ($a['halted']) echo '<span class="pill no">halted</span><br><span class="note">'
                             . h($a['halt_reason']) . '</span>';
        else {
            $m = ['connected'=>'ok','pending'=>'mid','deploying'=>'mid','error'=>'no','disabled'=>'dim'];
            echo '<span class="pill ' . ($m[$a['status']] ?? 'dim') . '">' . h($a['status']) . '</span>';
        }
        if ($a['status_detail']) echo '<br><span class="note">' . h($a['status_detail']) . '</span>';
      ?></td>
      <td><?= money((float)$a['balance']) ?></td>
      <td><?= money((float)$a['equity']) ?></td>
      <td><?= (int)$a['n_open'] ?></td>
      <td><?= (int)$a['day_trades'] ?></td>
      <td class="note"><?= h(substr((string)$a['last_sync'], 5, 11)) ?></td>
      <td>
        <form method="post" style="display:inline">
          <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
          <input type="hidden" name="account_id" value="<?= (int)$a['id'] ?>">
          <input type="hidden" name="action" value="<?= $a['halted'] ? 'unhalt' : 'halt' ?>">
          <button class="btn sm ghost"><?= $a['halted'] ? 'Unhalt' : 'Halt' ?></button>
        </form>
        <?php if (!$a['is_demo']): ?>
          <form method="post" style="display:inline"
                onsubmit="return confirm('<?= $a['live_approved'] ? 'Revoke' : 'APPROVE' ?> live trading for real money on account #<?= (int)$a['id'] ?>?')">
            <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
            <input type="hidden" name="account_id" value="<?= (int)$a['id'] ?>">
            <input type="hidden" name="action" value="<?= $a['live_approved'] ? 'revoke_live' : 'approve_live' ?>">
            <button class="btn sm <?= $a['live_approved'] ? 'ghost' : 'danger' ?>">
              <?= $a['live_approved'] ? 'Revoke live' : 'Approve live' ?></button>
          </form>
        <?php endif; ?>
      </td>
    </tr>
  <?php endforeach; ?>
  <?php if (!$accounts): ?><tr><td colspan="11" class="empty">No linked accounts.</td></tr><?php endif; ?>
  </tbody>
</table></div>
</div>
<?php layout_foot();
