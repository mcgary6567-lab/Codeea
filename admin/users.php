<?php
/** Customer list: plan, status, per-user trading permission. Paginated. */
require_once __DIR__ . '/_boot.php';
$me = require_admin();

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    csrf_check();
    require_admin('support');
    $uid    = (int)($_POST['user_id'] ?? 0);
    $action = (string)($_POST['action'] ?? '');
    $back   = 'users.php' . (($_POST['back'] ?? '') !== '' ? '?' . preg_replace('/[^\w=&%.@+-]/', '', (string)$_POST['back']) : '');
    $u = q1('SELECT * FROM users WHERE id = ?', [$uid]);
    if (!$u) { flash('User not found.', 'err'); header("Location: $back"); exit; }

    switch ($action) {
        case 'toggle_trading':
            $new = empty($u['trading_enabled']) ? 1 : 0;
            // Turning a customer ON for live order flow is an owner decision.
            if ($new === 1) require_admin('owner');
            q('UPDATE users SET trading_enabled = ? WHERE id = ?', [$new, $uid]);
            gs_audit('admin', (int)$me['id'], 'user_trading_' . ($new ? 'enabled' : 'disabled'),
                     ['user' => $uid]);
            flash('Trading ' . ($new ? 'enabled' : 'disabled') . ' for ' . $u['email']);
            break;

        case 'suspend':
            q('UPDATE users SET status = "suspended", trading_enabled = 0 WHERE id = ?', [$uid]);
            q('UPDATE broker_accounts SET halted = 1, halt_reason = "user_suspended"
                WHERE user_id = ?', [$uid]);
            q('INSERT INTO app_commands (target_user, type, payload, created_by)
               VALUES (?, "force_logout", NULL, ?)', [$uid, (int)$me['id']]);
            gs_audit('admin', (int)$me['id'], 'user_suspended', ['user' => $uid]);
            flash('Suspended ' . $u['email'] . ' and halted their accounts.');
            break;

        case 'activate':
            q('UPDATE users SET status = "active" WHERE id = ?', [$uid]);
            gs_audit('admin', (int)$me['id'], 'user_activated', ['user' => $uid]);
            flash('Activated ' . $u['email']);
            break;

        case 'set_plan':
            $plan = (string)($_POST['plan'] ?? 'trial');
            if (!in_array($plan, ['trial', 'monthly', 'lifetime'], true)) $plan = 'trial';
            $exp = trim((string)($_POST['plan_expires'] ?? ''));
            q('UPDATE users SET plan = ?, plan_expires = ? WHERE id = ?',
              [$plan, $exp !== '' ? $exp : null, $uid]);
            gs_audit('admin', (int)$me['id'], 'user_plan_changed',
                     ['user' => $uid, 'plan' => $plan]);
            flash('Plan updated for ' . $u['email']);
            break;
    }
    header("Location: $back"); exit;
}

$search = trim((string)($_GET['q'] ?? ''));
$where  = $search !== '' ? 'WHERE u.email LIKE ? OR u.name LIKE ?' : '';
$args   = $search !== '' ? ["%$search%", "%$search%"] : [];

$total = (int)qval("SELECT COUNT(*) FROM users u $where", $args, 0);
[$page, $offset, $per, $pager] = paginate($total, 50, 'page');
$back  = http_build_query(array_filter(['q' => $search, 'page' => $page > 1 ? $page : null]));

$users = qall(
  "SELECT u.*,
          (SELECT COUNT(*) FROM broker_accounts b WHERE b.user_id = u.id) AS n_acc,
          (SELECT COUNT(*) FROM trades t WHERE t.user_id = u.id) AS n_trades,
          (SELECT COALESCE(SUM(t.profit),0) FROM trades t WHERE t.user_id = u.id) AS pnl
     FROM users u $where ORDER BY u.id DESC LIMIT $per OFFSET $offset", $args);

layout_head('Users');
?>
<h1>Users</h1>
<p class="sub"><?= number_format($total) ?> user<?= $total === 1 ? '' : 's' ?>. Suspending a user halts their accounts and signs the app out.</p>

<div class="panel">
  <form method="get" class="row">
    <div class="field" style="flex:1 1 260px">
      <label>Search</label>
      <input type="text" name="q" value="<?= h($search) ?>" placeholder="email or name">
    </div>
    <div style="align-self:end"><button class="btn ghost">Search</button></div>
  </form>
</div>

<div class="panel">
<div class="tw"><table>
  <thead><tr>
    <th class="hide-sm">#</th><th>Email</th><th>Plan</th><th>Status</th><th>Trading</th>
    <th class="num hide-sm">Accts</th><th class="num hide-sm">Trades</th><th class="num">P/L</th><th class="hide-sm">Last seen</th><th>Actions</th>
  </tr></thead>
  <tbody>
  <?php foreach ($users as $u): ?>
    <tr>
      <td class="hide-sm"><?= (int)$u['id'] ?></td>
      <td><?= h($u['email']) ?><?php if ($u['name']): ?><br><span class="note"><?= h($u['name']) ?></span><?php endif; ?></td>
      <td><span class="pill dim"><?= h($u['plan']) ?></span>
          <?php if ($u['plan_expires']): ?><br><span class="note"><?= h(substr((string)$u['plan_expires'],0,10)) ?></span><?php endif; ?></td>
      <td><?php
        $m = ['active' => 'ok', 'suspended' => 'no', 'pending' => 'mid'];
        echo '<span class="pill ' . ($m[$u['status']] ?? 'dim') . '">' . h($u['status']) . '</span>';
      ?></td>
      <td><?= $u['trading_enabled']
            ? '<span class="pill ok">ON</span>' : '<span class="pill dim">off</span>' ?></td>
      <td class="num hide-sm"><?= (int)$u['n_acc'] ?></td>
      <td class="num hide-sm"><?= (int)$u['n_trades'] ?></td>
      <td class="num <?= (float)$u['pnl'] >= 0 ? 'pos' : 'neg' ?>"><?= money((float)$u['pnl']) ?></td>
      <td class="note hide-sm"><?= h(substr((string)$u['last_seen'], 0, 16)) ?></td>
      <td><div class="actions">
        <form method="post">
          <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
          <input type="hidden" name="user_id" value="<?= (int)$u['id'] ?>">
          <input type="hidden" name="back" value="<?= h($back) ?>">
          <input type="hidden" name="action" value="toggle_trading">
          <button class="btn sm ghost"><?= $u['trading_enabled'] ? 'Disable' : 'Enable' ?></button>
        </form>
        <form method="post"
              onsubmit="return confirm('<?= $u['status'] === 'suspended' ? 'Activate' : 'Suspend' ?> this user?')">
          <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
          <input type="hidden" name="user_id" value="<?= (int)$u['id'] ?>">
          <input type="hidden" name="back" value="<?= h($back) ?>">
          <input type="hidden" name="action" value="<?= $u['status'] === 'suspended' ? 'activate' : 'suspend' ?>">
          <button class="btn sm <?= $u['status'] === 'suspended' ? 'ghost' : 'danger' ?>">
            <?= $u['status'] === 'suspended' ? 'Activate' : 'Suspend' ?></button>
        </form>
      </div></td>
    </tr>
  <?php endforeach; ?>
  <?php if (!$users): ?><tr><td colspan="10" class="empty">No users.</td></tr><?php endif; ?>
  </tbody>
</table></div>
<?= $pager ?>
</div>
<?php layout_foot();
