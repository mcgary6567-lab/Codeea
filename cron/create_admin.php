<?php
/**
 * One-off: create the first admin operator.
 *
 *   php cron/create_admin.php you@example.com 'a-long-passphrase' owner
 *
 * Delete or chmod 000 this file once you have your account.
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') { http_response_code(403); exit("cli only\n"); }

require_once __DIR__ . '/../lib/bootstrap.php';

$email = strtolower(trim((string)($argv[1] ?? '')));
$pass  = (string)($argv[2] ?? '');
$role  = (string)($argv[3] ?? 'owner');

if (!filter_var($email, FILTER_VALIDATE_EMAIL) || strlen($pass) < 12) {
    fwrite(STDERR, "usage: php create_admin.php <email> <password (12+ chars)> [owner|support|readonly]\n");
    exit(1);
}
if (!in_array($role, ['owner', 'support', 'readonly'], true)) $role = 'support';

if (qval('SELECT 1 FROM admins WHERE email = ?', [$email])) {
    q('UPDATE admins SET pass_hash = ?, role = ? WHERE email = ?',
      [password_hash($pass, PASSWORD_DEFAULT), $role, $email]);
    echo "updated existing admin $email ($role)\n";
} else {
    q('INSERT INTO admins (email, pass_hash, role) VALUES (?,?,?)',
      [$email, password_hash($pass, PASSWORD_DEFAULT), $role]);
    echo "created admin $email ($role)\n";
}
gs_audit('system', null, 'admin_created', ['email' => $email, 'role' => $role]);
echo "Now delete this script.\n";
