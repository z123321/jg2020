from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "mysql+pymysql://root:123456@127.0.0.1:3306/backtest"
    SECRET_KEY: str = "your-secret-key-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    REDIS_URL: str = "redis://127.0.0.1:6379/0"

    class Config:
        env_file = ".env"

settings = Settings()