from pydantic_settings import BaseSettings
from pydantic import model_validator


class Settings(BaseSettings):
    SERVICE_PORT: int = 8013
    SERVICE_NAME: str = "model-trainer"

    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "stock_user"
    POSTGRES_PASSWORD: str = "stock_password"
    POSTGRES_DB: str = "stock_prediction_db"
    POSTGRES_URL: str = ""

    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_URL: str = ""

    MLFLOW_TRACKING_URI: str = "http://mlflow:5000"

    WATCHLIST: str = "RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,KOTAKBANK,BAJFINANCE,SBIN,WIPRO,AXISBANK"
    XGBOOST_MODEL_NAME: str = "technical-xgboost"
    RL_MODEL_NAME: str = "rl-trading-policy"
    MIN_TRAIN_ACCURACY: float = 0.52
    MIN_SHARPE_RATIO: float = 1.0
    TRAIN_DAYS: int = 365
    RL_TIMESTEPS: int = 200_000
    RETRAIN_SCHEDULE_HOURS: int = 24

    @model_validator(mode="after")
    def build_urls(self) -> "Settings":
        if not self.POSTGRES_URL:
            self.POSTGRES_URL = f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        if not self.RABBITMQ_URL:
            self.RABBITMQ_URL = f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/"
        return self

    @property
    def watchlist_symbols(self) -> list[str]:
        return [s.strip() for s in self.WATCHLIST.split(",") if s.strip()]

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
