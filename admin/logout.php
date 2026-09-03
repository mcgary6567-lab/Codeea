<?php
require_once __DIR__ . '/_boot.php';
$a = admin_user();
if ($a) gs_audit('admin', (int)$a['id'], 'admin_logout');
$_SESSION = [];
session_destroy();
header('Location: index.php');
