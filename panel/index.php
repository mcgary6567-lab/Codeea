<?php
// =====================================================================
//  Gold Scalpers - private licence panel (pro)
//  Add / disable / remove / reset MT5 accounts in licenses.txt, with
//  search, pagination (10/page) and name/email/date-time metadata.
//  PII (name/email) is stored in a private .accounts.json, NOT in the
//  public licenses.txt. Protect this folder with a password (config.php)
//  + Hostinger "Password Protect Directories".
// =====================================================================
ini_set('session.cookie_httponly', '1');
ini_set('session.use_strict_mode', '1');
if (!empty($_SERVER['HTTPS'])) ini_set('session.cookie_secure', '1');
session_start();
require __DIR__ . '/config.php';

if (empty($_SESSION['csrf'])) { $_SESSION['csrf'] = bin2hex(random_bytes(16)); }
function csrf_ok() { return isset($_POST['csrf']) && hash_equals($_SESSION['csrf'], (string)$_POST['csrf']); }
function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function fmt_dt($iso) { if (!$iso) return '—'; $t = strtotime($iso); return $t ? date('Y-m-d H:i', $t) : h($iso); }
function flag_emoji($cc) {
  $cc = strtoupper(trim($cc));
  if (strlen($cc) !== 2 || !ctype_alpha($cc)) return '';
  $f = '';
  foreach (array(ord($cc[0]), ord($cc[1])) as $o) { $f .= function_exists('mb_chr') ? mb_chr(0x1F1E6 + ($o - 65), 'UTF-8') : ''; }
  return $f;
}

// ---------- data layer ----------
function load_entries($file) {
  $out = array();
  if (!is_file($file)) return $out;
  foreach (preg_split('/\r\n|\r|\n/', (string)file_get_contents($file)) as $line) {
    $t = trim($line);
    if ($t === '') continue;
    if (preg_match('/^(#\s*)?(\d{3,12})\b\s*(?:#\s*(.*))?$/', $t, $m)) {
      $out[] = array('n' => $m[2], 'label' => trim($m[3] ?? ''), 'on' => (($m[1] ?? '') === ''));
    }
  }
  return $out;
}
function save_entries($file, $entries) {
  $o  = "# Gold Scalpers EA - Licence File (managed via /panel)\n";
  $o .= "# -----------------------------------------------\n";
  $o .= "# Listed accounts can run the EA.  '#' prefix = disabled.\n";
  $o .= "# -----------------------------------------------\n\n";
  foreach ($entries as $e) {
    $label = $e['label'] !== '' ? '   # ' . $e['label'] : '';
    $o .= ($e['on'] ? '' : '# ') . $e['n'] . $label . "\n";
  }
  return @file_put_contents($file, $o, LOCK_EX) !== false;
}
function load_meta($file) { if (!is_file($file)) return array(); $j = json_decode((string)file_get_contents($file), true); return is_array($j) ? $j : array(); }
function save_meta($file, $meta) { return @file_put_contents($file, json_encode($meta, JSON_PRETTY_PRINT), LOCK_EX) !== false; }

// ---------- logout ----------
if (isset($_GET['logout'])) { $_SESSION = array(); session_destroy(); header('Location: index.php'); exit; }

// ---------- auth ----------
$loginError = '';
if (empty($_SESSION['auth'])) {
  if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['password'])) {
    if ($PANEL_PASSWORD === 'CHANGE-ME-NOW') { $loginError = 'Set a password in panel/config.php first.'; }
    elseif (hash_equals((string)$PANEL_PASSWORD, (string)$_POST['password'])) {
      session_regenerate_id(true); $_SESSION['auth'] = true; $_SESSION['csrf'] = bin2hex(random_bytes(16));
      header('Location: index.php'); exit;
    } else { $loginError = 'Wrong password.'; }
  }
}
$authed = !empty($_SESSION['auth']);

$msg = ''; $err = ''; $q = ''; $page = 1; $pages = 1; $total = 0; $active = 0; $slice = array();

