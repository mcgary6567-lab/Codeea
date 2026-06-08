<?php
// Admin-authorised PIN reset claim for the desktop app.
//
//   POST https://hooks.prometheusai.tech/reset.php   (email=..., machine=...)
//
// The PIN never leaves the user's machine (it encrypts their local vault), so
// the admin can't set it — they can only *authorise* a reset from panel.php
// (which sets clients.pin_reset_at). When the user clicks "Forgot PIN?" in the
// app and enters their registered email, the app calls this endpoint. If a
// fresh, unused authorisation exists for that licence we return the licence
// token (so the app can rebuild its vault under a new PIN without losing the
// licence) and CONSUME the authorisation. Saved exchange API keys can't be
// recovered (they were encrypted under the forgotten PIN) — the app re-prompts.
//
// Returns:
//   { "ok": true,  "token": "...", "expires_at": <epoch|0> }
//   { "ok": false, "error": "..." }   // no/expired authorisation, unknown email

require_once __DIR__ . '/db.php';

$pdo = db();
ensure_extras($pdo);                 // make sure the pin_reset_at column exists

$email = trim((string)($_POST['email'] ?? $_GET['email'] ?? ''));
if ($email === '' || strpos($email, '@') === false) {
    json_out(['ok' => false, 'error' => 'enter your registered email'], 400);
}

// One authorised, active licence per email (label). Match case-insensitively.
$c = $pdo->prepare("SELECT * FROM clients WHERE LOWER(label) = LOWER(?) AND active = 1
                    ORDER BY created_at DESC LIMIT 1");
$c->execute([$email]);
$client = $c->fetch();

if (!$client) {
    json_out(['ok' => false, 'error' => 'no active licence for that email'], 403);
}

$auth = (int)($client['pin_reset_at'] ?? 0);
$WINDOW = 24 * 3600;                  // authorisation is valid for 24h
if ($auth <= 0) {
    json_out(['ok' => false, 'error' => 'no PIN reset has been authorised — ask the admin first'], 403);
}
if (time() - $auth > $WINDOW) {
    // Expired: clear it so a stale flag can't linger, and ask them to retry.
    $pdo->prepare("UPDATE clients SET pin_reset_at = NULL WHERE token = ?")->execute([$client['token']]);
    json_out(['ok' => false, 'error' => 'the reset authorisation expired — ask the admin to authorise again'], 403);
}

// Consume the authorisation (one-time) and record where it was claimed from.
$pdo->prepare("UPDATE clients SET pin_reset_at = NULL, last_ip = ? WHERE token = ?")
    ->execute([client_ip(), $client['token']]);

json_out([
    'ok'         => true,
    'token'      => $client['token'],
    'expires_at' => (int)$client['expires_at'],
]);
