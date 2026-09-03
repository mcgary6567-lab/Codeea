<?php
/**
 * Settings: integration credentials and engine locks, editable from the panel.
 *
 * Values saved here override the config file (see gs_apply_overrides in
 * lib/bootstrap.php). The MetaApi token is encrypted with app_key before it is
 * stored and is never echoed back — only its last four characters are shown.
 * Owner role only; every save is audited without the secret.
 */
require_once __DIR__ . '/_boot.php';
require_once __DIR__ . '/../lib/metaapi.php';
$me = require_admin('owner');

function set_mask(string $s): string
{
    if ($s === '' || $s === 'CHANGEME') return 'not set';
    return 'set · ends with ' . substr($s, -4);
}
function set_is_https(string $u): bool
{
    return (bool)filter_var($u, FILTER_VALIDATE_URL) && stripos($u, 'https://') === 0;
}

/* ---------------- actions ---------------- */
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    csrf_check();
    $action = (string)($_POST['action'] ?? '');

    if ($action === 'save') {
        $stored = gs_overrides_raw();
        $errors = [];

        /* --- MetaApi ------------------------------------------------ */
        $m = is_array($stored['metaapi'] ?? null) ? $stored['metaapi'] : [];
        $token = trim((string)($_POST['metaapi_token'] ?? ''));
        if (!empty($_POST['clear_token'])) {
            unset($m['token_enc']);
        } elseif ($token !== '') {
            if (strlen($token) < 20 || preg_match('/\s/', $token)) {
                $errors[] = 'That does not look like a MetaApi token (too short or contains spaces).';
            } else {
                $m['token_enc'] = gs_encrypt($token);
            }
        }
        $region = substr(trim((string)($_POST['metaapi_region'] ?? '')), 0, 40);
        $base   = rtrim(trim((string)($_POST['metaapi_base'] ?? '')), '/');
        $prov   = rtrim(trim((string)($_POST['metaapi_prov'] ?? '')), '/');
        if ($region === '' || !preg_match('/^[a-z0-9-]+$/', $region)) $errors[] = 'Region must be a MetaApi region id such as new-york or london.';
        if (!set_is_https($base)) $errors[] = 'Client API base must be an https:// URL.';
        if (!set_is_https($prov)) $errors[] = 'Provisioning API base must be an https:// URL.';
        $m['region']  = $region;
        $m['base']    = substr($base, 0, 200);
        $m['prov']    = substr($prov, 0, 200);
        $m['enabled'] = !empty($_POST['metaapi_enabled']);

        // Refuse to switch execution on with no usable token anywhere.
        $fileTok = (string)(gs_config()['metaapi']['token'] ?? '');
        $hasTok  = isset($m['token_enc']) || ($fileTok !== '' && $fileTok !== 'CHANGEME');
        if ($m['enabled'] && !$hasTok) $errors[] = 'Execution cannot be enabled without a MetaApi token.';

        /* --- Engine locks ------------------------------------------- */
        $e = is_array($stored['engine'] ?? null) ? $stored['engine'] : [];
        $e['allow_live'] = !empty($_POST['allow_live']);
        $lot = (float)($_POST['max_lot_per_trade'] ?? 0.5);
        if (!is_numeric($_POST['max_lot_per_trade'] ?? null) || $lot < 0.01 || $lot > 2.0) {
            $errors[] = 'Max lot per trade must be between 0.01 and 2.00.';
        }
        $e['max_lot_per_trade'] = round($lot, 2);

        /* --- Push (FCM) --------------------------------------------- */
        $f = is_array($stored['fcm'] ?? null) ? $stored['fcm'] : [];
        $f['project_id']      = substr(trim((string)($_POST['fcm_project_id'] ?? '')), 0, 64);
        $f['service_account'] = substr(trim((string)($_POST['fcm_service_account'] ?? '')), 0, 255);
        $f['enabled']         = !empty($_POST['fcm_enabled']);
        if ($f['enabled'] && ($f['project_id'] === '' || !is_readable($f['service_account']))) {
            $errors[] = 'Push cannot be enabled until the project id is set and the service-account JSON path is readable on the server.';
        }

        if ($errors) {
            flash(implode(' ', $errors), 'err');
            header('Location: settings.php'); exit;
        }

        $doc = ['metaapi' => $m, 'engine' => $e, 'fcm' => $f];
        q('INSERT INTO engine_state (k, v) VALUES (?,?)
           ON DUPLICATE KEY UPDATE v = VALUES(v)',
          [GS_OVERRIDES_KEY, json_encode($doc, JSON_UNESCAPED_SLASHES)]);

        $safe = $doc;
        $safe['metaapi']['token_enc'] = isset($m['token_enc']) ? '[set]' : '[none]';
        gs_audit('admin', (int)$me['id'], 'settings_saved', [
            'token_changed' => $token !== '' || !empty($_POST['clear_token']),
            'values'        => $safe,
        ]);
        flash('Settings saved. The engine picks them up on its next tick.', 'ok');
        header('Location: settings.php'); exit;
    }

    if ($action === 'test_metaapi') {
        $cfg = ma_cfg();
        $tok = (string)($cfg['token'] ?? '');
        if ($tok === '' || $tok === 'CHANGEME') {
            flash('No MetaApi token is in effect — save one first.', 'err');
        } else {
            [$code, $body, $err] = ma_request('GET', ma_prov_url('/users/current/accounts'), null, 15);
            if ($code === 200 && is_array($body)) {
                flash('MetaApi reachable. Token accepted; ' . count($body) . ' account(s) on that MetaApi user.', 'ok');
            } elseif ($code === 401 || $code === 403) {
                flash('MetaApi rejected the token (HTTP ' . $code . ').', 'err');
            } else {
                flash('MetaApi test failed: HTTP ' . $code . ' ' . ($err ?: ma_reason($body, 'no detail')), 'err');
            }
        }
        gs_audit('admin', (int)$me['id'], 'settings_test_metaapi');
        header('Location: settings.php'); exit;
    }

    if ($action === 'reset') {
        q('DELETE FROM engine_state WHERE k = ?', [GS_OVERRIDES_KEY]);
        gs_audit('admin', (int)$me['id'], 'settings_reset_to_file');
        flash('Panel overrides removed. Values from the config file are back in effect.', 'ok');
        header('Location: settings.php'); exit;
    }
}

