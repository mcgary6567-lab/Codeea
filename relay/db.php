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
        // Per-token IPs (key-sharing detection). Created here too so poll.php can
        // record from day one without the admin opening the panel first.
        $pdo->exec("CREATE TABLE IF NOT EXISTS client_ips (
            token VARCHAR(64) NOT NULL,
            ip VARCHAR(45) NOT NULL,
            last_seen INT NOT NULL,
            hits BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (token, ip),
            INDEX (last_seen)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
        // Self-service free trials: one trial per machine fingerprint AND email,
        // so reinstalling/relaunching can't farm unlimited trials.
        $pdo->exec("CREATE TABLE IF NOT EXISTS trials (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            machine VARCHAR(64) NOT NULL,
            email VARCHAR(190) NOT NULL,
            token VARCHAR(64) NOT NULL,
            created_at INT NOT NULL,
            INDEX (machine),
            INDEX (email)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
    }
    return $pdo;
}

// Optional extras (notes + IP / key-sharing tracking). Called by the admin
// panel; kept out of the hot poll/verify path. Safe to call repeatedly.
function ensure_extras($pdo) {
    $pdo->exec("CREATE TABLE IF NOT EXISTS client_ips (
        token VARCHAR(64) NOT NULL,
        ip VARCHAR(45) NOT NULL,
        last_seen INT NOT NULL,
        hits BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (token, ip),
        INDEX (last_seen)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
    // Additive columns for installs created before these existed.
    foreach ([
        'note'    => "ALTER TABLE clients ADD COLUMN note VARCHAR(255) NULL",
        'last_ip' => "ALTER TABLE clients ADD COLUMN last_ip VARCHAR(45) NULL",
    ] as $col => $sql) {
        try {
            $q = $pdo->prepare("SELECT COUNT(*) FROM information_schema.columns
                                WHERE table_schema = DATABASE() AND table_name='clients'
                                  AND column_name = ?");
            $q->execute([$col]);
            if (!(int)$q->fetchColumn()) {
                $pdo->exec($sql);
            }
        } catch (Throwable $e) { /* lacking info_schema rights — skip silently */ }
    }
}

// Best guess at the real client IP (honours Cloudflare / reverse proxies).
function client_ip(): string {
    foreach (['HTTP_CF_CONNECTING_IP', 'HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR'] as $h) {
        if (!empty($_SERVER[$h])) {
            $ip = trim(explode(',', $_SERVER[$h])[0]);
            if ($ip !== '') return substr($ip, 0, 45);
        }
    }
    return '';
}

function json_out($arr, $code = 200) {
    http_response_code($code);
    header('Content-Type: application/json');
    echo json_encode($arr);
    exit;
}
