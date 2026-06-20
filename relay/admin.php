<?php
// Issue / list / revoke customer licence tokens. Protected by ADMIN_KEY.
//
//   Create a token (optionally label + N-day expiry):
//     https://hooks.prometheusai.tech/admin.php?key=ADMIN_KEY&action=create&label=John&days=30
//   List all tokens:
//     https://hooks.prometheusai.tech/admin.php?key=ADMIN_KEY&action=list
//   Revoke / re-activate / extend:
//     ...&action=revoke&token=XXXX
//     ...&action=activate&token=XXXX
//     ...&action=extend&token=XXXX&days=30
//
// Give the generated token to the customer; they paste it into the app.

require_once __DIR__ . '/db.php';

if (($_GET['key'] ?? '') !== ADMIN_KEY) {
    json_out(['ok' => false, 'error' => 'unauthorized'], 403);
}

$pdo = db();
$action = $_GET['action'] ?? 'list';
$token  = preg_replace('/[^A-Za-z0-9_\-]/', '', $_GET['token'] ?? '');

if ($action === 'create') {
    $newToken = bin2hex(random_bytes(10));   // 20 hex chars
    $label = substr((string)($_GET['label'] ?? ''), 0, 128);
    $days  = (int)($_GET['days'] ?? 0);
    $exp   = $days > 0 ? time() + $days * 86400 : 0;
    $pdo->prepare("INSERT INTO clients (token,label,active,expires_at,created_at) VALUES (?,?,1,?,?)")
        ->execute([$newToken, $label, $exp, time()]);
    json_out(['ok' => true, 'token' => $newToken, 'label' => $label, 'expires_at' => $exp]);
}

if ($action === 'revoke' && $token !== '') {
    $pdo->prepare("UPDATE clients SET active=0 WHERE token=?")->execute([$token]);
    json_out(['ok' => true, 'token' => $token, 'active' => 0]);
}

if ($action === 'activate' && $token !== '') {
    $pdo->prepare("UPDATE clients SET active=1 WHERE token=?")->execute([$token]);
    json_out(['ok' => true, 'token' => $token, 'active' => 1]);
}

if ($action === 'extend' && $token !== '') {
    $days = (int)($_GET['days'] ?? 30);
    $base = max(time(), 0);
    // extend from now (or current expiry if later)
    $row = $pdo->prepare("SELECT expires_at FROM clients WHERE token=?");
    $row->execute([$token]);
    $cur = (int)($row->fetch()['expires_at'] ?? 0);
    $start = $cur > time() ? $cur : time();
    $pdo->prepare("UPDATE clients SET expires_at=?, active=1 WHERE token=?")
        ->execute([$start + $days * 86400, $token]);
    json_out(['ok' => true, 'token' => $token, 'expires_at' => $start + $days * 86400]);
}

// default: list
$rows = $pdo->query("SELECT token,label,active,expires_at,last_poll_at,created_at FROM clients ORDER BY created_at DESC")->fetchAll();
json_out(['ok' => true, 'count' => count($rows), 'clients' => $rows]);
