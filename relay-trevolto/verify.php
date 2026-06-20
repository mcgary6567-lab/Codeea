<?php
// Licence verification for the built-in strategy engine.
//
//   https://hooks.trevolto.com/verify.php?token=THEIR_LICENCE_TOKEN
//
// The desktop app calls this on startup (and periodically) before running its
// built-in strategy. It is READ-ONLY: unlike poll.php it never touches a
// client's signal cursor, so verifying a licence can't disturb cloud-signal
// delivery. Same licence token as the cloud feed — one token per customer.
//
// Returns:
//   { "ok": true,  "expires_at": <epoch|0>, "label": "...", "trial": bool, "server_time": N }
//   { "ok": false, "error": "invalid token" | "token disabled" | "token expired" }

require_once __DIR__ . '/db.php';

$token = preg_replace('/[^A-Za-z0-9_\-]/', '', $_GET['token'] ?? $_GET['t'] ?? '');
if ($token === '') {
    json_out(['ok' => false, 'error' => 'missing token'], 400);
}

$pdo = db();
$c = $pdo->prepare("SELECT * FROM clients WHERE token = ?");
$c->execute([$token]);
$client = $c->fetch();

if (!$client) {
    json_out(['ok' => false, 'error' => 'invalid token'], 403);
}
if (!(int)$client['active']) {
    json_out(['ok' => false, 'error' => 'token disabled'], 403);
}
if ((int)$client['expires_at'] > 0 && (int)$client['expires_at'] < time()) {
    json_out(['ok' => false, 'error' => 'token expired'], 403);
}

json_out([
    'ok'          => true,
    'expires_at'  => (int)$client['expires_at'],
    'label'       => $client['label'],
    'trial'       => (($client['note'] ?? '') === 'trial'),
    'server_time' => time(),
]);
