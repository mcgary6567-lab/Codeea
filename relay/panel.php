<?php
// Browser dashboard for managing customer licence tokens — a friendly front-end
// to the same data admin.php exposes as JSON. Protected by ADMIN_KEY.
//
//   Open in a browser:
//     https://hooks.prometheusai.tech/panel.php?key=ADMIN_KEY
//
//   From here you can create tokens (with a label + optional N-day expiry),
//   revoke / re-activate, and extend — all with buttons, no hand-built URLs.

require_once __DIR__ . '/db.php';

$KEY = $_GET['key'] ?? $_POST['key'] ?? '';
if ($KEY !== ADMIN_KEY) {
    http_response_code(403);
    header('Content-Type: text/html; charset=utf-8');
    echo '<!doctype html><meta charset="utf-8"><title>Unauthorized</title>'
       . '<body style="font:16px system-ui;background:#0f1115;color:#e6e6e6;'
       . 'display:flex;height:100vh;align-items:center;justify-content:center">'
       . '<p>403 — append <code>?key=YOUR_ADMIN_KEY</code> to the URL.</p>';
    exit;
}

$pdo  = db();
$flash = '';

// ---- mutating actions (POST) -------------------------------------------
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = $_POST['action'] ?? '';
    $token  = preg_replace('/[^A-Za-z0-9_\-]/', '', $_POST['token'] ?? '');
    if ($action === 'create') {
        $newToken = bin2hex(random_bytes(10));
        $label = substr((string)($_POST['label'] ?? ''), 0, 128);
        $days  = (int)($_POST['days'] ?? 0);
        $exp   = $days > 0 ? time() + $days * 86400 : 0;
        $pdo->prepare("INSERT INTO clients (token,label,active,expires_at,created_at) VALUES (?,?,1,?,?)")
            ->execute([$newToken, $label, $exp, time()]);
        $flash = "Created token <code>$newToken</code>" . ($label ? " for <b>" . htmlspecialchars($label) . "</b>" : "");
    } elseif ($action === 'revoke' && $token !== '') {
        $pdo->prepare("UPDATE clients SET active=0 WHERE token=?")->execute([$token]);
        $flash = "Revoked <code>$token</code>";
    } elseif ($action === 'activate' && $token !== '') {
        $pdo->prepare("UPDATE clients SET active=1 WHERE token=?")->execute([$token]);
        $flash = "Re-activated <code>$token</code>";
    } elseif ($action === 'extend' && $token !== '') {
        $days = (int)($_POST['days'] ?? 30);
        $row  = $pdo->prepare("SELECT expires_at FROM clients WHERE token=?");
        $row->execute([$token]);
        $cur   = (int)($row->fetch()['expires_at'] ?? 0);
        $start = $cur > time() ? $cur : time();
        $pdo->prepare("UPDATE clients SET expires_at=?, active=1 WHERE token=?")
            ->execute([$start + $days * 86400, $token]);
        $flash = "Extended <code>$token</code> by $days days";
    }
}

$rows = $pdo->query("SELECT token,label,active,expires_at,last_poll_at,created_at FROM clients ORDER BY created_at DESC")->fetchAll();
$now  = time();
$k    = htmlspecialchars($KEY);

