<?php
/**
 * Admin panel bootstrap: session auth, CSRF, and the shared chrome.
 * Server-rendered, no build step, no third-party assets — it has to work on
 * shared hosting with nothing installed.
 */
declare(strict_types=1);

require_once __DIR__ . '/../lib/bootstrap.php';
require_once __DIR__ . '/../lib/crypto.php';
require_once __DIR__ . '/../lib/settings.php';

if (session_status() !== PHP_SESSION_ACTIVE) {
    session_set_cookie_params([
        'httponly' => true,
        'samesite' => 'Strict',
        'secure'   => !empty($_SERVER['HTTPS']) || ($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https',
    ]);
    session_start();
}

function admin_user(): ?array
{
    if (empty($_SESSION['admin_id'])) return null;
    return q1('SELECT * FROM admins WHERE id = ?', [$_SESSION['admin_id']]);
}

function require_admin(string $minRole = 'readonly'): array
{
    $a = admin_user();
    if (!$a) { header('Location: index.php'); exit; }
    $rank = ['readonly' => 0, 'support' => 1, 'owner' => 2];
    if (($rank[$a['role']] ?? 0) < ($rank[$minRole] ?? 0)) {
        http_response_code(403);
        exit('Insufficient role for this action.');
    }
    return $a;
}

function csrf_token(): string
{
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(16));
    }
    return $_SESSION['csrf'];
}

function csrf_check(): void
{
    $sent = (string)($_POST['csrf'] ?? '');
    if (!hash_equals((string)($_SESSION['csrf'] ?? ''), $sent)) {
        http_response_code(419);
        exit('Session expired — reload and try again.');
    }
}

function h(?string $s): string
{
    return htmlspecialchars((string)$s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function money(float $v): string
{
    return ($v < 0 ? '-' : '') . '$' . number_format(abs($v), 2);
}

function flash(?string $msg = null, string $kind = 'ok'): ?array
{
    if ($msg !== null) { $_SESSION['flash'] = ['m' => $msg, 'k' => $kind]; return null; }
    $f = $_SESSION['flash'] ?? null;
    unset($_SESSION['flash']);
    return $f;
}

function layout_head(string $title): void
{
    $a = admin_user();
    $f = flash();
    ?><!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title><?= h($title) ?> · Gold Scalpers Admin</title>
<link rel="stylesheet" href="assets/admin.css">
</head><body>
<header class="topbar">
  <a class="brand" href="dashboard.php">
    <svg width="26" height="26" viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <circle cx="16" cy="16" r="14" stroke="#F59E0B" stroke-width="1.5" opacity=".3"/>
      <circle cx="16" cy="16" r="9" stroke="#F59E0B" stroke-width="1.5"/>
      <circle cx="16" cy="16" r="2" fill="#F59E0B"/>
    </svg>
    Gold Scalpers <span>Admin</span>
  </a>
  <?php if ($a): ?>
  <nav>
    <a href="dashboard.php">Dashboard</a>
    <a href="users.php">Users</a>
    <a href="accounts.php">Accounts</a>
    <a href="control.php">Control</a>
    <a href="trades.php">Trades</a>
    <?php if ($a['role'] === 'owner'): ?><a href="settings.php">Settings</a><a href="logs.php">Logs</a><?php endif; ?>
  </nav>
  <div class="who"><?= h($a['email']) ?> <span class="role"><?= h($a['role']) ?></span>
    <a class="out" href="logout.php">Sign out</a></div>
  <?php endif; ?>
</header>
<main>
<?php if ($f): ?><div class="flash <?= h($f['k']) ?>"><?= h($f['m']) ?></div><?php endif; ?>
<?php
}

function layout_foot(): void
{
    echo "</main>\n<footer class=\"foot\">Gold Scalpers control plane · all actions are audited</footer>\n</body></html>";
}
