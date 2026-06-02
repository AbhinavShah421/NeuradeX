"""
Configuration settings for the application
"""

from pydantic_settings import BaseSettings
from pydantic import model_validator


class Settings(BaseSettings):
    """Application settings"""

    # App settings
    APP_NAME: str = "NeuradeX"
    DEBUG: bool = True
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # CORS settings
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000"
    ]

    # Database - PostgreSQL
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "stock_user"
    POSTGRES_PASSWORD: str = "stock_password"
    POSTGRES_DB: str = "stock_prediction_db"
    POSTGRES_URL: str = ""

    # Database - MongoDB
    MONGODB_HOST: str = "mongodb"
    MONGODB_PORT: int = 27017
    MONGODB_USER: str = "stock_admin"
    MONGODB_PASSWORD: str = "stock_password"
    MONGODB_DB: str = "stock_prediction"
    MONGODB_URL: str = ""

    # Database - InfluxDB
    INFLUXDB_HOST: str = "influxdb"
    INFLUXDB_PORT: int = 8086
    INFLUXDB_USER: str = "stock_user"
    INFLUXDB_PASSWORD: str = "stock_password"
    INFLUXDB_DB: str = "stock_metrics"
    INFLUXDB_ORG: str = "stock-org"
    INFLUXDB_TOKEN: str = "stock-token"

    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    REDIS_URL: str = ""

    # RabbitMQ
    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_URL: str = ""

    # LLM Settings
    # Provider selection: "auto" uses Anthropic when ANTHROPIC_API_KEY is set,
    # otherwise falls back to Ollama. Force one with "anthropic" / "ollama", or
    # disable LLM entirely with "off".
    LLM_PROVIDER: str = "auto"          # auto | anthropic | ollama | off

    # Anthropic (Claude) — used when a key is present
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"
    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com"

    # Angel One (SmartAPI) — real-time NSE LTP for paper trading. Optional; when
    # unset, live data uses the Yahoo feed. Get these at smartapi.angelbroking.com.
    ANGEL_API_KEY: str = ""
    ANGEL_CLIENT_CODE: str = ""
    ANGEL_PIN: str = ""
    ANGEL_TOTP_SECRET: str = ""
    ANGEL_POLL_SECS: int = 3

    # Ollama (local) — runs on the host; host.docker.internal reaches it from Docker
    LLM_MODEL: str = "llama3.2"          # ollama model
    LLM_API_URL: str = "http://host.docker.internal:11434"
    LLM_MAX_TOKENS: int = 512
    LLM_TEMPERATURE: float = 0.7

    # Auth / JWT
    JWT_SECRET: str = "neuradex-jwt-secret-change-in-production"
    JWT_EXPIRE_HOURS: int = 24

    # SMTP (email OTP)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "NeuradeX <noreply@neuradex.in>"

    # Twilio (WhatsApp OTP)
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_FROM: str = "whatsapp:+14155238886"  # Twilio sandbox number

    # Groww Trading API
    GROWW_API_KEY: str = ""
    GROWW_API_SECRET: str = ""

    # API Keys (for external services)
    ALPHA_VANTAGE_KEY: str = ""
    NEWSAPI_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # ML Model settings
    MODEL_UPDATE_INTERVAL: int = 3600
    PREDICTION_CONFIDENCE_THRESHOLD: float = 0.5

    # Socket.IO settings
    SOCKETIO_ASYNC_MODE: str = "asgi"
    SOCKETIO_CORS_ALLOWED_ORIGINS: list[str] = ["*"]

    # Internal service URLs
    FEEDBACK_SERVICE_URL: str = "http://feedback-service:8012"
    SCANNER_SERVICE_URL: str = "http://stock-scanner:8014"
    AUTOPILOT_SERVICE_URL: str = "http://autopilot-service:8015"

    # The agents are INTRADAY. The multi-day strategy backtester (SMA/MACD swing
    # trades held for days/months) must NOT train the intraday ensemble weights/
    # RL — that pollutes the learning signal. Intraday training comes from paper +
    # replay sessions. Leave off unless you specifically want swing-trade training.
    TRAIN_FROM_STRATEGY_BACKTEST: bool = False

    # Intraday trade costs (basis points) so simulated P&L is net-of-cost.
    TRADE_SLIPPAGE_BPS: float = 2.0   # per side — spread/slippage on the fill
    TRADE_FEE_BPS: float = 3.0        # per side — brokerage + exchange + GST
    TRADE_STT_BPS: float = 2.5        # sell side — Securities Transaction Tax

    # Trade gate — how selective session entries are. Switchable at runtime from
    # the dashboard (stored in Redis); this is the default.
    #   strict → fewest trades, highest conviction (ensemble must vote BUY)
    #   gentle → balanced (timing setup + ensemble not bearish + confidence floor)
    #   loose  → most trades (timing setup + ensemble not bearish)
    TRADE_GATE: str = "gentle"

    # Pattern-memory nightly refresh (replays real backtests to keep the bank fresh)
    MEMORY_SWEEP_ENABLED: bool = True
    MEMORY_SWEEP_HOUR_IST: int = 2        # run at ~02:00 IST (after market close)
    MEMORY_SWEEP_LOOKBACK_DAYS: int = 730 # 2 years of daily candles per symbol

    @model_validator(mode='after')
    def compute_urls(self) -> 'Settings':
        if not self.POSTGRES_URL:
            self.POSTGRES_URL = (
                f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )
        if not self.MONGODB_URL:
            self.MONGODB_URL = (
                f"mongodb://{self.MONGODB_USER}:{self.MONGODB_PASSWORD}"
                f"@{self.MONGODB_HOST}:{self.MONGODB_PORT}/{self.MONGODB_DB}"
                f"?authSource=admin"
            )
        if not self.REDIS_URL:
            if self.REDIS_PASSWORD:
                self.REDIS_URL = f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
            else:
                self.REDIS_URL = f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        if not self.RABBITMQ_URL:
            self.RABBITMQ_URL = (
                f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}"
                f"@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/"
            )
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True


# Global settings instance
settings = Settings()