function ago($ts) {
    if (!$ts) return '—';
    $d = time() - (int)$ts;
    if ($d < 60) return $d . 's ago';
    if ($d < 3600) return intdiv($d, 60) . 'm ago';
    if ($d < 86400) return intdiv($d, 3600) . 'h ago';
    return intdiv($d, 86400) . 'd ago';
}
?>
<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prometheus — Licences</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font: 14px/1.5 system-ui, Segoe UI, sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#8a93a2; margin:0 0 20px; }
  .flash { background:#16321f; border:1px solid #2e7d4f; color:#bdf5cf; padding:10px 14px; border-radius:8px; margin-bottom:18px; }
  .card { background:#171a21; border:1px solid #232936; border-radius:12px; padding:18px; margin-bottom:22px; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:9px 10px; border-bottom:1px solid #232936; vertical-align:middle; }
  th { color:#8a93a2; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  code { background:#0c0e13; padding:2px 6px; border-radius:5px; font-size:13px; }
  .pill { display:inline-block; padding:2px 9px; border-radius:999px; font-size:12px; font-weight:600; }
  .on  { background:#16321f; color:#5ce08a; }
  .off { background:#3a1d1f; color:#ff8f8f; }
  .exp { background:#3a2e16; color:#f5d27a; }
  input, button { font:inherit; border-radius:8px; border:1px solid #2a313e; }
  input { background:#0c0e13; color:#e6e6e6; padding:7px 10px; }
  button { background:#2d6cdf; color:#fff; border-color:#2d6cdf; padding:7px 12px; cursor:pointer; }
  button.ghost { background:transparent; color:#c6ccd6; border-color:#39414f; padding:5px 10px; }
  button.danger { background:transparent; color:#ff8f8f; border-color:#5a2a2e; padding:5px 10px; }
  form.inline { display:inline; }
  .row-actions { white-space:nowrap; display:flex; gap:6px; align-items:center; }
  .create input[type=text] { width:160px; }
  .create input[type=number] { width:90px; }
</style>
</head><body>
  <h1>Prometheus — Customer Licences</h1>
  <p class="sub"><?= count($rows) ?> token<?= count($rows) === 1 ? '' : 's' ?> · each one = one paying customer's app.</p>

  <?php if ($flash): ?><div class="flash"><?= $flash ?></div><?php endif; ?>

  <div class="card create">
    <form method="post" class="inline">
      <input type="hidden" name="key" value="<?= $k ?>">
      <input type="hidden" name="action" value="create">
      <input type="text"   name="label" placeholder="Customer name / email">
      <input type="number" name="days"  placeholder="Days (0 = no expiry)" min="0">
      <button type="submit">+ Create token</button>
    </form>
  </div>

  <div class="card">
    <table>
      <thead><tr>
        <th>Token</th><th>Label</th><th>Status</th><th>Expires</th><th>Last poll</th><th>Created</th><th></th>
      </tr></thead>
      <tbody>
      <?php foreach ($rows as $r):
        $exp = (int)$r['expires_at'];
        $expired = $exp > 0 && $exp < $now;
        if (!$r['active'])      { $cls='off'; $lbl='revoked'; }
        elseif ($expired)       { $cls='exp'; $lbl='expired'; }
        else                    { $cls='on';  $lbl='active'; }
        $t = htmlspecialchars($r['token']);
      ?>
        <tr>
          <td><code><?= $t ?></code></td>
          <td><?= htmlspecialchars((string)$r['label']) ?: '—' ?></td>
          <td><span class="pill <?= $cls ?>"><?= $lbl ?></span></td>
          <td><?= $exp ? date('Y-m-d', $exp) : 'never' ?></td>
          <td><?= ago($r['last_poll_at']) ?></td>
          <td><?= ago($r['created_at']) ?></td>
          <td><div class="row-actions">
            <?php if ($r['active']): ?>
              <form method="post" class="inline" onsubmit="return confirm('Revoke this token? The customer\'s app will stop receiving signals.')">
                <input type="hidden" name="key" value="<?= $k ?>">
                <input type="hidden" name="action" value="revoke">
                <input type="hidden" name="token" value="<?= $t ?>">
                <button class="danger" type="submit">Revoke</button>
              </form>
            <?php else: ?>
              <form method="post" class="inline">
                <input type="hidden" name="key" value="<?= $k ?>">
                <input type="hidden" name="action" value="activate">
                <input type="hidden" name="token" value="<?= $t ?>">
                <button class="ghost" type="submit">Activate</button>
              </form>
            <?php endif; ?>
            <form method="post" class="inline">
              <input type="hidden" name="key" value="<?= $k ?>">
              <input type="hidden" name="action" value="extend">
              <input type="hidden" name="token" value="<?= $t ?>">
              <input type="hidden" name="days" value="30">
              <button class="ghost" type="submit">+30d</button>
            </form>
          </div></td>
        </tr>
      <?php endforeach; ?>
      <?php if (!$rows): ?>
        <tr><td colspan="7" style="color:#8a93a2;padding:20px">No tokens yet — create one above.</td></tr>
      <?php endif; ?>
      </tbody>
    </table>
  </div>
</body></html>
