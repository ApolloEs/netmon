-- Reference SQL schema. The authoritative definition lives in db.py;
-- this file exists for quick inspection and manual psql use.

CREATE TABLE speed_tests (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    download_mbps REAL,
    upload_mbps REAL,
    ping_ms REAL,
    jitter_ms REAL,
    packet_loss_pct REAL,
    target_mbps REAL NOT NULL,
    pct_of_target REAL,
    server_id TEXT,
    server_name TEXT
);

CREATE TABLE test_events (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,  -- completed | postponed | skipped | forced
    scheduled_for TIMESTAMPTZ,
    current_throughput_mbps REAL,
    reason TEXT,
    retry_count INT DEFAULT 0,
    speed_test_id INT REFERENCES speed_tests(id)
);

CREATE TABLE outages (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    duration_seconds INT,
    trigger TEXT
);

CREATE TABLE connectivity_pings (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    target TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    latency_ms REAL
);

CREATE INDEX idx_pings_timestamp ON connectivity_pings(timestamp);
