<?php
/** Admin sign-in. */
require_once __DIR__ . '/_boot.php';

if (admin_user()) { header('Location: dashboard.php'); exit; }

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    csrf_check();
    $email = strtolower(trim((string)($_POST['email'] ?? '')));
    $pass  = (string)($_POST['password'] ?? '');

    if (!gs_rate_limit('adm:' . gs_ip(), 10, 900)) {
        flash('Too many attempts. Wait 15 minutes.', 'err');
        header('Location: index.php'); exit;
    }

    $a = q1('SELECT * FROM admins WHERE email = ?', [$email]);
    if ($a && password_verify($pass, (string)$a['pass_hash'])) {
        session_regenerate_id(true);
        $_SESSION['admin_id'] = (int)$a['id'];
        q('UPDATE admins SET last_login = ? WHERE id = ?', [gs_now(), $a['id']]);
        gs_audit('admin', (int)$a['id'], 'admin_login');
        header('Location: dashboard.php'); exit;
    }
    gs_audit('admin', $a['id'] ?? null, 'admin_login_failed', ['email' => $email]);
    flash('Email or password is incorrect.', 'err');
    header('Location: index.php'); exit;
}

layout_head('Sign in');
?>
<div class="login">
  <div class="panel">
    <h1>Control Plane</h1>
    <form method="post">
      <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
      <div class="field">
        <label for="e">Email</label>
        <input id="e" type="email" name="email" required autocomplete="username">
      </div>
      <div class="field">
        <label for="p">Password</label>
        <input id="p" type="password" name="password" required autocomplete="current-password">
      </div>
      <button class="btn" style="width:100%">Sign in</button>
    </form>
    <p class="note">Every action in here is written to the audit log.</p>
  </div>
</div>
<?php layout_foot();
