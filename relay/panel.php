<?php
// Browser dashboard for issuing & managing customer licence keys — a friendly
// front-end to the same `clients` data admin.php exposes as JSON.
//
//   Open in a browser:  https://hooks.prometheusai.tech/panel.php
//   Log in once with your ADMIN_KEY (stored in a session cookie, not the URL).
//
// Features: generate keys (email + expiry), copy / email the key to a customer,
// search + filter + sort, renewal tools (days-left, expiring-soon, extend, set
// exact date), edit email + notes, rotate a leaked key, delete, CSV export, and
// key-sharing detection (one key polling from several IPs).

session_set_cookie_params(['httponly' => true, 'samesite' => 'Lax']);
session_start();
require_once __DIR__ . '/db.php';

const PAGE_CSS = <<<CSS
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font: 14px/1.5 system-ui, Segoe UI, sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  a { color:#7aa2f7; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#8a93a2; margin:0 0 16px; }
  .topbar { display:flex; justify-content:space-between; align-items:flex-start; }
  .flash { background:#16321f; border:1px solid #2e7d4f; color:#bdf5cf; padding:10px 14px; border-radius:8px; margin-bottom:18px; }
  .card { background:#171a21; border:1px solid #232936; border-radius:12px; padding:18px; margin-bottom:18px; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:9px 10px; border-bottom:1px solid #232936; vertical-align:middle; }
  th { color:#8a93a2; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  code { background:#0c0e13; padding:2px 6px; border-radius:5px; font-size:13px; }
  .pill { display:inline-block; padding:2px 9px; border-radius:999px; font-size:12px; font-weight:600; }
  .on  { background:#16321f; color:#5ce08a; }
  .off { background:#3a1d1f; color:#ff8f8f; }
  .exp { background:#3a2e16; color:#f5d27a; }
  .badge { display:inline-block; padding:1px 7px; border-radius:6px; font-size:11px; background:#22293a; color:#9fb0c8; }
  .warn { background:#3a2e16; color:#f5d27a; }
  .muted { color:#8a93a2; font-size:12px; }
  input, button, textarea, select { font:inherit; border-radius:8px; border:1px solid #2a313e; }
  input, textarea, select { background:#0c0e13; color:#e6e6e6; padding:7px 10px; }
  select.dur, select { cursor:pointer; }
  button { background:#2d6cdf; color:#fff; border-color:#2d6cdf; padding:7px 12px; cursor:pointer; }
  button.ghost { background:transparent; color:#c6ccd6; border-color:#39414f; padding:5px 10px; }
  button.danger { background:transparent; color:#ff8f8f; border-color:#5a2a2e; padding:5px 10px; }
  button.sm, select.sm { padding:4px 8px; font-size:12px; }
  form.inline { display:inline; }
  .row-actions { white-space:nowrap; display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
  .create input[type=email] { width:220px; }
  .create input[type=text] { width:200px; }
  .tabs { display:flex; gap:6px; flex-wrap:wrap; margin:0 0 12px; }
  .tabs a { text-decoration:none; color:#c6ccd6; border:1px solid #2a313e; padding:5px 12px; border-radius:999px; font-size:13px; }
  .tabs a.active { background:#2d6cdf; border-color:#2d6cdf; color:#fff; }
  .toolbar { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:14px; }
  .result { background:#10243a; border:1px solid #2d6cdf; }
  .result .lbl { color:#8a93a2; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .bigkey { font:600 22px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; color:#9ecbff;
            background:#0c0e13; border:1px dashed #2d6cdf; border-radius:8px; padding:12px 14px;
            margin:8px 0 10px; word-break:break-all; }
  .keyrow { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  textarea.msg { width:100%; min-height:84px; margin-top:6px; resize:vertical; }
  details.manage summary { cursor:pointer; color:#9ecbff; font-size:12px; list-style:none; }
  details.manage summary::-webkit-details-marker { display:none; }
  details.manage .panel { margin-top:8px; padding:10px; background:#0c0e13; border:1px solid #232936;
                          border-radius:8px; display:flex; flex-direction:column; gap:8px; min-width:280px; }
  a.btn { text-decoration:none; }
  .login-wrap { display:flex; min-height:70vh; align-items:center; justify-content:center; }
  .login-card { width:340px; }
  /* Clickable IP badge + popup */
  .ipbtn { cursor:pointer; border:none; font-size:11px; padding:1px 7px; line-height:1.5; }
  .ipbtn:hover { background:#4a3a1c; }
  .overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.6);
             align-items:center; justify-content:center; z-index:50; }
  .modal { background:#161b22; border:1px solid #2a313e; border-radius:12px;
           padding:18px 20px; min-width:340px; max-width:90vw; max-height:80vh;
           overflow:auto; position:relative; }
  .modalx { position:absolute; top:8px; right:10px; background:transparent; border:none;
            color:#9fb0c8; font-size:16px; cursor:pointer; padding:2px 6px; }
  .ipmodal-head { font-weight:600; margin-bottom:10px; color:#e6e6e6; padding-right:22px; }
  .iptbl { border-collapse:collapse; width:100%; margin-bottom:8px; }
  .iptbl th { text-align:left; color:#8a93a2; font-size:11px; text-transform:uppercase;
              letter-spacing:.03em; padding:4px 14px 4px 0; }
  .iptbl td { padding:5px 14px 5px 0; font-size:13px; border-top:1px solid #232936; vertical-align:top; }
CSS;

function head(string $title): void {
    echo '<!doctype html><html lang="en"><head><meta charset="utf-8">';
    echo '<meta name="viewport" content="width=device-width, initial-scale=1">';
    echo '<title>' . htmlspecialchars($title) . '</title><style>' . PAGE_CSS . '</style></head><body>';
}

function ago($ts) {
    if (!$ts) return '—';
    $d = time() - (int)$ts;
    if ($d < 60)    return $d . 's ago';
    if ($d < 3600)  return intdiv($d, 60) . 'm ago';
    if ($d < 86400) return intdiv($d, 3600) . 'h ago';
    return intdiv($d, 86400) . 'd ago';
}

function plan_label($exp, $created) {
    if (!$exp) return 'Lifetime';
    $d = ((int)$exp - (int)$created) / 86400;
    foreach ([10 => 'Trial', 7 => '7d', 30 => '30d', 90 => '90d', 365 => '1y'] as $n => $lab) {
        if (abs($d - $n) <= 2) return $lab;
    }
    return 'custom';
}

function customer_msg(string $token): string {
    return "Your Prometheus AI licence key:\n\n{$token}\n\n"
         . "In the bot, open the Webhook tab and paste it into \"Licence token\". "
         . "That unlocks cloud signals and the built-in strategy — then open the "
         . "Strategy tab and enable it. Keep this key private; it's tied to your subscription.";
}

function renewal_msg(string $token, int $exp): string {
    $when = $exp ? date('Y-m-d', $exp) : 'never';
    return "Hi — your Prometheus AI licence (key {$token}) "
         . ($exp ? "expires on {$when}." : "is lifetime.")
         . " Reply to renew and I'll extend it. Thanks!";
}

function purl(array $over = []): string {
    $p = ['f' => $_GET['f'] ?? 'all', 'q' => $_GET['q'] ?? '', 'sort' => $_GET['sort'] ?? 'new'];
    $p = array_merge($p, $over);
    $p = array_filter($p, fn($v) => $v !== '' && $v !== null && $v !== 'all' && !($v === 'new'));
    return 'panel.php' . ($p ? '?' . http_build_query($p) : '');
}

function status_of(array $r, int $now): string {
    $exp = (int)$r['expires_at'];
    if (!$r['active'])                  return 'revoked';
    if ($exp > 0 && $exp < $now)        return 'expired';
    if ($exp > 0 && $exp - $now <= 7 * 86400) return 'expiring';
    return 'active';
}

// ---- logout -------------------------------------------------------------
if (isset($_GET['logout'])) {
    $_SESSION = [];
    session_destroy();
    header('Location: panel.php');
    exit;
}

// ---- auth ---------------------------------------------------------------
$authed = !empty($_SESSION['pa_admin']);
if (!$authed) {
    $provided = $_POST['key'] ?? $_GET['key'] ?? null;
    if ($provided !== null && hash_equals(ADMIN_KEY, (string)$provided)) {
        session_regenerate_id(true);
        $_SESSION['pa_admin'] = 1;
        header('Location: panel.php');
        exit;
    }
    $bad = ($provided !== null);
    head('Sign in — Prometheus Licences');
    echo '<div class="login-wrap"><div class="card login-card">';
    echo '<h1>Prometheus Licences</h1><p class="sub">Sign in with your admin key.</p>';
    if ($bad) {
        echo '<div class="flash" style="background:#3a1d1f;border-color:#5a2a2e;color:#ff8f8f">Wrong admin key.</div>';
    }
    echo '<form method="post"><input type="password" name="key" placeholder="Admin key" autofocus '
       . 'style="width:100%;margin-bottom:10px"><br>';
    echo '<button type="submit" style="width:100%">Sign in</button></form></div></div></body></html>';
    exit;
}

$pdo = db();
ensure_extras($pdo);   // notes + IP tracking tables/columns

// ---- CSV export (before any HTML) --------------------------------------
if (($_GET['export'] ?? '') === 'csv') {
    $rows = $pdo->query("SELECT * FROM clients ORDER BY created_at DESC")->fetchAll();
    $now  = time();
    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename="prometheus_customers.csv"');
    $out = fopen('php://output', 'w');
    fputcsv($out, ['email', 'key', 'status', 'plan', 'expires', 'last_seen', 'last_ip', 'note', 'created']);
    foreach ($rows as $r) {
        $exp = (int)$r['expires_at'];
        fputcsv($out, [
            $r['label'] ?? '', $r['token'], status_of($r, $now),
            plan_label($exp, $r['created_at']),
            $exp ? date('Y-m-d', $exp) : 'lifetime',
            $r['last_poll_at'] ? date('Y-m-d H:i', (int)$r['last_poll_at']) : '',
            $r['last_ip'] ?? '', $r['note'] ?? '',
            date('Y-m-d', (int)$r['created_at']),
        ]);
    }
    fclose($out);
    exit;
}

// ---- mutating actions (POST → redirect) --------------------------------
if ($_SERVER['REQUEST_METHOD'] === 'POST' && ($_POST['action'] ?? '') !== '') {
    $action = $_POST['action'];
    $token  = preg_replace('/[^A-Za-z0-9_\-]/', '', $_POST['token'] ?? '');

    if ($action === 'create') {
        $newToken = bin2hex(random_bytes(10));
        $label = substr(trim((string)($_POST['label'] ?? '')), 0, 128);
        $note  = substr(trim((string)($_POST['note'] ?? '')), 0, 255);
        $days  = (int)($_POST['days'] ?? 0);
        $exp   = $days > 0 ? time() + $days * 86400 : 0;
        $pdo->prepare("INSERT INTO clients (token,label,note,active,expires_at,created_at) VALUES (?,?,?,1,?,?)")
            ->execute([$newToken, $label, $note, $exp, time()]);
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
    } elseif ($action === 'set_expiry' && $token !== '') {
        $date = trim((string)($_POST['date'] ?? ''));
        $ts   = $date ? strtotime($date . ' 23:59:59') : 0;
        $exp  = $ts ?: 0;
        $pdo->prepare("UPDATE clients SET expires_at=? WHERE token=?")->execute([$exp, $token]);
        $_SESSION['flash'] = $exp ? "Set expiry to $date" : "Set to lifetime";
    } elseif ($action === 'update' && $token !== '') {
        $label = substr(trim((string)($_POST['label'] ?? '')), 0, 128);
        $note  = substr(trim((string)($_POST['note'] ?? '')), 0, 255);
        $pdo->prepare("UPDATE clients SET label=?, note=? WHERE token=?")->execute([$label, $note, $token]);
        $_SESSION['flash'] = "Updated details";
    } elseif ($action === 'rotate' && $token !== '') {
        $new = bin2hex(random_bytes(10));
        $pdo->prepare("UPDATE clients SET token=? WHERE token=?")->execute([$new, $token]);
        try { $pdo->prepare("UPDATE client_ips SET token=? WHERE token=?")->execute([$new, $token]); } catch (Throwable $e) {}
        $row = $pdo->prepare("SELECT label,expires_at FROM clients WHERE token=?");
        $row->execute([$new]);
        $c = $row->fetch() ?: [];
        $_SESSION['result'] = ['token' => $new, 'label' => $c['label'] ?? '', 'exp' => (int)($c['expires_at'] ?? 0)];
        $_SESSION['flash'] = "Rotated key — send the NEW key below to the customer; the old one no longer works.";
    } elseif ($action === 'delete' && $token !== '') {
        $pdo->prepare("DELETE FROM clients WHERE token=?")->execute([$token]);
        try { $pdo->prepare("DELETE FROM client_ips WHERE token=?")->execute([$token]); } catch (Throwable $e) {}
        $_SESSION['flash'] = "Deleted $token";
    }
    header('Location: ' . purl());
    exit;
}

$flash  = $_SESSION['flash']  ?? '';   unset($_SESSION['flash']);
$result = $_SESSION['result'] ?? null; unset($_SESSION['result']);
$rows   = $pdo->query("SELECT * FROM clients ORDER BY created_at DESC")->fetchAll();
$now    = time();

// Key-sharing map: distinct IPs per token over the last 7 days.
//   $ipmap[token]    = ['n'=>count, 'ips'=>'a, b']        (badge + tooltip)
//   $ipdetail[token] = [ ['ip'=>, 'last_seen'=>, 'hits'=>], ... ]  (click popup)
$ipmap = $ipdetail = [];
try {
    $st = $pdo->prepare("SELECT token, ip, last_seen, hits FROM client_ips
                         WHERE last_seen >= ? ORDER BY last_seen DESC");
    $st->execute([$now - 7 * 86400]);
    foreach ($st->fetchAll() as $r) {
        $ipdetail[$r['token']][] = $r;
    }
    foreach ($ipdetail as $tok => $list) {
        $ipmap[$tok] = ['n' => count($list),
                        'ips' => implode(', ', array_column($list, 'ip'))];
    }
} catch (Throwable $e) { /* no IP data yet */ }

// Counts (for header + tab badges) computed from all rows.
$online = $active24 = 0;
$cnt = ['all' => 0, 'online' => 0, 'active' => 0, 'expiring' => 0, 'expired' => 0, 'revoked' => 0];
foreach ($rows as $r) {
    $cnt['all']++;
    $lp = (int)$r['last_poll_at'];
    if ($lp && $now - $lp <= 60)    { $online++; $cnt['online']++; }
    if ($lp && $now - $lp <= 86400) { $active24++; }
    $cnt[status_of($r, $now)]++;
}

// Filter + search + sort for the displayed list.
$curf = $_GET['f'] ?? 'all';
$q    = trim((string)($_GET['q'] ?? ''));
$sort = $_GET['sort'] ?? 'new';
$view = array_filter($rows, function ($r) use ($curf, $q, $now) {
    if ($curf !== 'all') {
        if ($curf === 'online') { $lp = (int)$r['last_poll_at']; if (!($lp && $now - $lp <= 60)) return false; }
        elseif (status_of($r, $now) !== $curf) return false;
    }
    if ($q !== '') {
        $hay = strtolower(($r['label'] ?? '') . ' ' . $r['token'] . ' ' . ($r['note'] ?? ''));
        if (strpos($hay, strtolower($q)) === false) return false;
    }
    return true;
});
usort($view, function ($a, $b) use ($sort) {
    if ($sort === 'expiry') {
        $ea = (int)$a['expires_at'] ?: PHP_INT_MAX;
        $eb = (int)$b['expires_at'] ?: PHP_INT_MAX;
        return $ea <=> $eb;
    }
    if ($sort === 'online') return (int)$b['last_poll_at'] <=> (int)$a['last_poll_at'];
    return (int)$b['created_at'] <=> (int)$a['created_at'];
});

head('Prometheus — Licences');
?>
  <div class="topbar">
    <div>
      <h1>Prometheus — Customer Licences</h1>
      <p class="sub">
        <span style="color:#5ce08a;font-weight:600">● <?= $online ?> online now</span>
        · <?= $active24 ?> active (24h)
        · <?= $cnt['active'] + $cnt['expiring'] ?> active licence<?= ($cnt['active'] + $cnt['expiring']) === 1 ? '' : 's' ?>
        · <?= $cnt['all'] ?> total
        <a href="<?= htmlspecialchars(purl()) ?>" style="margin-left:8px;text-decoration:none">↻ refresh</a>
      </p>
    </div>
    <a href="?logout=1" style="text-decoration:none"><button class="ghost" type="button">Log out</button></a>
  </div>

  <?php if ($flash): ?><div class="flash"><?= htmlspecialchars($flash) ?></div><?php endif; ?>

  <?php if ($result):
    $rt   = htmlspecialchars($result['token']);
    $rlbl = htmlspecialchars((string)$result['label']);
    $rexp = (int)$result['exp'] ? date('Y-m-d', (int)$result['exp']) : 'never';
    $msg  = customer_msg($result['token']);
    $mailto = '';
    if (strpos((string)$result['label'], '@') !== false) {
        $mailto = 'mailto:' . rawurlencode($result['label'])
                . '?subject=' . rawurlencode('Your Prometheus AI licence key')
                . '&body=' . rawurlencode($msg);
    }
  ?>
  <div class="card result">
    <div class="lbl">New key<?= $rlbl ? ' for ' . $rlbl : '' ?> · expires <?= $rexp ?></div>
    <div class="bigkey"><?= $rt ?></div>
    <div class="keyrow">
      <button type="button" onclick="copyText('<?= $rt ?>', this)">Copy key</button>
      <button type="button" class="ghost" onclick="copyText(document.getElementById('msg').value, this)">Copy message</button>
      <?php if ($mailto): ?><a class="btn" href="<?= htmlspecialchars($mailto) ?>"><button type="button" class="ghost">✉ Email to customer</button></a><?php endif; ?>
    </div>
    <textarea class="msg" id="msg" readonly><?= htmlspecialchars($msg) ?></textarea>
  </div>
  <?php endif; ?>

  <div class="card create">
    <form method="post" class="inline">
      <input type="hidden" name="action" value="create">
      <input type="email" name="label" placeholder="Customer email" required>
      <input type="text"  name="note"  placeholder="Note (plan, ref…) — optional">
      <select name="days" class="dur">
        <option value="0" selected>Lifetime (no expiry)</option>
        <option value="10">Trial — 10 days</option>
        <option value="7">7 days</option>
        <option value="30">30 days</option>
        <option value="90">90 days</option>
        <option value="365">1 year</option>
      </select>
      <button type="submit">+ Generate key</button>
    </form>
  </div>

  <div class="tabs">
    <?php foreach (['all'=>'All','online'=>'● Online','active'=>'Active','expiring'=>'Expiring','expired'=>'Expired','revoked'=>'Revoked'] as $key=>$lbl): ?>
      <a class="<?= $curf === $key ? 'active' : '' ?>" href="<?= htmlspecialchars(purl(['f'=>$key])) ?>"><?= $lbl ?> (<?= $cnt[$key] ?>)</a>
    <?php endforeach; ?>
  </div>

  <form method="get" class="toolbar">
    <input type="hidden" name="f" value="<?= htmlspecialchars($curf) ?>">
    <input type="text" name="q" value="<?= htmlspecialchars($q) ?>" placeholder="Search email, key or note" style="width:240px">
    <select name="sort" class="sm" onchange="this.form.submit()">
      <option value="new"    <?= $sort==='new'?'selected':'' ?>>Newest first</option>
      <option value="expiry" <?= $sort==='expiry'?'selected':'' ?>>Expiring soonest</option>
      <option value="online" <?= $sort==='online'?'selected':'' ?>>Recently online</option>
    </select>
    <button type="submit" class="sm">Search</button>
    <?php if ($q): ?><a href="<?= htmlspecialchars(purl(['q'=>''])) ?>" style="text-decoration:none"><button type="button" class="ghost sm">Clear</button></a><?php endif; ?>
    <a class="btn" href="?export=csv"><button type="button" class="ghost sm">⤓ Export CSV</button></a>
  </form>

  <div class="card">
    <table>
      <thead><tr>
        <th>Key</th><th>Email</th><th>Plan</th><th>Status</th><th>Expires</th><th>Last seen</th><th>Manage</th>
      </tr></thead>
      <tbody>
      <?php foreach ($view as $r):
        $t   = htmlspecialchars($r['token']);
        $exp = (int)$r['expires_at'];
        $st  = status_of($r, $now);
        $cls = ['revoked'=>'off','expired'=>'exp','expiring'=>'exp','active'=>'on'][$st];
        $lp  = (int)$r['last_poll_at'];
        $isOnline = $lp && ($now - $lp <= 60);
        $share = $ipmap[$r['token']] ?? null;
        $shared = $share && $share['n'] >= 2;
        $email = htmlspecialchars((string)($r['label'] ?? ''));
        $note  = htmlspecialchars((string)($r['note'] ?? ''));
      ?>
        <tr>
          <td><code><?= $t ?></code></td>
          <td>
            <?= $email ?: '—' ?>
            <?php if ($share): $n = (int)$share['n']; $lbl = $n . ' IP' . ($n === 1 ? '' : 's'); ?>
              <button type="button" class="badge ipbtn<?= $shared ? ' warn' : '' ?>"
                      title="Click to see the IP address<?= $n === 1 ? '' : 'es' ?>"
                      onclick="showIps('ipd-<?= $t ?>')"><?= $shared ? '⚠ ' : '' ?><?= $lbl ?></button>
              <template id="ipd-<?= $t ?>">
                <div class="ipmodal-head"><?= $shared ? '⚠ ' : '' ?><?= $lbl ?> · <?= $email ?: 'no email' ?></div>
                <table class="iptbl">
                  <tr><th>IP address</th><th>Last seen</th><th>Polls</th></tr>
                  <?php foreach (($ipdetail[$r['token']] ?? []) as $d): ?>
                    <tr><td><code><?= htmlspecialchars($d['ip']) ?></code></td>
                        <td><?= ago((int)$d['last_seen']) ?></td>
                        <td><?= (int)$d['hits'] ?></td></tr>
                  <?php endforeach; ?>
                </table>
                <?php if ($shared): ?>
                  <div class="muted">Several IPs on one key can mean key-sharing — or just
                    the customer using it from home plus a VPS/phone (last 7 days).</div>
                <?php else: ?>
                  <div class="muted">Single IP seen in the last 7 days.</div>
                <?php endif; ?>
              </template>
            <?php endif; ?>
            <?php if ($note): ?><div class="muted"><?= $note ?></div><?php endif; ?>
          </td>
          <td><span class="badge"><?= plan_label($exp, $r['created_at']) ?></span></td>
          <td><span class="pill <?= $cls ?>"><?= $st ?></span></td>
          <td>
            <?php if (!$exp): ?>—
            <?php else: $dl = (int)ceil(($exp - $now) / 86400);
              echo date('Y-m-d', $exp);
              if ($dl < 0) echo ' <span class="badge warn">expired</span>';
              elseif ($dl <= 7) echo ' <span class="badge warn">' . $dl . 'd left</span>';
              else echo ' <span class="muted">' . $dl . 'd</span>';
            endif; ?>
          </td>
          <td><?= $isOnline ? '<span class="pill on">● online</span>' : ago($lp) ?></td>
          <td><div class="row-actions">
            <button type="button" class="ghost sm" onclick="copyText('<?= $t ?>', this)">Copy</button>
            <?php if (strpos((string)($r['label'] ?? ''), '@') !== false):
              $mt = 'mailto:' . rawurlencode($r['label'])
                  . '?subject=' . rawurlencode('Your Prometheus AI licence key')
                  . '&body=' . rawurlencode(customer_msg($r['token'])); ?>
              <a class="btn" href="<?= htmlspecialchars($mt) ?>"><button type="button" class="ghost sm">✉</button></a>
            <?php endif; ?>
            <?php if ($r['active']): ?>
              <form method="post" class="inline" onsubmit="return confirm('Revoke this key? The customer\'s app stops receiving signals and the strategy locks.')">
                <input type="hidden" name="action" value="revoke"><input type="hidden" name="token" value="<?= $t ?>">
                <button class="danger sm" type="submit">Revoke</button>
              </form>
            <?php else: ?>
              <form method="post" class="inline">
                <input type="hidden" name="action" value="activate"><input type="hidden" name="token" value="<?= $t ?>">
                <button class="ghost sm" type="submit">Activate</button>
              </form>
            <?php endif; ?>
            <form method="post" class="inline">
              <input type="hidden" name="action" value="extend"><input type="hidden" name="token" value="<?= $t ?>">
              <select name="days" class="sm"><option value="7">+7d</option><option value="30" selected>+30d</option><option value="90">+90d</option><option value="365">+1y</option></select>
              <button class="ghost sm" type="submit">Extend</button>
            </form>

            <details class="manage">
              <summary>⚙ more</summary>
              <div class="panel">
                <form method="post">
                  <input type="hidden" name="action" value="update"><input type="hidden" name="token" value="<?= $t ?>">
                  <div class="muted">Email</div>
                  <input type="email" name="label" value="<?= $email ?>" style="width:100%">
                  <div class="muted">Note</div>
                  <input type="text" name="note" value="<?= $note ?>" style="width:100%">
                  <button class="ghost sm" type="submit">Save details</button>
                </form>
                <form method="post">
                  <input type="hidden" name="action" value="set_expiry"><input type="hidden" name="token" value="<?= $t ?>">
                  <div class="muted">Set exact expiry (blank = lifetime)</div>
                  <input type="date" name="date" value="<?= $exp ? date('Y-m-d', $exp) : '' ?>">
                  <button class="ghost sm" type="submit">Set date</button>
                </form>
                <?php if ($share): ?><div class="muted">Seen from <?= $share['n'] ?> IP<?= $share['n']===1?'':'s' ?> (7d): <?= htmlspecialchars($share['ips']) ?></div><?php endif; ?>
                <div class="muted">Created <?= ago($r['created_at']) ?><?= ($r['last_ip'] ?? '') ? ' · last IP ' . htmlspecialchars($r['last_ip']) : '' ?></div>
                <div class="row-actions">
                  <form method="post" class="inline" onsubmit="return confirm('Rotate this key? A NEW key is issued and the current one stops working immediately. You must send the new key to the customer.')">
                    <input type="hidden" name="action" value="rotate"><input type="hidden" name="token" value="<?= $t ?>">
                    <button class="ghost sm" type="submit">🔁 Rotate key</button>
                  </form>
                  <form method="post" class="inline" onsubmit="return confirm('Delete this customer permanently? This cannot be undone (use Revoke to keep the record).')">
                    <input type="hidden" name="action" value="delete"><input type="hidden" name="token" value="<?= $t ?>">
                    <button class="danger sm" type="submit">🗑 Delete</button>
                  </form>
                </div>
              </div>
            </details>
          </div></td>
        </tr>
      <?php endforeach; ?>
      <?php if (!$view): ?>
        <tr><td colspan="7" style="color:#8a93a2;padding:20px">No keys match — <?= $q || $curf !== 'all' ? 'try clearing the search/filter' : 'generate one above' ?>.</td></tr>
      <?php endif; ?>
      </tbody>
    </table>
  </div>

  <div id="ipmodal" class="overlay" onclick="if(event.target===this)hideIps()">
    <div class="modal">
      <button type="button" class="modalx" onclick="hideIps()" title="Close">✕</button>
      <div id="ipmodal-body"></div>
    </div>
  </div>

<script>
function showIps(id) {
  const tpl = document.getElementById(id);
  if (!tpl) return;
  document.getElementById('ipmodal-body').innerHTML = tpl.innerHTML;
  document.getElementById('ipmodal').style.display = 'flex';
}
function hideIps() { document.getElementById('ipmodal').style.display = 'none'; }
document.addEventListener('keydown', e => { if (e.key === 'Escape') hideIps(); });

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
