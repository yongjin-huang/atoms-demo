from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://atoms:atoms@localhost:5432/atoms"

    # Shared with the Next BFF. The only thing standing between a forged
    # X-User-Id header and someone else's projects.
    INTERNAL_API_KEY: str = "dev-internal-key-change-me"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
