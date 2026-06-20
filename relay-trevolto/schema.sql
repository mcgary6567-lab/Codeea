-- Optional: the tables are auto-created by db.php on first request, so you
-- normally don't need to run this. Provided for reference / manual import.

CREATE TABLE IF NOT EXISTS signals (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    payload MEDIUMTEXT NOT NULL,
    symbol VARCHAR(64) NULL,
    action VARCHAR(16) NULL,
    created_at INT NOT NULL,
    INDEX (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS clients (
    token VARCHAR(64) PRIMARY KEY,
    label VARCHAR(128) NULL,
    active TINYINT NOT NULL DEFAULT 1,
    expires_at INT NOT NULL DEFAULT 0,
    last_seen_id BIGINT NOT NULL DEFAULT 0,
    last_poll_at INT NOT NULL DEFAULT 0,
    created_at INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
