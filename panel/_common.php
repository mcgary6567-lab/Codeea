<?php
// =====================================================================
//  Shared by every /panel page: session, the one login, the tab bar and
//  the stylesheet. index.php shows the login form; the other pages send
//  a visitor who is not logged in back to it.
// =====================================================================
ini_set('session.cookie_httponly', '1');
ini_set('session.use_strict_mode', '1');
if (!empty($_SERVER['HTTPS'])) ini_set('session.cookie_secure', '1');
header('X-Robots-Tag: noindex, nofollow');
header('X-Frame-Options: DENY');
session_start();
require __DIR__ . '/config.php';

$SITE_ROOT = dirname(__DIR__);           // public_html
$PRIVATE   = dirname($SITE_ROOT);        // above public_html: survives deploys, never served

if (empty($_SESSION['csrf'])) { $_SESSION['csrf'] = bin2hex(random_bytes(16)); }
function csrf_ok() { return isset($_POST['csrf']) && hash_equals($_SESSION['csrf'], (string)$_POST['csrf']); }
function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }

// Pages other than the login page call this first.
function panel_require_login() {
  if (empty($_SESSION['auth'])) { header('Location: /panel/index.php'); exit; }
  header('Cache-Control: no-store');
}

function panel_head($title) {
  echo '<!doctype html><html lang="en"><head><meta charset="utf-8">'
     . '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">'
     . '<title>' . h($title) . ' - Gold Scalpers panel</title><style>' . panel_css() . '</style></head><body><div class="wrap">';
}
function panel_foot() { echo '</div></body></html>'; }

function panel_tabs($active) {
  $tabs = array('licences' => array('Licences', '/panel/index.php'),
                'stats'    => array('Visitor stats', '/panel/stats.php'),
                'ai'       => array('AI settings', '/panel/ai.php'));
  echo '<div class="tabs">';
  foreach ($tabs as $k => $t)
    echo '<a href="' . $t[1] . '"' . ($k === $active ? ' class="cur"' : '') . '>' . h($t[0]) . '</a>';
  echo '<a class="out" href="/panel/index.php?logout">Log out</a></div>';
}

function panel_css() {
  return <<<CSS
  body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1216;color:#e8eaed;margin:0;padding:1.5rem}
  .wrap{max-width:1040px;margin:0 auto}
  .card{background:#171b21;border:1px solid #262c35;border-radius:12px;padding:1.3rem 1.4rem;margin-bottom:1.2rem}
  h1{color:#F59E0B;font-size:1.25rem;margin:0 0 1rem}h2{font-size:1rem;margin:0 0 .8rem;color:#e8eaed}
  label{display:block;font-size:.78rem;color:#9aa3ad;margin:.6rem 0 .25rem}
  input[type=text],input[type=password],input[type=email],input[type=search],select{width:100%;box-sizing:border-box;background:#0f1216;border:1px solid #2b323c;color:#fff;border-radius:8px;padding:.6rem .7rem;font-size:.95rem}
  .btn{background:#F59E0B;color:#111;border:0;border-radius:8px;padding:.6rem 1.1rem;font-weight:700;cursor:pointer;font-size:.9rem;margin-top:.6rem}
  .btn.sm{padding:.32rem .6rem;font-size:.78rem;margin-top:0}.btn.gray,.btn.alt{background:#2b323c;color:#e8eaed}.btn.red{background:#7f1d1d;color:#fff}.btn.ghost{background:transparent;border:1px solid #2b323c;color:#e8eaed}
  table{width:100%;border-collapse:collapse;font-size:.88rem;margin-top:.4rem}
  th,td{text-align:left;padding:.5rem .5rem;border-bottom:1px solid #262c35;vertical-align:middle;font-variant-numeric:tabular-nums}
  th{color:#9aa3ad;font-size:.68rem;text-transform:uppercase;letter-spacing:.04em}
  code{background:#0f1216;border:1px solid #2b323c;border-radius:5px;padding:.1rem .35rem;font-size:.85rem}
  .on{color:#34d399;font-weight:700}.off{color:#f87171;font-weight:700}
  .msg,.msg.ok{background:#064e3b;color:#a7f3d0;padding:.6rem .8rem;border-radius:8px;margin-bottom:1rem;font-size:.9rem}
  .err,.msg.bad{background:#7f1d1d;color:#fecaca;padding:.6rem .8rem;border-radius:8px;margin-bottom:1rem;font-size:.9rem}
  .row{display:flex;gap:.7rem;align-items:flex-end;flex-wrap:wrap}.row>div{flex:1;min-width:150px}
  .top{display:flex;justify-content:space-between;align-items:center}
  .toolbar{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-bottom:.3rem}
  .toolbar form{display:flex;gap:.5rem;flex:1;min-width:220px}
  .pager{display:flex;gap:.5rem;align-items:center;justify-content:center;margin-top:1rem;font-size:.85rem;color:#9aa3ad}
  .muted,.hint,.note{color:#9aa3ad;font-size:.8rem}a{color:#F59E0B}.src{font-size:.66rem;color:#9aa3ad;text-transform:uppercase}
  .tabs{display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:1.2rem;border-bottom:1px solid #262c35;padding-bottom:.6rem}
  .tabs a{color:#e8eaed;text-decoration:none;padding:.45rem .9rem;border-radius:8px;font-weight:600;font-size:.9rem;background:#171b21;border:1px solid #262c35}
  .tabs a.cur{background:#F59E0B;color:#111;border-color:#F59E0B}.tabs a.out{margin-left:auto;background:transparent;color:#9aa3ad}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.7rem}
  .k{background:#171b21;border:1px solid #262c35;border-radius:10px;padding:.8rem}
  .k b{display:block;font-size:1.45rem;color:#fff;font-variant-numeric:tabular-nums}.k span{font-size:.76rem;color:#9aa3ad}
  .scroll{overflow-x:auto}
  @media(max-width:620px){body{padding:.8rem}.card{padding:1rem .9rem;overflow-x:auto}h1{font-size:1.1rem}th,td{padding:.45rem .35rem}.btn.sm{padding:.3rem .45rem;font-size:.72rem}.row>div{min-width:120px}.tabs a.out{margin-left:0}}
CSS;
}
