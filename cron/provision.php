<?php
/**
 * Provisioning cron: deploys newly-linked broker accounts to MetaApi and
 * promotes them to `connected` once the broker connection is actually up.
 *
 *   */5 * * * * /usr/bin/php /home/USER/domains/app.goldscalpers.com/public_html/cron/provision.php
 *
 * The work itself lives in lib/provisioning.php so the engine can run it as a
 * fallback (if this cron stalls) and the admin dashboard can run it on demand.
 * Kept separate from engine.php on purpose: provisioning is slow and
 * occasionally hangs, and it must never delay a trading tick.
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') { http_response_code(403); exit("cli only\n"); }

require_once __DIR__ . '/../lib/provisioning.php';

gs_provision_run(static function (string $m): void {
    fwrite(STDOUT, '[' . gmdate('Y-m-d H:i:s') . '] ' . $m . "\n");
});
