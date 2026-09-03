<?php
/**
 * Remote control: the global strategy config, the kill switch, and commands
 * pushed down to every app install.
 *
 * The app has no authority of its own — it reads what this page writes.
 */
require_once __DIR__ . '/_boot.php';
require_once __DIR__ . '/../lib/push.php';
$me = require_admin('support');

$global = gs_cfg_row('global', '');

/** Push to every install that has a token. No-op until Firebase is configured. */
function push_everyone(string $title, string $body, array $data = []): int
{
    if (!gs_push_enabled()) return 0;
    $sent = 0;
    foreach (qall('SELECT DISTINCT user_id FROM devices WHERE fcm_token IS NOT NULL') as $d) {
        $sent += gs_push_user((int)$d['user_id'], $title, $body, $data);
    }
    return $sent;
}

/* ---------------- actions ---------------- */
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    csrf_check();
    $action = (string)($_POST['action'] ?? '');

    /* --- kill switch: stop the world ------------------------------ */
    if ($action === 'kill_all') {
        require_admin('owner');
        gs_save_config('global', '', ['trading_enabled' => false], (int)$me['id']);
        q('UPDATE broker_accounts SET halted = 1, halt_reason = "admin_kill_switch"
            WHERE status = "connected"');
        q('INSERT INTO app_commands (target_user, type, payload, created_by)
           VALUES (NULL, "kill", ?, ?)',
          [json_encode(['reason' => 'Trading stopped by operations.']), (int)$me['id']]);
        gs_audit('admin', (int)$me['id'], 'KILL_SWITCH', 'all accounts halted');
        $n = push_everyone('Trading stopped', 'Trading stopped by operations.', ['type' => 'kill']);
        flash('Kill switch engaged. Every account is halted and trading is off.'
              . ($n ? " Push sent to $n device(s)." : ''), 'ok');
        header('Location: control.php'); exit;
    }

    /* --- resume --------------------------------------------------- */
    if ($action === 'resume_all') {
        require_admin('owner');
        q('UPDATE broker_accounts SET halted = 0, halt_reason = \'\'
            WHERE halt_reason = "admin_kill_switch"');
        q('INSERT INTO app_commands (target_user, type, payload, created_by)
           VALUES (NULL, "resume", ?, ?)',
          [json_encode(['reason' => 'Trading resumed.']), (int)$me['id']]);
        gs_audit('admin', (int)$me['id'], 'kill_switch_released');
        flash('Halts cleared. Trading stays OFF until you enable it below.', 'ok');
        header('Location: control.php'); exit;
    }

    /* --- global config save --------------------------------------- */
    if ($action === 'save_config') {
        $bools = [
            'trading_enabled','allow_buy','allow_sell','use_macd_filter',
            'only_with_trend_ema','use_chop_filter','use_scale_in','use_step_stop',
            'use_break_even','use_loss_cooldown','only_trade_sessions',
            'sess_sydney','sess_tokyo','sess_london','sess_newyork',
            'no_overnight','news_filter','prop_firm_mode','prop_trailing_dd',
        ];
        $nums = [
            'slope_period','slope_method','slope_price','ema_period','macd_fast',
            'macd_slow','macd_signal','confirm_candles','min_body_ratio',
            'min_ema_gap_boxes','fixed_lot','trades_per_signal','additional_trades',
            'pips_interval','addon_lot','swing_sl_buffer_pips','take_profit_pips',
            'step_stop_distance_pips','break_even_pips','break_even_lock_pips',
            'slippage_pips','daily_profit_target','daily_loss_limit',
            'max_drawdown_pct','max_trades_per_day','max_open_positions',
            'max_spread_pips','prop_daily_loss_pct','prop_max_drawdown_pct',
            'cooldown_loss_streak','cooldown_minutes',
        ];
        $patch = [];
        foreach ($bools as $k) $patch[$k] = !empty($_POST[$k]);
        foreach ($nums as $k) {
            if (isset($_POST[$k]) && is_numeric($_POST[$k])) $patch[$k] = (float)$_POST[$k];
        }
        foreach (['symbol','timeframe','entry_mode','flat_time'] as $k) {
            if (isset($_POST[$k])) $patch[$k] = substr(trim((string)$_POST[$k]), 0, 24);
        }

        // Enabling trading globally is an owner-level act.
        if (!empty($patch['trading_enabled']) && empty($global['trading_enabled'])) {
            require_admin('owner');
        }

        gs_save_config('global', '', $patch, (int)$me['id']);
        q('INSERT INTO app_commands (target_user, type, payload, created_by)
           VALUES (NULL, "reload_config", NULL, ?)', [(int)$me['id']]);
        gs_audit('admin', (int)$me['id'], 'config_saved', $patch);
        flash('Global config saved and pushed to all apps.', 'ok');
        header('Location: control.php'); exit;
    }

    /* --- broadcast ------------------------------------------------- */
    if ($action === 'broadcast') {
        $msg = trim((string)($_POST['message'] ?? ''));
        if ($msg === '') { flash('Message was empty.', 'err'); header('Location: control.php'); exit; }
        q('INSERT INTO app_commands (target_user, type, payload, created_by, expires_at)
           VALUES (NULL, "message", ?, ?, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 7 DAY))',
          [json_encode(['title' => substr((string)($_POST['title'] ?? 'Notice'), 0, 80),
                        'body'  => substr($msg, 0, 500)]), (int)$me['id']]);
        gs_audit('admin', (int)$me['id'], 'broadcast', substr($msg, 0, 200));
        $n = push_everyone(substr((string)($_POST['title'] ?? 'Notice'), 0, 80),
                           substr($msg, 0, 500), ['type' => 'message']);
        flash('Broadcast queued for every app install.'
              . ($n ? " Push sent to $n device(s)." : ''), 'ok');
        header('Location: control.php'); exit;
    }
}

$g = gs_cfg_row('global', '');
$num = static fn(string $k, $d = 0) => h((string)($g[$k] ?? $d));
$chk = static fn(string $k) => !empty($g[$k]) ? 'checked' : '';

$recentCmds = qall('SELECT c.*, a.email FROM app_commands c
                    LEFT JOIN admins a ON a.id = c.created_by
                    ORDER BY c.id DESC LIMIT 10');

layout_head('Control');
?>
<h1>Control</h1>
<p class="sub">The single source of truth for every app install. Changes take effect on the next sync.</p>

<div class="panel danger-zone">
  <h2>Kill switch</h2>
  <p style="color:var(--muted);font-size:.9rem;margin-bottom:.9rem">
    Halts every connected account, turns global trading off, and tells every app
    to stop. Open positions are closed by the engine on its next tick.
  </p>
  <div class="row">
    <form method="post" onsubmit="return confirm('Stop ALL trading and halt every account?')">
      <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
      <input type="hidden" name="action" value="kill_all">
      <button class="btn danger">Stop everything</button>
    </form>
    <form method="post" onsubmit="return confirm('Clear kill-switch halts?')">
      <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
      <input type="hidden" name="action" value="resume_all">
      <button class="btn ghost">Clear halts</button>
    </form>
  </div>
  <p class="note">Owner role required. Clearing halts does not re-enable trading —
     that is a separate, deliberate step below.</p>
</div>

<form method="post">
<input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
<input type="hidden" name="action" value="save_config">

<div class="panel">
  <h2>Master</h2>
  <div class="check">
    <input type="checkbox" id="te" name="trading_enabled" <?= $chk('trading_enabled') ?>>
    <label for="te"><strong>Trading enabled</strong> — with this off the engine
      evaluates and records signals but places no orders</label>
  </div>
  <div class="row">
    <div class="check"><input type="checkbox" id="ab" name="allow_buy" <?= $chk('allow_buy') ?>>
      <label for="ab">Allow BUY</label></div>
    <div class="check"><input type="checkbox" id="as" name="allow_sell" <?= $chk('allow_sell') ?>>
      <label for="as">Allow SELL</label></div>
  </div>
  <div class="row">
    <div class="field"><label>Symbol</label>
      <input type="text" name="symbol" value="<?= $num('symbol', 'XAUUSD') ?>"></div>
    <div class="field"><label>Timeframe</label>
      <select name="timeframe">
        <?php foreach (['M1','M5','M15','M30','H1'] as $tf): ?>
          <option <?= ($g['timeframe'] ?? 'M5') === $tf ? 'selected' : '' ?>><?= $tf ?></option>
        <?php endforeach; ?>
      </select></div>
    <div class="field"><label>Entry mode</label>
      <select name="entry_mode">
        <?php foreach (['slope_ema_cross' => 'Slope crosses 50 EMA',
                        'slope_flip' => 'Slope colour flip',
                        'either' => 'Either'] as $v => $lab): ?>
          <option value="<?= $v ?>" <?= ($g['entry_mode'] ?? '') === $v ? 'selected' : '' ?>>
            <?= h($lab) ?></option>
        <?php endforeach; ?>
      </select></div>
  </div>
</div>

<div class="panel">
  <h2>Strategy</h2>
  <div class="row">
    <div class="field"><label>Slope period</label>
      <input type="number" name="slope_period" value="<?= $num('slope_period', 12) ?>"></div>
    <div class="field"><label>Slope method</label>
      <select name="slope_method">
        <?php foreach (['SMA','EMA','SMMA','LWMA'] as $i => $lab): ?>
          <option value="<?= $i ?>" <?= (int)($g['slope_method'] ?? 2) === $i ? 'selected' : '' ?>>
            <?= $lab ?></option>
        <?php endforeach; ?>
      </select></div>
    <div class="field"><label>EMA period</label>
      <input type="number" name="ema_period" value="<?= $num('ema_period', 50) ?>"></div>
    <div class="field"><label>MACD fast</label>
      <input type="number" name="macd_fast" value="<?= $num('macd_fast', 12) ?>"></div>
    <div class="field"><label>MACD slow</label>
      <input type="number" name="macd_slow" value="<?= $num('macd_slow', 24) ?>"></div>
  </div>
  <div class="row">
    <div class="field"><label>Confirm candles</label>
      <input type="number" name="confirm_candles" value="<?= $num('confirm_candles', 1) ?>"></div>
    <div class="field"><label>Min body ratio</label>
      <input type="number" step="0.01" name="min_body_ratio" value="<?= $num('min_body_ratio', 0.25) ?>"></div>
    <div class="field"><label>Min EMA gap (boxes)</label>
      <input type="number" step="0.1" name="min_ema_gap_boxes" value="<?= $num('min_ema_gap_boxes', 0.8) ?>"></div>
  </div>
  <div class="row">
    <div class="check"><input type="checkbox" id="mf" name="use_macd_filter" <?= $chk('use_macd_filter') ?>>
      <label for="mf">MACD filter</label></div>
    <div class="check"><input type="checkbox" id="te2" name="only_with_trend_ema" <?= $chk('only_with_trend_ema') ?>>
      <label for="te2">Only with 50 EMA trend</label></div>
    <div class="check"><input type="checkbox" id="cf" name="use_chop_filter" <?= $chk('use_chop_filter') ?>>
      <label for="cf">Chop filter</label></div>
  </div>
  <p class="note">Changing any of these changes the config hash, so signals recorded
     before and after remain attributable to the version that produced them.</p>
</div>

<div class="panel">
  <h2>Risk</h2>
  <div class="row">
    <div class="field"><label>Fixed lot</label>
      <input type="number" step="0.01" name="fixed_lot" value="<?= $num('fixed_lot', 0.01) ?>"></div>
    <div class="field"><label>Take profit (pips)</label>
      <input type="number" name="take_profit_pips" value="<?= $num('take_profit_pips', 500) ?>"></div>
    <div class="field"><label>Swing SL buffer (pips)</label>
      <input type="number" name="swing_sl_buffer_pips" value="<?= $num('swing_sl_buffer_pips', 20) ?>"></div>
    <div class="field"><label>Max spread (pips)</label>
      <input type="number" name="max_spread_pips" value="<?= $num('max_spread_pips', 30) ?>"></div>
  </div>
  <div class="row">
    <div class="field"><label>Daily loss limit ($)</label>
      <input type="number" step="0.01" name="daily_loss_limit" value="<?= $num('daily_loss_limit', 90) ?>"></div>
    <div class="field"><label>Daily profit target ($)</label>
      <input type="number" step="0.01" name="daily_profit_target" value="<?= $num('daily_profit_target', 200) ?>"></div>
    <div class="field"><label>Max drawdown (%)</label>
      <input type="number" step="0.1" name="max_drawdown_pct" value="<?= $num('max_drawdown_pct', 12) ?>"></div>
    <div class="field"><label>Max trades / day</label>
      <input type="number" name="max_trades_per_day" value="<?= $num('max_trades_per_day', 14) ?>"></div>
    <div class="field"><label>Max open positions</label>
      <input type="number" name="max_open_positions" value="<?= $num('max_open_positions', 3) ?>"></div>
  </div>
  <div class="row">
    <div class="check"><input type="checkbox" id="be" name="use_break_even" <?= $chk('use_break_even') ?>>
      <label for="be">Break-even lock</label></div>
    <div class="field"><label>BE trigger (pips)</label>
      <input type="number" name="break_even_pips" value="<?= $num('break_even_pips', 300) ?>"></div>
    <div class="field"><label>BE lock (pips)</label>
      <input type="number" name="break_even_lock_pips" value="<?= $num('break_even_lock_pips', 200) ?>"></div>
  </div>
</div>

<div class="panel">
  <h2>Prop-firm mode</h2>
  <div class="check"><input type="checkbox" id="pf" name="prop_firm_mode" <?= $chk('prop_firm_mode') ?>>
    <label for="pf">Enforce percentage limits from balance baselines</label></div>
  <div class="row">
    <div class="field"><label>Daily loss (%)</label>
      <input type="number" step="0.1" name="prop_daily_loss_pct" value="<?= $num('prop_daily_loss_pct', 4) ?>"></div>
    <div class="field"><label>Max drawdown (%)</label>
      <input type="number" step="0.1" name="prop_max_drawdown_pct" value="<?= $num('prop_max_drawdown_pct', 8) ?>"></div>
    <div class="check"><input type="checkbox" id="ptd" name="prop_trailing_dd" <?= $chk('prop_trailing_dd') ?>>
      <label for="ptd">Trailing drawdown</label></div>
  </div>
  <p class="note">Several prop firms restrict commercially sold EAs and third-party
     trade routing. Confirm the firm's own rules before enabling this for a customer.</p>
</div>

<div class="panel">
  <h2>Sessions (broker server time)</h2>
  <div class="check"><input type="checkbox" id="ots" name="only_trade_sessions" <?= $chk('only_trade_sessions') ?>>
    <label for="ots">Only trade inside the sessions below</label></div>
  <div class="row">
    <div class="check"><input type="checkbox" id="s1" name="sess_sydney" <?= $chk('sess_sydney') ?>>
      <label for="s1">Sydney 21:00–06:00</label></div>
    <div class="check"><input type="checkbox" id="s2" name="sess_tokyo" <?= $chk('sess_tokyo') ?>>
      <label for="s2">Tokyo 00:00–09:00</label></div>
    <div class="check"><input type="checkbox" id="s3" name="sess_london" <?= $chk('sess_london') ?>>
      <label for="s3">London 07:00–16:00</label></div>
    <div class="check"><input type="checkbox" id="s4" name="sess_newyork" <?= $chk('sess_newyork') ?>>
      <label for="s4">New York 13:00–22:00</label></div>
  </div>
  <div class="row">
    <div class="check"><input type="checkbox" id="no" name="no_overnight" <?= $chk('no_overnight') ?>>
      <label for="no">Flatten before end of day</label></div>
    <div class="field"><label>Flat time (HH:MM)</label>
      <input type="text" name="flat_time" value="<?= $num('flat_time', '23:50') ?>"></div>
  </div>
</div>

<button class="btn">Save and push to all apps</button>
</form>

<div class="panel" style="margin-top:1.2rem">
  <h2>Broadcast a message</h2>
  <form method="post">
    <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
    <input type="hidden" name="action" value="broadcast">
    <div class="field"><label>Title</label>
      <input type="text" name="title" maxlength="80" placeholder="Scheduled maintenance"></div>
    <div class="field"><label>Message</label>
      <textarea name="message" maxlength="500" required></textarea></div>
    <button class="btn ghost">Send to every install</button>
  </form>
</div>

<div class="panel">
  <h2>Recent commands</h2>
  <div class="tw"><table>
    <thead><tr><th>When</th><th>Type</th><th>Target</th><th>By</th><th>Payload</th></tr></thead>
    <tbody>
    <?php foreach ($recentCmds as $c): ?>
      <tr>
        <td><?= h(substr((string)$c['created_at'], 5, 11)) ?></td>
        <td><span class="pill <?= $c['type'] === 'kill' ? 'no' : 'dim' ?>"><?= h($c['type']) ?></span></td>
        <td><?= $c['target_user'] ? '#' . (int)$c['target_user'] : 'all' ?></td>
        <td><?= h($c['email'] ?? 'system') ?></td>
        <td class="note"><?= h(substr((string)$c['payload'], 0, 90)) ?></td>
      </tr>
    <?php endforeach; ?>
    <?php if (!$recentCmds): ?><tr><td colspan="5" class="empty">None yet.</td></tr><?php endif; ?>
    </tbody>
  </table></div>
</div>
<?php layout_foot();
