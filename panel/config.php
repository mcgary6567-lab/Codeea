<?php
// =====================================================================
//  Gold Scalpers - Licence Panel configuration
//  -------------------------------------------------------------------
//  1) Set a STRONG password below (this is what you type to log in).
//  2) Save and upload. Keep this file private (it is denied web access
//     by panel/.htaccess, and PHP still reads it server-side).
// =====================================================================

$PANEL_PASSWORD = 'Love2all123@@';   // <-- CHANGE THIS to your own password

// Path to the licence file the EA reads (site root, one level up). Leave as-is
// unless your licenses.txt lives somewhere else.
$LICENSE_FILE = __DIR__ . '/../licdata/licenses.txt';   // outside the deployed tree (licdata/ is not in git)
// One-time migration: the list used to live in the deployed tree (licenses.txt at the
// site root) and every git push overwrote it. It now lives in licdata/, which is NOT in
// the repo. If licdata/ does not exist yet, seed it from the old file so nothing is lost.
if (!is_file($LICENSE_FILE) && is_file(__DIR__ . '/../licenses.txt')) {
  @mkdir(dirname($LICENSE_FILE), 0755, true);
  @copy(__DIR__ . '/../licenses.txt', $LICENSE_FILE);
}

// Private metadata (name / email / date-time per account). Never web-served
// (dotfile, denied by the root .htaccess). Keeps customer PII out of the
// public licenses.txt.
$META_FILE = __DIR__ . '/../.accounts.json';
