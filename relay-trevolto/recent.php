<?php
// Debug view: shows the most recent signals the relay RECEIVED from your
// TradingView account — without consuming them (read-only). Use this to confirm
// TradingView is actually reaching the relay, independent of the bot's polling.
//
//   https://hooks.trevolto.com/recent.php?key=ADMIN_KEY
//   https://hooks.trevolto.com/recent.php?key=ADMIN_KEY&n=30
//
// Protected by ADMIN_KEY.

require_once __DIR__ . '/db.php';

if (($_GET['key'] ?? '') !== ADMIN_KEY) {
    json_out(['ok' => false, 'error' => 'unauthorized'], 403);
}

$n = (int)($_GET['n'] ?? 20);
if ($n < 1) { $n = 1; }
if ($n > 100) { $n = 100; }

$pdo = db();
$rows = $pdo->query(
    "SELECT id, symbol, action, created_at, payload FROM signals ORDER BY id DESC LIMIT $n"
)->fetchAll();

foreach ($rows as &$r) {
    $r['received'] = gmdate('Y-m-d H:i:s', (int)$r['created_at']) . ' UTC';
    $r['ago_sec']  = time() - (int)$r['created_at'];
}

json_out(['ok' => true, 'now' => gmdate('Y-m-d H:i:s') . ' UTC',
          'count' => count($rows), 'signals' => $rows]);
