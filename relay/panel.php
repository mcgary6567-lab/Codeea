<?php
// Browser dashboard for issuing customer licence keys — a friendly front-end to
// the same `clients` data admin.php exposes as JSON.
//
//   Open in a browser:  https://hooks.prometheusai.tech/panel.php
//   Log in once with your ADMIN_KEY (stored in a session cookie, not the URL).
//
// Enter a customer's email, click "Generate key", then copy the key (or the
// ready-made message) and send it to them. They paste it into the bot's
// Webhook tab → Licence token, which unlocks cloud signals + the built-in
// strategy. You can also revoke / re-activate / extend any key here.

session_set_cookie_params(['httponly' => true, 'samesite' => 'Lax']);
session_start();
require_once __DIR__ . '/db.php';

// Shared page chrome so the login screen and the dashboard look identical.
const PAGE_CSS = <<<CSS
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font: 14px/1.5 system-ui, Segoe UI, sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a { color:#7aa2f7; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#8a93a2; margin:0 0 20px; }
  .topbar { display:flex; justify-content:space-between; align-items:flex-start; }
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
  input, button, textarea, select { font:inherit; border-radius:8px; border:1px solid #2a313e; }
  input, textarea, select { background:#0c0e13; color:#e6e6e6; padding:7px 10px; }
  select.dur { cursor:pointer; }
  button { background:#2d6cdf; color:#fff; border-color:#2d6cdf; padding:7px 12px; cursor:pointer; }
  button.ghost { background:transparent; color:#c6ccd6; border-color:#39414f; padding:5px 10px; }
  button.danger { background:transparent; color:#ff8f8f; border-color:#5a2a2e; padding:5px 10px; }
  form.inline { display:inline; }
  .row-actions { white-space:nowrap; display:flex; gap:6px; align-items:center; }
  .create input[type=email] { width:230px; }
  .create input[type=number] { width:150px; }
  /* generated-key result */
  .result { background:#10243a; border:1px solid #2d6cdf; }
  .result .lbl { color:#8a93a2; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .bigkey { font:600 22px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; color:#9ecbff;
            background:#0c0e13; border:1px dashed #2d6cdf; border-radius:8px; padding:12px 14px;
            margin:8px 0 10px; word-break:break-all; }
  .keyrow { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  textarea.msg { width:100%; min-height:88px; margin-top:6px; resize:vertical; }
  .login-wrap { display:flex; min-height:70vh; align-items:center; justify-content:center; }
  .login-card { width:340px; }
CSS;

function head(string $title): void {
    echo '<!doctype html><html lang="en"><head><meta charset="utf-8">';
    echo '<meta name="viewport" content="width=device-width, initial-scale=1">';
    echo '<title>' . htmlspecialchars($title) . '</title><style>' . PAGE_CSS . '</style></head><body>';
}

// ---- logout -------------------------------------------------------------
if (isset($_GET['logout'])) {
    $_SESSION = [];
    session_destroy();
    header('Location: panel.php');
    exit;
}

// ---- auth (login form posts the admin key; also accept ?key= for back-compat) ----
$authed = !empty($_SESSION['pa_admin']);
if (!$authed) {
    $provided = $_POST['key'] ?? $_GET['key'] ?? null;
    if ($provided !== null && hash_equals(ADMIN_KEY, (string)$provided)) {
        session_regenerate_id(true);
        $_SESSION['pa_admin'] = 1;
        header('Location: panel.php');   // strip the key out of the URL/history
        exit;
    }
    $bad = ($provided !== null);
    head('Sign in — Prometheus Licences');
    echo '<div class="login-wrap"><div class="card login-card">';
    echo '<h1>Prometheus Licences</h1><p class="sub">Sign in with your admin key.</p>';
    if ($bad) {
        echo '<div class="flash" style="background:#3a1d1f;border-color:#5a2a2e;color:#ff8f8f">Wrong admin key.</div>';
    }
    echo '<form method="post">';
    echo '<input type="password" name="key" placeholder="Admin key" autofocus '
       . 'style="width:100%;margin-bottom:10px"><br>';
    echo '<button type="submit" style="width:100%">Sign in</button>';
    echo '</form></div></div></body></html>';
    exit;
}

$pdo = db();

// ---- mutating actions (POST → redirect, so refresh never re-submits) ----
if ($_SERVER['REQUEST_METHOD'] === 'POST' && ($_POST['action'] ?? '') !== '') {
    $action = $_POST['action'];
    $token  = preg_replace('/[^A-Za-z0-9_\-]/', '', $_POST['token'] ?? '');

    if ($action === 'create') {
        $newToken = bin2hex(random_bytes(10));                  // 20 hex chars
        $label = substr(trim((string)($_POST['label'] ?? '')), 0, 128);
        $days  = (int)($_POST['days'] ?? 0);
        $exp   = $days > 0 ? time() + $days * 86400 : 0;        // 0 = no expiry
        $pdo->prepare("INSERT INTO clients (token,label,active,expires_at,created_at) VALUES (?,?,1,?,?)")
            ->execute([$newToken, $label, $exp, time()]);
        $_SESSION['result'] = ['token' => $newToken, 'label' => $label, 'exp' => $exp];
    } elseif ($action === 'revoke' && $token !== '') {
        $pdo->prepare("UPDATE clients SET active=0 WHERE token=?")->execute([$token]);
        $_SESSION['flash'] = "Revoked $token";
    } elseif ($action === 'activate' && $token !== '') {
        $pdo->prepare("UPDATE clients SET active=1 WHERE token=?")->execute([$token]);
        $_SESSION['flash'] = "Re-activated $token";
    } elseif ($action === 'extend' && $token !== '') {
        $days  = (int)($_POST['days'] ?? 30);
        $row   = $pdo->prepare("SELECT expires_at FROM clients WHERE token=?");
        $row->execute([$token]);
        $cur   = (int)($row->fetch()['expires_at'] ?? 0);
        $start = $cur > time() ? $cur : time();
        $pdo->prepare("UPDATE clients SET expires_at=?, active=1 WHERE token=?")
            ->execute([$start + $days * 86400, $token]);
        $_SESSION['flash'] = "Extended $token by $days days";
    }
    header('Location: panel.php');
    exit;
}

$flash  = $_SESSION['flash']  ?? '';   unset($_SESSION['flash']);
$result = $_SESSION['result'] ?? null; unset($_SESSION['result']);
$rows   = $pdo->query("SELECT token,label,active,expires_at,last_poll_at,created_at FROM clients ORDER BY created_at DESC")->fetchAll();
$now    = time();

function ago($ts) {
    if (!$ts) return '—';
    $d = time() - (int)$ts;
    if ($d < 60)    return $d . 's ago';
    if ($d < 3600)  return intdiv($d, 60) . 'm ago';
    if ($d < 86400) return intdiv($d, 3600) . 'h ago';
    return intdiv($d, 86400) . 'd ago';
}

head('Prometheus — Licences');
?>
  <div class="topbar">
    <div>
      <h1>Prometheus — Customer Licences</h1>
      <p class="sub"><?= count($rows) ?> key<?= count($rows) === 1 ? '' : 's' ?> · each one = one paying customer's app.</p>
    </div>
    <a href="?logout=1" class="ghost" style="text-decoration:none"><button class="ghost" type="button">Log out</button></a>
  </div>

  <?php if ($flash): ?><div class="flash"><?= htmlspecialchars($flash) ?></div><?php endif; ?>

  <?php if ($result):
    $rt   = htmlspecialchars($result['token']);
    $rlbl = htmlspecialchars((string)$result['label']);
    $rexp = (int)$result['exp'] ? date('Y-m-d', (int)$result['exp']) : 'never';
    $msg  = "Your Prometheus AI licence key:\n\n{$result['token']}\n\n"
          . "In the bot, open the Webhook tab and paste it into \"Licence token\". "
          . "That unlocks cloud signals and the built-in strategy — then open the "
          . "Strategy tab and enable it. Keep this key private; it's tied to your subscription.";
  ?>
  <div class="card result">
    <div class="lbl">New key<?= $rlbl ? ' for ' . $rlbl : '' ?> · expires <?= $rexp ?></div>
    <div class="bigkey" id="newkey"><?= $rt ?></div>
    <div class="keyrow">
      <button type="button" onclick="copyText('<?= $rt ?>', this)">Copy key</button>
      <button type="button" class="ghost" onclick="copyText(document.getElementById('msg').value, this)">Copy message to customer</button>
    </div>
    <textarea class="msg" id="msg" readonly><?= htmlspecialchars($msg) ?></textarea>
  </div>
  <?php endif; ?>

  <div class="card create">
    <form method="post" class="inline">
      <input type="hidden" name="action" value="create">
      <input type="email"  name="label" placeholder="Customer email" required>
      <select name="days" class="dur">
        <option value="0" selected>Lifetime (no expiry)</option>
        <option value="7">7 days</option>
        <option value="30">30 days</option>
        <option value="90">90 days</option>
        <option value="365">1 year</option>
      </select>
      <button type="submit">+ Generate key</button>
    </form>
  </div>

  <div class="card">
    <table>
      <thead><tr>
        <th>Key</th><th>Email</th><th>Status</th><th>Expires</th><th>Last poll</th><th>Created</th><th></th>
      </tr></thead>
      <tbody>
      <?php foreach ($rows as $r):
        $exp = (int)$r['expires_at'];
        $expired = $exp > 0 && $exp < $now;
        if (!$r['active'])  { $cls='off'; $lbl='revoked'; }
        elseif ($expired)   { $cls='exp'; $lbl='expired'; }
        else                { $cls='on';  $lbl='active'; }
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
            <button type="button" class="ghost" onclick="copyText('<?= $t ?>', this)">Copy</button>
            <?php if ($r['active']): ?>
              <form method="post" class="inline" onsubmit="return confirm('Revoke this key? The customer\'s app will stop receiving signals and the strategy will lock.')">
                <input type="hidden" name="action" value="revoke">
                <input type="hidden" name="token" value="<?= $t ?>">
                <button class="danger" type="submit">Revoke</button>
              </form>
            <?php else: ?>
              <form method="post" class="inline">
                <input type="hidden" name="action" value="activate">
                <input type="hidden" name="token" value="<?= $t ?>">
                <button class="ghost" type="submit">Activate</button>
              </form>
            <?php endif; ?>
            <form method="post" class="inline">
              <input type="hidden" name="action" value="extend">
              <input type="hidden" name="token" value="<?= $t ?>">
              <input type="hidden" name="days" value="30">
              <button class="ghost" type="submit">+30d</button>
            </form>
          </div></td>
        </tr>
      <?php endforeach; ?>
      <?php if (!$rows): ?>
        <tr><td colspan="7" style="color:#8a93a2;padding:20px">No keys yet — generate one above.</td></tr>
      <?php endif; ?>
      </tbody>
    </table>
  </div>

<script>
function copyText(text, btn) {
  const done = () => { const o = btn.textContent; btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = o, 1200); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(fallback);
  } else { fallback(); }
  function fallback() {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    ta.remove(); done();
  }
}
</script>
</body></html>
