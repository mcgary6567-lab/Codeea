<?php
// Receives the TradingView webhook from YOUR account and stores the signal.
// TradingView webhook URL:  https://hooks.prometheusai.tech/hook.php?key=SELLER_KEY
// The alert's message must be the indicator's Webhook JSON payload.

require_once __DIR__ . '/db.php';

if (($_GET['key'] ?? '') !== SELLER_KEY) {
    json_out(['ok' => false, 'error' => 'unauthorized'], 403);
}

$raw = file_get_contents('php://input');
if ($raw === '' || $raw === false) {
    json_out(['ok' => false, 'error' => 'empty body'], 400);
}

$data = json_decode($raw, true);
if (!is_array($data)) {
    json_out(['ok' => false, 'error' => 'bad json'], 400);
}

$symbol = substr((string)($data['symbol'] ?? $data['ticker'] ?? ''), 0, 64);
$action = substr((string)($data['action'] ?? $data['event'] ?? ''), 0, 16);

$pdo = db();
$st = $pdo->prepare("INSERT INTO signals (payload, symbol, action, created_at) VALUES (?,?,?,?)");
$st->execute([$raw, $symbol, $action, time()]);
$id = (int)$pdo->lastInsertId();

// Keep the table small.
$pdo->prepare("DELETE FROM signals WHERE created_at < ?")->execute([time() - SIGNAL_TTL]);

json_out(['ok' => true, 'id' => $id]);
