from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "企业班车智能调度系统"
    API_V1_PREFIX: str = "/api/v1"
    SECRET_KEY: str = "shuttle-bus-scheduling-secret-key-2024"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    ALGORITHM: str = "HS256"

    DATABASE_URL: str = "sqlite:///./shuttle_bus.db"

    MIN_PASSENGERS_THRESHOLD: int = 3
    SEAT_LOCK_TIMEOUT: int = 300
    NOTIFY_BEFORE_DEPARTURE: int = 30
    ETA_UPDATE_INTERVAL: int = 60

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()
