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
$LICENSE_FILE = __DIR__ . '/../licenses.txt';

// Private metadata (name / email / date-time per account). Never web-served
// (dotfile, denied by the root .htaccess). Keeps customer PII out of the
// public licenses.txt.
$META_FILE = __DIR__ . '/../.accounts.json';
