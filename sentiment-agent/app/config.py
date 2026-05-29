from pydantic_settings import BaseSettings
from pydantic import model_validator


class Settings(BaseSettings):
    SERVICE_PORT: int = 8003
    SERVICE_NAME: str = "sentiment-agent"

    MONGODB_HOST: str = "mongodb"
    MONGODB_PORT: int = 27017
    MONGODB_USER: str = "stock_admin"
    MONGODB_PASSWORD: str = "stock_password"
    MONGODB_DB: str = "stock_prediction"
    MONGODB_URL: str = ""

    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_URL: str = ""

    FINBERT_MODEL: str = "ProsusAI/finbert"
    SENTIMENT_WINDOW_MINUTES: int = 60
    MLFLOW_TRACKING_URI: str = "http://mlflow:5000"

    @model_validator(mode="after")
    def build_urls(self) -> "Settings":
        if not self.MONGODB_URL:
            self.MONGODB_URL = (
                f"mongodb://{self.MONGODB_USER}:{self.MONGODB_PASSWORD}"
                f"@{self.MONGODB_HOST}:{self.MONGODB_PORT}/{self.MONGODB_DB}?authSource=admin"
            )
        if not self.RABBITMQ_URL:
            self.RABBITMQ_URL = (
                f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}"
                f"@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/"
            )
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
