<?php
// Self-service free-trial issuance.
//
//   POST (or GET) trial.php  with  machine=<sha256 hex>&email=<address>
//
// Hands a new customer a 10-day, full-access licence with no manual step, to
// build trust before they buy. A trial token is just a normal `clients` row
// with a 10-day expiry, so poll.php / verify.php already honour and expire it.
//
// Anti-abuse: one trial per machine fingerprint AND per email. A repeat request
// from the same machine/email restores the still-valid trial, or — once it has
// lapsed — is refused so the trial can't be reset forever.
//
// Returns:
//   { "ok": true,  "token": "...", "expires_at": <epoch>, "trial": true }
//   { "ok": false, "error": "...", "code": "used" | "bad_request" }

require_once __DIR__ . '/db.php';

const TRIAL_DAYS = 10;

$machine = preg_replace('/[^A-Za-z0-9]/', '', $_REQUEST['machine'] ?? '');
$email   = strtolower(trim((string)($_REQUEST['email'] ?? '')));
$email   = substr($email, 0, 190);

// Machine fingerprint is required; email is OPTIONAL. If supplied it must be a
// valid address (strengthens anti-abuse + lets us reach the customer).
if (strlen($machine) < 8) {
    json_out(['ok' => false, 'code' => 'bad_request',
              'error' => 'Could not identify this device. Please try again.'], 400);
}
if ($email !== '' && !filter_var($email, FILTER_VALIDATE_EMAIL)) {
    json_out(['ok' => false, 'code' => 'bad_request',
              'error' => 'That email looks invalid — fix it or leave it blank.'], 400);
}

$pdo = db();
ensure_extras($pdo);           // make sure the `note` column / extra tables exist
$now = time();

// Has this machine (or, when given, this email) already taken a trial? Never
// match on a blank email, or all email-less trials would collide.
if ($email !== '') {
    $q = $pdo->prepare("SELECT token FROM trials WHERE machine = ? OR email = ? ORDER BY created_at DESC LIMIT 1");
    $q->execute([$machine, $email]);
} else {
    $q = $pdo->prepare("SELECT token FROM trials WHERE machine = ? ORDER BY created_at DESC LIMIT 1");
    $q->execute([$machine]);
}
$prev = $q->fetch();

if ($prev) {
    // Restore the existing trial if it's still active; otherwise refuse a new one.
    $c = $pdo->prepare("SELECT token, active, expires_at FROM clients WHERE token = ?");
    $c->execute([$prev['token']]);
    $client = $c->fetch();
    if ($client && (int)$client['active']
        && ((int)$client['expires_at'] === 0 || (int)$client['expires_at'] > $now)) {
        json_out([
            'ok'         => true,
            'token'      => $client['token'],
            'expires_at' => (int)$client['expires_at'],
            'trial'      => true,
            'restored'   => true,
        ]);
    }
    json_out([
        'ok'    => false,
        'code'  => 'used',
        'error' => 'Your free trial has already been used. Click "Get License" to continue.',
    ], 403);
}

// Issue a fresh 10-day trial licence.
$token = bin2hex(random_bytes(10));
$exp   = $now + TRIAL_DAYS * 86400;
$pdo->prepare("INSERT INTO clients (token, label, note, active, expires_at, created_at) VALUES (?,?,?,1,?,?)")
    ->execute([$token, $email, 'trial', $exp, $now]);
$pdo->prepare("INSERT INTO trials (machine, email, token, created_at) VALUES (?,?,?,?)")
    ->execute([$machine, $email, $token, $now]);

json_out([
    'ok'         => true,
    'token'      => $token,
    'expires_at' => $exp,
    'trial'      => true,
]);
