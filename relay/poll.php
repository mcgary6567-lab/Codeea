<?php
// Customer apps poll this for new signals:
//   https://hooks.prometheusai.tech/poll.php?token=THEIR_LICENCE_TOKEN
// Returns only signals newer than what this token has already received. A brand
// new token starts from "now" (it does not replay old signals).

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

$now = time();

// First-ever poll: jump to the latest signal so we don't replay history.
if ((int)$client['last_poll_at'] === 0) {
    $maxId = (int)$pdo->query("SELECT COALESCE(MAX(id),0) AS m FROM signals")->fetch()['m'];
    $pdo->prepare("UPDATE clients SET last_seen_id=?, last_poll_at=? WHERE token=?")
        ->execute([$maxId, $now, $token]);
    json_out(['ok' => true, 'server_time' => $now, 'signals' => []]);
}

$lastSeen = (int)$client['last_seen_id'];
$q = $pdo->prepare("SELECT id, payload FROM signals WHERE id > ? ORDER BY id ASC LIMIT 50");
$q->execute([$lastSeen]);

$signals = [];
$newLast = $lastSeen;
foreach ($q->fetchAll() as $r) {
    $obj = json_decode($r['payload'], true);
    if (is_array($obj)) {
        $obj['_id'] = (int)$r['id'];
        $signals[] = $obj;
    }
    if ((int)$r['id'] > $newLast) {
        $newLast = (int)$r['id'];
    }
}

$pdo->prepare("UPDATE clients SET last_seen_id=?, last_poll_at=? WHERE token=?")
    ->execute([$newLast, $now, $token]);

json_out(['ok' => true, 'server_time' => $now, 'signals' => $signals]);
