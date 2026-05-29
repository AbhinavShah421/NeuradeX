from pydantic_settings import BaseSettings
from pydantic import model_validator


class Settings(BaseSettings):
    SERVICE_PORT: int = 8001
    SERVICE_NAME: str = "market-data-service"

    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "stock_user"
    POSTGRES_PASSWORD: str = "stock_password"
    POSTGRES_DB: str = "stock_prediction_db"
    POSTGRES_URL: str = ""

    MONGODB_HOST: str = "mongodb"
    MONGODB_PORT: int = 27017
    MONGODB_USER: str = "stock_admin"
    MONGODB_PASSWORD: str = "stock_password"
    MONGODB_DB: str = "stock_prediction"
    MONGODB_URL: str = ""

    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    REDIS_URL: str = ""

    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_URL: str = ""

    GROWW_API_KEY: str = ""
    GROWW_API_SECRET: str = ""
    NEWSAPI_KEY: str = ""
    ALPHA_VANTAGE_KEY: str = ""

    WATCHLIST: str = "RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK"
    TICK_INTERVAL_SECONDS: int = 60
    NEWS_INTERVAL_SECONDS: int = 300
    HISTORICAL_DAYS: int = 365

    MLFLOW_TRACKING_URI: str = "http://mlflow:5000"

    @model_validator(mode="after")
    def build_urls(self) -> "Settings":
        if not self.POSTGRES_URL:
            self.POSTGRES_URL = (
                f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )
        if not self.MONGODB_URL:
            self.MONGODB_URL = (
                f"mongodb://{self.MONGODB_USER}:{self.MONGODB_PASSWORD}"
                f"@{self.MONGODB_HOST}:{self.MONGODB_PORT}/{self.MONGODB_DB}?authSource=admin"
            )
        if not self.REDIS_URL:
            auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
            self.REDIS_URL = f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        if not self.RABBITMQ_URL:
            self.RABBITMQ_URL = (
                f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}"
                f"@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/"
            )
        return self

    @property
    def watchlist_symbols(self) -> list[str]:
        return [s.strip() for s in self.WATCHLIST.split(",") if s.strip()]

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
