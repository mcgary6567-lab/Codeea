<?php
/**
 * Logs: the engine and provisioning cron logs, and the activity (audit) trail.
 * Support can read; clearing anything is owner-only and is itself audited.
 *
 * The crons append to <home>/gs-engine.log and <home>/gs-provision.log
 * themselves (gs_log_append); <home> is three levels above the web root on
 * Hostinger. Override with engine.log_dir.
 */
require_once __DIR__ . '/_boot.php';
$me = require_admin('support');

$tab   = (string)($_GET['tab'] ?? $_POST['tab'] ?? $_GET['log'] ?? 'engine');
if (!in_array($tab, ['engine', 'provision', 'activity'], true)) $tab = 'engine';
$lines = max(50, min(2000, (int)($_GET['lines'] ?? 300)));

/* ---------------- actions (owner) ---------------- */
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    csrf_check();
    require_admin('owner');
    $action = (string)($_POST['action'] ?? '');

    if ($action === 'clear_log' && $tab !== 'activity') {
        $file = gs_logfile($tab);
        $size = is_file($file) ? (int)filesize($file) : 0;
        $ok   = file_put_contents($file, '', LOCK_EX) !== false;
        clearstatcache(true, $file);
        gs_audit('admin', (int)$me['id'], 'log_cleared', ['log' => $tab, 'bytes' => $size]);
        flash($ok ? sprintf('Cleared gs-%s.log (%s KB). The cron adds new lines on its next run.', $tab, number_format($size / 1024, 1))
                  : 'Could not clear the log file: ' . h($file), $ok ? 'ok' : 'err');
        header("Location: logs.php?tab=$tab"); exit;
    }

    if ($action === 'clear_activity') {
        $scope = (string)($_POST['scope'] ?? '30');
        if ($scope === 'all') {
            $n = q('DELETE FROM audit_log')->rowCount();
            $what = 'all';
        } else {
            $days = max(1, min(365, (int)$scope));
            $n = q('DELETE FROM audit_log WHERE created_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL ? DAY)', [$days])->rowCount();
            $what = "older than $days days";
        }
        gs_audit('admin', (int)$me['id'], 'activity_cleared', ['scope' => $what, 'rows' => $n]);
        flash("Removed $n activity entr" . ($n === 1 ? 'y' : 'ies') . " ($what).");
        header('Location: logs.php?tab=activity'); exit;
    }
    header("Location: logs.php?tab=$tab"); exit;
}

function tail_file(string $path, int $n): array
{
    if (!is_readable($path)) return [];
    $size = filesize($path);
    if ($size === false || $size === 0) return [];
    $chunk = min($size, max(65536, $n * 200));
    $fh = fopen($path, 'rb');
    fseek($fh, -$chunk, SEEK_END);
    $data = stream_get_contents($fh);
    fclose($fh);
    $out = preg_split('/\r?\n/', (string)$data);
    if ($chunk < $size) array_shift($out);           // drop the partial first line
    return array_slice(array_values(array_filter($out, static fn($l) => $l !== '')), -$n);
}