if ($authed) {
  $entries = load_entries($LICENSE_FILE);
  $meta    = load_meta($META_FILE);

  // ---------- actions ----------
  if ($_SERVER['REQUEST_METHOD'] === 'POST' && ($_POST['action'] ?? '') !== '') {
    if (!csrf_ok()) { $err = 'Session expired - try again.'; }
    else {
      $action = $_POST['action'];
      $num = preg_replace('/\D/', '', (string)($_POST['number'] ?? ''));
      if ($action === 'add') {
        if (!preg_match('/^\d{3,12}$/', $num)) { $err = 'Enter a valid account number (digits only).'; }
        else {
          $name  = trim(mb_substr(preg_replace('/[\r\n\t]/', ' ', (string)($_POST['name'] ?? '')), 0, 60));
          $email = trim(mb_substr(preg_replace('/[\r\n\t]/', ' ', (string)($_POST['email'] ?? '')), 0, 80));
          $found = false;
          foreach ($entries as &$e) { if ($e['n'] === $num) { $e['on'] = true; $found = true; } }
          unset($e);
          if (!$found) $entries[] = array('n' => $num, 'label' => 'panel ' . date('Y-m-d'), 'on' => true);
          $meta[$num] = array('name' => $name, 'email' => $email, 'added' => ($meta[$num]['added'] ?? date('c')), 'source' => ($meta[$num]['source'] ?? 'panel'));
          $msg = $found ? "Account $num enabled / updated." : "Account $num added.";
        }
      } elseif ($action === 'toggle') {
        foreach ($entries as &$e) { if ($e['n'] === $num) { $e['on'] = !$e['on']; $msg = "Account $num " . ($e['on'] ? 'enabled' : 'disabled') . "."; } }
        unset($e);
      } elseif ($action === 'remove') {
        $entries = array_values(array_filter($entries, function($e) use ($num) { return $e['n'] !== $num; }));
        unset($meta[$num]);
        $msg = "Account $num removed.";
      } elseif ($action === 'reset') {
        $entries = array(); $meta = array();
        $msg = "All accounts cleared. The licence list is now empty.";
      }
      if ($err === '') {
        $ok1 = save_entries($LICENSE_FILE, $entries);
        $ok2 = save_meta($META_FILE, $meta);
        if (!$ok1) $err = 'Could not write licenses.txt - check it is writable (chmod 644/664).';
        elseif (!$ok2) $err = 'Accounts saved, but could not write metadata (.accounts.json).';
      }
    }
    $entries = load_entries($LICENSE_FILE);
    $meta    = load_meta($META_FILE);
  }

  // ---------- merge + search + paginate ----------
  $rows = array();
  foreach ($entries as $e) {
    $m = $meta[$e['n']] ?? array();
    $added = $m['added'] ?? '';
    if ($added === '' && preg_match('/\d{4}-\d{2}-\d{2}/', (string)$e['label'], $dm)) $added = $dm[0]; // backfill date from comment
    $rows[] = array(
      'n' => $e['n'], 'on' => $e['on'],
      'name'  => ($m['name']  ?? $e['label']),
      'email' => ($m['email'] ?? ''),
      'added' => $added,
      'source'=> ($m['source']?? ''),
      'ip'    => ($m['ip']    ?? ''),
      'cc'    => ($m['cc']    ?? ''),
    );
  }
  $active = count(array_filter($entries, function($e){ return $e['on']; }));
  $q = trim((string)($_REQUEST['q'] ?? ''));
  if ($q !== '') {
    $ql = mb_strtolower($q);
    $rows = array_values(array_filter($rows, function($r) use ($ql) {
      return strpos(mb_strtolower($r['n'] . ' ' . $r['name'] . ' ' . $r['email'] . ' ' . $r['cc'] . ' ' . $r['ip']), $ql) !== false;
    }));
  }
  $total = count($rows);
  $perPage = 10;
  $pages = max(1, (int)ceil($total / $perPage));
  $page = min($pages, max(1, (int)($_REQUEST['page'] ?? 1)));
  $slice = array_slice($rows, ($page - 1) * $perPage, $perPage);
}
$csrf = $_SESSION['csrf'] ?? '';
?><!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Licence Panel - Gold Scalpers</title>
<style>
  body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1216;color:#e8eaed;margin:0;padding:1.5rem}
  .wrap{max-width:960px;margin:0 auto}
  .card{background:#171b21;border:1px solid #262c35;border-radius:12px;padding:1.3rem 1.4rem;margin-bottom:1.2rem}
  h1{color:#F59E0B;font-size:1.25rem;margin:0 0 1rem}h2{font-size:1rem;margin:0 0 .8rem;color:#e8eaed}
  label{display:block;font-size:.78rem;color:#9aa3ad;margin:.6rem 0 .25rem}
  input[type=text],input[type=password],input[type=email],input[type=search]{width:100%;box-sizing:border-box;background:#0f1216;border:1px solid #2b323c;color:#fff;border-radius:8px;padding:.6rem .7rem;font-size:.95rem}
  .btn{background:#F59E0B;color:#111;border:0;border-radius:8px;padding:.6rem 1.1rem;font-weight:700;cursor:pointer;font-size:.9rem}
  .btn.sm{padding:.32rem .6rem;font-size:.78rem}.btn.gray{background:#2b323c;color:#e8eaed}.btn.red{background:#7f1d1d;color:#fff}.btn.ghost{background:transparent;border:1px solid #2b323c;color:#e8eaed}
  table{width:100%;border-collapse:collapse;font-size:.88rem;margin-top:.4rem}
  th,td{text-align:left;padding:.5rem .5rem;border-bottom:1px solid #262c35;vertical-align:middle}
  th{color:#9aa3ad;font-size:.68rem;text-transform:uppercase;letter-spacing:.04em}
  code{background:#0f1216;border:1px solid #2b323c;border-radius:5px;padding:.1rem .35rem;font-size:.85rem}
  .on{color:#34d399;font-weight:700}.off{color:#f87171;font-weight:700}
  .msg{background:#064e3b;color:#a7f3d0;padding:.6rem .8rem;border-radius:8px;margin-bottom:1rem;font-size:.9rem}
  .err{background:#7f1d1d;color:#fecaca;padding:.6rem .8rem;border-radius:8px;margin-bottom:1rem;font-size:.9rem}
  .row{display:flex;gap:.7rem;align-items:flex-end;flex-wrap:wrap}.row>div{flex:1;min-width:150px}
  .top{display:flex;justify-content:space-between;align-items:center}
  .toolbar{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-bottom:.3rem}
  .toolbar form{display:flex;gap:.5rem;flex:1;min-width:220px}
  .pager{display:flex;gap:.5rem;align-items:center;justify-content:center;margin-top:1rem;font-size:.85rem;color:#9aa3ad}
  .muted{color:#9aa3ad;font-size:.8rem}a{color:#F59E0B}.src{font-size:.66rem;color:#9aa3ad;text-transform:uppercase}
  @media(max-width:620px){body{padding:.8rem}.card{padding:1rem .9rem;overflow-x:auto}h1{font-size:1.1rem}th,td{padding:.45rem .35rem}.btn.sm{padding:.3rem .45rem;font-size:.72rem}.row>div{min-width:120px}}
</style></head><body><div class="wrap">

<?php if (!$authed): ?>
  <div class="card" style="max-width:380px;margin:3rem auto">
    <h1>Licence Panel</h1>
    <?php if ($loginError): ?><p class="err"><?= h($loginError) ?></p><?php endif; ?>
    <form method="post">
      <label>Password</label>
      <input type="password" name="password" autofocus required>
      <div style="margin-top:.9rem"><button class="btn" type="submit">Log in</button></div>
    </form>
  </div>

<?php else: ?>
  <div class="card"><div class="top"><h1 style="margin:0">Licence Panel</h1><a class="muted" href="?logout">Log out</a></div>
    <p class="muted"><?= $active ?> active &middot; <?= $total ?> total. Changes reach the EA within ~2 min.</p>
    <p class="muted" style="font-size:.78rem">Name, email, country &amp; date are captured for accounts added here or via the thank-you form. Accounts added earlier show &mdash; (no data was stored for them).</p>
    <?php $metaWritable = is_file($META_FILE) ? is_writable($META_FILE) : @is_writable(dirname($META_FILE)); if (!$metaWritable): ?><p class="err">Metadata file <code>.accounts.json</code> isn&rsquo;t writable &mdash; name/email/date can&rsquo;t be saved. In Hostinger File Manager, set the site root (or an empty <code>.accounts.json</code>) to permission <strong>664</strong>.</p><?php endif; ?>
    <?php if ($msg): ?><p class="msg"><?= h($msg) ?></p><?php endif; ?>
    <?php if ($err): ?><p class="err"><?= h($err) ?></p><?php endif; ?>
    <?php if ($PANEL_PASSWORD === 'CHANGE-ME-NOW'): ?><p class="err">Default password still set in config.php - change it.</p><?php endif; ?>
  </div>

  <div class="card"><h2>Add / activate an account</h2>
    <form method="post">
      <input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="action" value="add">
      <input type="hidden" name="q" value="<?= h($q) ?>">
      <div class="row">
        <div><label>MT5 account number *</label><input type="text" name="number" inputmode="numeric" pattern="\d*" placeholder="e.g. 250481917" required></div>
        <div><label>Username / name</label><input type="text" name="name" placeholder="e.g. John D."></div>
        <div><label>Email</label><input type="email" name="email" placeholder="john@example.com"></div>
        <div style="flex:0"><label>&nbsp;</label><button class="btn" type="submit">Add</button></div>
      </div>
    </form>
  </div>

  <div class="card">
    <div class="toolbar">
      <form method="get">
        <input type="search" name="q" value="<?= h($q) ?>" placeholder="Search account, name or email...">
        <button class="btn gray sm" type="submit">Search</button>
        <?php if ($q !== ''): ?><a class="btn ghost sm" href="index.php" style="text-decoration:none">Clear</a><?php endif; ?>
      </form>
    </div>

    <?php if (!$slice): ?>
      <p class="muted"><?= $q !== '' ? 'No accounts match "' . h($q) . '".' : 'No accounts yet. Add one above.' ?></p>
    <?php else: ?>
      <table>
        <tr><th>Account</th><th>Name</th><th>Email</th><th>Added</th><th>Location</th><th>Status</th><th style="text-align:right">Actions</th></tr>
        <?php foreach ($slice as $r): ?>
          <tr>
            <td><code><?= h($r['n']) ?></code><?php if ($r['source']): ?><br><span class="src"><?= h($r['source']) ?></span><?php endif; ?></td>
            <td><?= $r['name'] !== '' ? h($r['name']) : '—' ?></td>
            <td><?= $r['email'] !== '' ? h($r['email']) : '—' ?></td>
            <td><?= fmt_dt($r['added']) ?></td>
            <td><?php if ($r['cc'] !== '' || $r['ip'] !== ''): ?><?php if ($r['cc'] !== ''): ?><img src="https://flagcdn.com/20x15/<?= strtolower(preg_replace('/[^a-zA-Z]/', '', $r['cc'])) ?>.png" width="20" height="15" alt="<?= h($r['cc']) ?>" style="vertical-align:middle;border-radius:2px"> <?= h($r['cc']) ?><?php endif; ?><?php if ($r['ip'] !== ''): ?><br><span class="muted" style="font-size:.7rem"><?= h($r['ip']) ?></span><?php endif; ?><?php else: ?>—<?php endif; ?></td>
            <td><?= $r['on'] ? '<span class="on">Active</span>' : '<span class="off">Disabled</span>' ?></td>
            <td style="text-align:right;white-space:nowrap">
              <form method="post" style="display:inline"><input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="q" value="<?= h($q) ?>"><input type="hidden" name="page" value="<?= $page ?>"><input type="hidden" name="number" value="<?= h($r['n']) ?>"><input type="hidden" name="action" value="toggle"><button class="btn sm gray" type="submit"><?= $r['on'] ? 'Disable' : 'Enable' ?></button></form>
              <form method="post" style="display:inline" onsubmit="return confirm('Remove account <?= h($r['n']) ?> permanently?')"><input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="q" value="<?= h($q) ?>"><input type="hidden" name="page" value="<?= $page ?>"><input type="hidden" name="number" value="<?= h($r['n']) ?>"><input type="hidden" name="action" value="remove"><button class="btn sm red" type="submit">Remove</button></form>
            </td>
          </tr>
        <?php endforeach; ?>
      </table>

      <?php if ($pages > 1): ?>
        <div class="pager">
          <?php if ($page > 1): ?><a class="btn ghost sm" href="?q=<?= urlencode($q) ?>&page=<?= $page - 1 ?>" style="text-decoration:none">&larr; Prev</a><?php endif; ?>
          <span>Page <?= $page ?> of <?= $pages ?></span>
          <?php if ($page < $pages): ?><a class="btn ghost sm" href="?q=<?= urlencode($q) ?>&page=<?= $page + 1 ?>" style="text-decoration:none">Next &rarr;</a><?php endif; ?>
        </div>
      <?php endif; ?>
    <?php endif; ?>
    <p class="muted" style="margin-top:1rem">Live file: <a href="/licenses.txt" target="_blank" rel="noopener">/licenses.txt</a></p>
  </div>

  <div class="card" style="border-color:#7f1d1d">
    <h2 style="color:#f87171">Danger zone</h2>
    <p class="muted">Reset removes <strong>every</strong> authorized account from the licence list. The EA will block all accounts (except demos) until you add them again. Cannot be undone.</p>
    <form method="post" onsubmit="return confirm('Remove ALL <?= $total ?> accounts and reset the licence list? This cannot be undone.')">
      <input type="hidden" name="csrf" value="<?= h($csrf) ?>"><input type="hidden" name="action" value="reset">
      <button class="btn red" type="submit">Reset licence list</button>
    </form>
  </div>

<?php endif; ?>
</div></body></html>
