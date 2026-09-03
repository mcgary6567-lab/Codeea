<?php
/**
 * Tail of the cron logs, so nobody needs File Manager or SSH to see why the
 * engine or provisioning did something. Owner only: the logs name accounts.
 *
 * The crons append to <home>/gs-engine.log and <home>/gs-provision.log
 * themselves (gs_log_append); <home> is three levels above the web root on
 * Hostinger. Override with engine.log_dir.
 */
require_once __DIR__ . '/_boot.php';
$me = require_admin('owner');

$which = ($_GET['log'] ?? $_POST['log'] ?? 'engine') === 'provision' ? 'provision' : 'engine';
$lines = max(50, min(2000, (int)($_GET['lines'] ?? 300)));
$file  = gs_logfile($which);

/* ---------------- actions ---------------- */
if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    csrf_check();
    if (($_POST['action'] ?? '') === 'clear') {
        $size = is_file($file) ? (int)filesize($file) : 0;
        $ok = @file_put_contents($file, '') !== false;
        gs_audit('admin', (int)$me['id'], 'log_cleared', ['log' => $which, 'bytes' => $size]);
        flash($ok ? "Cleared gs-$which.log (" . number_format($size / 1024, 1) . ' KB).' : 'Could not clear the log file.', $ok ? 'ok' : 'err');
        header("Location: logs.php?log=$which"); exit;
    }
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

$tail  = tail_file($file, $lines);
$mtime = is_file($file) ? filemtime($file) : null;
$size  = is_file($file) ? (int)filesize($file) : 0;

layout_head('Logs');
?>
<div class="dash-head">
  <div>
    <h1>Logs</h1>
    <p class="sub" style="margin:0">Last <?= count($tail) ?> lines of <code><?= h(basename($file)) ?></code>
      · <?= number_format($size / 1024, 1) ?> KB<?= $mtime ? ' · updated ' . gmdate('Y-m-d H:i:s', $mtime) . ' UTC' : '' ?></p>
  </div>
  <div class="dash-actions">
    <a class="btn <?= $which === 'engine' ? '' : 'ghost' ?> sm" href="logs.php?log=engine&lines=<?= $lines ?>">Engine</a>
    <a class="btn <?= $which === 'provision' ? '' : 'ghost' ?> sm" href="logs.php?log=provision&lines=<?= $lines ?>">Provisioning</a>
    <a class="btn ghost sm" href="logs.php?log=<?= $which ?>&lines=<?= $lines === 300 ? 1000 : 300 ?>"><?= $lines === 300 ? 'More lines' : 'Fewer lines' ?></a>
    <form method="post" style="display:inline" onsubmit="return confirm('Empty gs-<?= $which ?>.log? This cannot be undone.')">
      <input type="hidden" name="csrf" value="<?= h(csrf_token()) ?>">
      <input type="hidden" name="action" value="clear">
      <input type="hidden" name="log" value="<?= $which ?>">
      <button class="btn danger sm" <?= $size ? '' : 'disabled' ?>>Clear log</button>
    </form>
  </div>
</div>

<div class="panel">
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
   Clearing a log is recorded in the audit trail.</p>
<?php layout_foot();