/* ---------------- data ---------------- */
$isOwner = $me['role'] === 'owner';
if ($tab === 'activity') {
    $total = (int)qval('SELECT COUNT(*) FROM audit_log', [], 0);
    [$page, $offset, $per, $pager] = paginate($total, 50, 'page');
    $activity = qall(
      "SELECT l.*, CASE l.actor_type
                     WHEN 'admin' THEN (SELECT email FROM admins WHERE id = l.actor_id)
                     WHEN 'user'  THEN (SELECT email FROM users  WHERE id = l.actor_id)
                     ELSE 'system' END AS who
         FROM audit_log l ORDER BY l.id DESC LIMIT $per OFFSET $offset");
} else {
    $file  = gs_logfile($tab);
    $tail  = tail_file($file, $lines);
    $mtime = is_file($file) ? filemtime($file) : null;
    $size  = is_file($file) ? (int)filesize($file) : 0;
}

layout_head('Logs');
?>
<div class="dash-head">
  <div>
    <h1>Logs</h1>
    <p class="sub" style="margin:0">
      <?php if ($tab === 'activity'): ?>
        Every admin, customer and system action · <?= number_format($total) ?> entries
      <?php else: ?>
        Last <?= count($tail) ?> lines of <code>gs-<?= $tab ?>.log</code>
        · <?= number_format($size / 1024, 1) ?> KB<?= $mtime ? ' · updated ' . gmdate('Y-m-d H:i:s', $mtime) . ' UTC' : '' ?>
      <?php endif; ?>
    </p>
  </div>
  <div class="dash-actions">
    <a class="btn <?= $tab === 'engine' ? '' : 'ghost' ?> sm" href="logs.php?tab=engine">Engine</a>
    <a class="btn <?= $tab === 'provision' ? '' : 'ghost' ?> sm" href="logs.php?tab=provision">Provisioning</a>
    <a class="btn <?= $tab === 'activity' ? '' : 'ghost' ?> sm" href="logs.php?tab=activity">Activity</a>
  </div>
</div>

<?php if ($tab === 'activity'): ?>
<div class="panel">
  <?php if ($isOwner): ?>
  <div class="panel-head">
    <h2 style="margin:0">Activity</h2>
    <form method="post" class="actions" onsubmit="return confirm('Delete the selected activity entries? This cannot be undone.')">
      <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
      <input type="hidden" name="action" value="clear_activity">
      <input type="hidden" name="tab" value="activity">
      <select name="scope" style="width:auto;padding:.35rem .5rem">
        <option value="7">older than 7 days</option>
        <option value="30" selected>older than 30 days</option>
        <option value="90">older than 90 days</option>
        <option value="all">everything</option>
      </select>
      <button class="btn danger sm" <?= $total ? '' : 'disabled' ?>>Clear</button>
    </form>
  </div>
  <?php endif; ?>
  <div class="tw"><table>
    <thead><tr><th>When (UTC)</th><th>Action</th><th>By</th><th>Detail</th><th class="hide-sm">IP</th></tr></thead>
    <tbody>
    <?php foreach ($activity as $ev):
        $bad = (bool)preg_match('/kill|halt|fail|error|reject|suspend|cleared|purged/i', (string)$ev['action']);
        $dt = (string)$ev['detail'];
        if ($dt === 'null') $dt = '';
        if ($dt !== '' && $dt[0] === '{') {
            $j = json_decode($dt, true);
            if (is_array($j)) $dt = implode(' · ', array_map(static fn($k2, $v2) => $k2 . '=' . (is_scalar($v2) ? $v2 : json_encode($v2)), array_keys($j), $j));
        }
    ?>
      <tr class="<?= $bad ? 'row-bad' : '' ?>">
        <td class="note"><?= h(substr((string)$ev['created_at'], 0, 16)) ?></td>
        <td><strong><?= h(str_replace('_', ' ', (string)$ev['action'])) ?></strong></td>
        <td><?= h((string)$ev['who']) ?><span class="sub2"><?= h($ev['actor_type']) ?><?= $ev['actor_id'] ? ' #' . (int)$ev['actor_id'] : '' ?></span></td>
        <td class="note"><?= h(substr($dt, 0, 160)) ?></td>
        <td class="note hide-sm"><?= h((string)$ev['ip']) ?></td>
      </tr>
    <?php endforeach; ?>
    <?php if (!$activity): ?><tr><td colspan="5" class="empty">Nothing logged yet.</td></tr><?php endif; ?>
    </tbody>
  </table></div>
  <?= $pager ?>
</div>

<?php else: ?>
<div class="panel">
  <div class="panel-head">
    <h2 style="margin:0"><?= $tab === 'engine' ? 'Engine' : 'Provisioning' ?> log</h2>
    <div class="actions">
      <a class="btn ghost sm" href="logs.php?tab=<?= $tab ?>&lines=<?= $lines === 300 ? 1000 : 300 ?>"><?= $lines === 300 ? 'More lines' : 'Fewer lines' ?></a>
      <?php if ($isOwner): ?>
      <form method="post" onsubmit="return confirm('Empty gs-<?= $tab ?>.log? This cannot be undone.')">
        <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
        <input type="hidden" name="action" value="clear_log">
        <input type="hidden" name="tab" value="<?= $tab ?>">
        <button class="btn danger sm" <?= $size ? '' : 'disabled' ?>>Clear log</button>
      </form>
      <?php endif; ?>
    </div>
  </div>
<?php if (!is_file($file)): ?>
  <p class="empty">No log file yet at <code><?= h($file) ?></code>. The cron writes it on its first run;
     if your home directory is somewhere else, set <code>engine.log_dir</code> in the config.</p>
<?php elseif (!$tail): ?>
  <p class="empty">The log is empty.</p>
<?php else: ?>
  <pre class="log"><?php foreach ($tail as $l):
      $cls = preg_match('/error|fatal|fail|exception|refus|unreachable/i', $l) ? 'l-bad'
           : (preg_match('/ENTER|CONNECTED|provisioned|deploy|HALT|CLOSE/i', $l) ? 'l-hot' : '');
  ?><span class="<?= $cls ?>"><?= h($l) ?></span>
<?php endforeach; ?></pre>
<?php endif; ?>
</div>
<p class="note">Newest lines are at the bottom. Every engine entry carries a run id so one tick can be followed end to end.
   The engine writes a line every minute, so a cleared engine log fills again straight away.</p>
<?php endif; ?>
<?php layout_foot();