/* ---------------- render ---------------- */
$cfg   = gs_config();
$ov    = $cfg['_overrides'] ?? [];
$ma    = $cfg['metaapi'] ?? [];
$eng   = $cfg['engine']  ?? [];
$fcm   = $cfg['fcm']     ?? [];
$src   = static fn(string $key) => in_array($key, $ov, true)
            ? '<span class="pill ok">panel</span>' : '<span class="pill dim">file</span>';
$tokOk = ($ma['token'] ?? '') !== '' && ($ma['token'] ?? '') !== 'CHANGEME';

layout_head('Settings');
?>
<h1>Settings</h1>
<p class="sub">Integration credentials and the hard engine locks. Saved values override
   <code><?= h(basename((string)($cfg['_config_path'] ?? 'config'))) ?></code>; anything left blank keeps the file value.</p>

<form method="post">
<input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
<input type="hidden" name="action" value="save">

<div class="panel">
  <h2>MetaApi (execution)</h2>
  <p style="color:var(--muted);font-size:.9rem;margin-bottom:.9rem">
    Token from <strong>app.metaapi.cloud → API access</strong>. Stored encrypted with
    <code>app_key</code>; rotating that key invalidates it. Current token:
    <strong><?= h(set_mask((string)($ma['token'] ?? ''))) ?></strong> <?= $src('metaapi.token') ?>
  </p>
  <div class="row">
    <div class="field" style="flex:2 1 320px"><label>New API token</label>
      <input type="password" name="metaapi_token" autocomplete="off"
             placeholder="<?= $tokOk ? 'leave blank to keep the current token' : 'paste your MetaApi token' ?>"></div>
    <div class="check" style="align-self:flex-end;margin-bottom:.9rem">
      <input type="checkbox" id="ct" name="clear_token"><label for="ct">Forget the saved token</label></div>
  </div>
  <div class="row">
    <div class="field"><label>Region <?= $src('metaapi.region') ?></label>
      <input type="text" name="metaapi_region" value="<?= h((string)($ma['region'] ?? 'new-york')) ?>"></div>
    <div class="field" style="flex:2 1 300px"><label>Client API base <?= $src('metaapi.base') ?></label>
      <input type="text" name="metaapi_base" value="<?= h((string)($ma['base'] ?? 'https://mt-client-api-v1.new-york.agiliumtrade.ai')) ?>"></div>
    <div class="field" style="flex:2 1 300px"><label>Provisioning API base <?= $src('metaapi.prov') ?></label>
      <input type="text" name="metaapi_prov" value="<?= h((string)($ma['prov'] ?? 'https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai')) ?>"></div>
  </div>
  <div class="check">
    <input type="checkbox" id="me" name="metaapi_enabled" <?= !empty($ma['enabled']) ? 'checked' : '' ?>>
    <label for="me"><strong>Execution enabled</strong> — real order flow to MetaApi
      <?= $src('metaapi.enabled') ?>. Off = the engine records signals but never sends an order.</label>
  </div>
  <p class="note">The region must match where the client API base lives. Change one, change both.</p>
