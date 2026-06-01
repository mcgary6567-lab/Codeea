<?php
// Shared DB connection + helpers. Auto-creates the tables on first use so the
// only setup step is editing config.php (no phpMyAdmin import required).

require_once __DIR__ . '/config.php';

function db() {
    static $pdo = null;
    if ($pdo === null) {
        $pdo = new PDO(
            'mysql:host=' . DB_HOST . ';dbname=' . DB_NAME . ';charset=utf8mb4',
            DB_USER, DB_PASS,
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
             PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
        );
        // Broadcast feed: one row per signal from YOUR TradingView.
        $pdo->exec("CREATE TABLE IF NOT EXISTS signals (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            payload MEDIUMTEXT NOT NULL,
            symbol VARCHAR(64) NULL,
            action VARCHAR(16) NULL,
            created_at INT NOT NULL,
            INDEX (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
        // Customer licences: each token = one paying user's app.
        $pdo->exec("CREATE TABLE IF NOT EXISTS clients (
            token VARCHAR(64) PRIMARY KEY,
            label VARCHAR(128) NULL,
            active TINYINT NOT NULL DEFAULT 1,
            expires_at INT NOT NULL DEFAULT 0,
            last_seen_id BIGINT NOT NULL DEFAULT 0,
            last_poll_at INT NOT NULL DEFAULT 0,
            created_at INT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
    }
    return $pdo;
}

function json_out($arr, $code = 200) {
    http_response_code($code);
    header('Content-Type: application/json');
    echo json_encode($arr);
    exit;
}
