<?php
/**
 * Tail of the cron logs, so nobody needs File Manager or SSH to see why the
 * engine or provisioning did something. Owner only: the logs name accounts.
 *
 * The cron entries write to <home>/gs-engine.log and <home>/gs-provision.log;
 * <home> is three levels above the web root on Hostinger
 * (/home/USER/domains/<site>/public_html). Override with engine.log_dir.
 */
require_once __DIR__ . '/_boot.php';
$me = require_admin('owner');

$cfg    = gs_config();
$logDir = rtrim((string)($cfg['engine']['log_dir'] ?? dirname(GS_ROOT, 3)), '/');
$which  = ($_GET['log'] ?? 'engine') === 'provision' ? 'provision' : 'engine';
$lines  = max(50, min(2000, (int)($_GET['lines'] ?? 300)));
$file   = "$logDir/gs-$which.log";

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
    return array_slice(array_filter($out, static fn($l) => $l !== ''), -$n);
}

$tail = tail_file($file, $lines);
$mtime = is_file($file) ? filemtime($file) : null;

layout_head('Logs');
?>
<div class="dash-head">
  <div>
    <h1>Logs</h1>
    <p class="sub" style="margin:0">Last <?= count($tail) ?> lines of <code><?= h($file) ?></code>
      <?= $mtime ? ' · updated ' . gmdate('Y-m-d H:i:s', $mtime) . ' UTC' : '' ?></p>
  </div>
  <div class="dash-actions">
    <a class="btn <?= $which === 'engine' ? '' : 'ghost' ?> sm" href="logs.php?log=engine&lines=<?= $lines ?>">Engine</a>
    <a class="btn <?= $which === 'provision' ? '' : 'ghost' ?> sm" href="logs.php?log=provision&lines=<?= $lines ?>">Provisioning</a>
    <a class="btn ghost sm" href="logs.php?log=<?= $which ?>&lines=<?= $lines === 300 ? 1000 : 300 ?>"><?= $lines === 300 ? 'More' : 'Fewer' ?></a>
  </div>
</div>

<div class="panel">
<?php if (!is_file($file)): ?>
  <p class="empty">No log file yet at that path. The cron command writes it on its first run;
     if your home directory is somewhere else, set <code>engine.log_dir</code> in the config.</p>
<?php elseif (!$tail): ?>
  <p class="empty">The log exists but is empty.</p>
<?php else: ?>
  <pre class="log"><?php foreach ($tail as $l):
      $cls = preg_match('/error|fatal|fail|exception|refus/i', $l) ? 'l-bad'
           : (preg_match('/ENTER|CONNECTED|provisioned|deploy|halt/i', $l) ? 'l-hot' : '');
  ?><span class="<?= $cls ?>"><?= h($l) ?></span>
<?php endforeach; ?></pre>
<?php endif; ?>
</div>
<p class="note">Newest lines are at the bottom. Every engine entry carries a run id so one tick can be followed end to end.</p>
<?php layout_foot();