</div>

<div class="panel">
  <h2>Engine locks</h2>
  <div class="check">
    <input type="checkbox" id="al" name="allow_live" <?= !empty($eng['allow_live']) ? 'checked' : '' ?>>
    <label for="al"><strong>Allow live (non-demo) accounts</strong> <?= $src('engine.allow_live') ?>.
      Even when on, each live account still needs its own approval under Accounts.</label>
  </div>
  <div class="row">
    <div class="field"><label>Max lot per trade (hard ceiling) <?= $src('engine.max_lot_per_trade') ?></label>
      <input type="number" step="0.01" min="0.01" max="2" name="max_lot_per_trade"
             value="<?= h((string)($eng['max_lot_per_trade'] ?? 0.5)) ?>"></div>
  </div>
  <p class="note">Applied after every other sizing rule. Nothing in the strategy config can exceed it.</p>
</div>

<div class="panel">
  <h2>Push notifications (Firebase)</h2>
  <div class="row">
    <div class="field"><label>Project id <?= $src('fcm.project_id') ?></label>
      <input type="text" name="fcm_project_id" value="<?= h((string)($fcm['project_id'] ?? '')) ?>"></div>
    <div class="field" style="flex:2 1 320px"><label>Service-account JSON path on the server <?= $src('fcm.service_account') ?></label>
      <input type="text" name="fcm_service_account" value="<?= h((string)($fcm['service_account'] ?? '')) ?>"
             placeholder="/home/u218044176/domains/app.goldscalpers.com/fcm-service-account.json"></div>
  </div>
  <div class="check">
    <input type="checkbox" id="fe" name="fcm_enabled" <?= !empty($fcm['enabled']) ? 'checked' : '' ?>>
    <label for="fe">Push enabled <?= $src('fcm.enabled') ?></label>
  </div>
  <p class="note">Upload the JSON key <em>beside</em> <code>public_html</code>, never inside it.</p>
</div>

<button class="btn">Save settings</button>
</form>

<div class="panel" style="margin-top:1.2rem">
  <h2>Check</h2>
  <div class="row">
    <form method="post">
      <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
      <input type="hidden" name="action" value="test_metaapi">
      <button class="btn ghost" <?= $tokOk ? '' : 'disabled' ?>>Test saved MetaApi connection</button>
    </form>
    <form method="post" onsubmit="return confirm('Drop every value saved here and go back to the config file?')">
      <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
      <input type="hidden" name="action" value="reset">
      <button class="btn ghost" <?= $ov ? '' : 'disabled' ?>>Reset to file values</button>
    </form>
  </div>
  <p class="note">The test lists the accounts on your MetaApi user with the token currently in effect.
     It places nothing and changes nothing.</p>
</div>

<div class="panel">
  <h2>In effect right now</h2>
  <div class="tw"><table>
    <thead><tr><th>Setting</th><th>Value</th><th>Source</th></tr></thead>
    <tbody>
      <tr><td>metaapi.token</td><td><?= h(set_mask((string)($ma['token'] ?? ''))) ?></td><td><?= $src('metaapi.token') ?></td></tr>
      <tr><td>metaapi.enabled</td><td><span class="pill <?= !empty($ma['enabled']) ? 'no' : 'dim' ?>"><?= !empty($ma['enabled']) ? 'ON' : 'OFF' ?></span></td><td><?= $src('metaapi.enabled') ?></td></tr>
      <tr><td>metaapi.region</td><td><?= h((string)($ma['region'] ?? '')) ?></td><td><?= $src('metaapi.region') ?></td></tr>
      <tr><td>engine.allow_live</td><td><span class="pill <?= !empty($eng['allow_live']) ? 'no' : 'dim' ?>"><?= !empty($eng['allow_live']) ? 'ON' : 'OFF' ?></span></td><td><?= $src('engine.allow_live') ?></td></tr>
      <tr><td>engine.max_lot_per_trade</td><td><?= h((string)($eng['max_lot_per_trade'] ?? '')) ?></td><td><?= $src('engine.max_lot_per_trade') ?></td></tr>
      <tr><td>fcm.enabled</td><td><span class="pill <?= !empty($fcm['enabled']) ? 'ok' : 'dim' ?>"><?= !empty($fcm['enabled']) ? 'ON' : 'OFF' ?></span></td><td><?= $src('fcm.enabled') ?></td></tr>
    </tbody>
  </table></div>
</div>
<?php layout_foot();
