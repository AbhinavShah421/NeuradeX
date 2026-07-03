-- TimescaleDB initialization
-- Runs once when the postgres container is first created (empty volume).
-- If upgrading an existing container, run this manually:
--   docker exec -it stock-prediction-postgres psql -U stock_user -d stock_prediction_db -f /docker-entrypoint-initdb.d/init_timescale.sql

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- OHLCV candles (time-series backbone for all agents)
CREATE TABLE IF NOT EXISTS ohlcv (
    time        TIMESTAMPTZ     NOT NULL,
    symbol      TEXT            NOT NULL,
    exchange    TEXT            NOT NULL DEFAULT 'NSE',
    interval    TEXT            NOT NULL,   -- '1m','5m','15m','1h','1d'
    open        DOUBLE PRECISION NOT NULL,
    high        DOUBLE PRECISION NOT NULL,
    low         DOUBLE PRECISION NOT NULL,
    close       DOUBLE PRECISION NOT NULL,
    volume      BIGINT          NOT NULL,
    oi          BIGINT,
    source      TEXT
);

SELECT create_hypertable('ohlcv', 'time', if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ohlcv_unique ON ohlcv (symbol, exchange, interval, time DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_interval ON ohlcv (symbol, interval, time DESC);

-- Ensemble agent weights (persisted across restarts)
CREATE TABLE IF NOT EXISTS agent_weights (
    id          SERIAL PRIMARY KEY,
    agent       TEXT            NOT NULL UNIQUE,
    weight      DOUBLE PRECISION NOT NULL DEFAULT 0.20,
    win_count   INTEGER         NOT NULL DEFAULT 0,
    total_count INTEGER         NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

INSERT INTO agent_weights (agent, weight) VALUES
    ('technical', 0.30),
    ('sentiment', 0.20),
    ('macro',     0.15),
    ('pattern',   0.20),
    ('rl',        0.15)
ON CONFLICT (agent) DO NOTHING;

-- Trade records for feedback learning
CREATE TABLE IF NOT EXISTS trade_records (
    id                  SERIAL PRIMARY KEY,
    trade_id            TEXT            UNIQUE NOT NULL,
    symbol              TEXT            NOT NULL,
    exchange            TEXT            NOT NULL DEFAULT 'NSE',
    action              TEXT            NOT NULL,   -- BUY/SELL
    entry_price         DOUBLE PRECISION NOT NULL,
    exit_price          DOUBLE PRECISION,
    pnl_pct             DOUBLE PRECISION,
    pnl_abs             DOUBLE PRECISION,
    duration_minutes    INTEGER,
    ensemble_confidence DOUBLE PRECISION,
    agent_signals       JSONB,
    market_context      JSONB,
    outcome             TEXT,                       -- WIN/LOSS/BREAK_EVEN/OPEN
    timestamp_open      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    timestamp_close     TIMESTAMPTZ,
    trade_source        TEXT            DEFAULT 'LIVE',  -- LIVE/PAPER/BACKTEST
    paper_trade         BOOLEAN         DEFAULT FALSE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()  -- when the run was executed
);
ALTER TABLE trade_records ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE trade_records ADD COLUMN IF NOT EXISTS session_id TEXT;

CREATE INDEX IF NOT EXISTS idx_trade_records_symbol  ON trade_records (symbol, timestamp_open DESC);
CREATE INDEX IF NOT EXISTS idx_trade_records_outcome ON trade_records (outcome, timestamp_open DESC);
CREATE INDEX IF NOT EXISTS idx_trade_records_session ON trade_records (session_id) WHERE session_id IS NOT NULL;

-- One row per completed session — survives Redis TTL expiry
CREATE TABLE IF NOT EXISTS session_metadata (
    id              SERIAL PRIMARY KEY,
    session_id      TEXT             UNIQUE NOT NULL,
    symbol          TEXT             NOT NULL,
    mode            TEXT             NOT NULL,   -- paper / replay / backtest
    date            TEXT             NOT NULL,   -- YYYY-MM-DD
    status          TEXT             NOT NULL,   -- done / stopped / error
    capital         DOUBLE PRECISION,
    final_cash      DOUBLE PRECISION,
    trade_count     INTEGER          DEFAULT 0,
    win_count       INTEGER          DEFAULT 0,
    total_pnl_abs   DOUBLE PRECISION,
    total_pnl_pct   DOUBLE PRECISION,
    candle_count    INTEGER          DEFAULT 0,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    session_data    JSONB            -- full metrics snapshot
);

-- One row per candle decision — full log, not the 30-candle rolling Redis window
CREATE TABLE IF NOT EXISTS session_decisions (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT             NOT NULL,
    symbol      TEXT             NOT NULL,
    candle_time TEXT             NOT NULL,   -- HH:MM
    price       DOUBLE PRECISION,
    action      TEXT,
    executed    BOOLEAN          DEFAULT FALSE,
    confidence  DOUBLE PRECISION,
    reason      TEXT,
    indicators  JSONB,
    agents      JSONB,
    trade       JSONB,           -- non-null only on BUY/SELL execution
    created_at  TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_decisions_session
    ON session_decisions (session_id, candle_time);

-- RL agent experience replay buffer (recent 10k tuples)
CREATE TABLE IF NOT EXISTS rl_experiences (
    id          BIGSERIAL PRIMARY KEY,
    symbol      TEXT            NOT NULL,
    state       JSONB           NOT NULL,
    action      INTEGER         NOT NULL,   -- 0=HOLD, 1=BUY, 2=SELL
    reward      DOUBLE PRECISION NOT NULL,
    next_state  JSONB           NOT NULL,
    done        BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rl_exp_created ON rl_experiences (created_at DESC);
