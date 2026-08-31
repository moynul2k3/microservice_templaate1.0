from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DEBUG: bool = True
    APP_NAME: str = "Auth Service"
    ENV: str = "development"

    DATABASE_URL: str

    SECRET_KEY: str | None = None
    REFRESH_SECRET_KEY: str | None = None
    JWT_SECRET_KEY: str | None = None
    JWT_REFRESH_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str | None = None
    SMTP_FROM_NAME: str = "Auth Service"
    SMTP_STARTTLS: bool = True

    STORAGE_ENDPOINT_URL: str | None = None
    STORAGE_PUBLIC_BASE_URL: str | None = None
    STORAGE_ACCESS_KEY: str | None = None
    STORAGE_SECRET_KEY: str | None = None
    SERVICE_SHORT_NAME: str | None = None
    STORAGE_REGION: str | None = None

    SIGNUP_OTP_EXPIRE_MINUTES: int = 7
    REDIS_URL: str = "redis://redis:6379/0"
    RADIS_URL: str = "redis://redis:6379/0"

    def model_post_init(self, __context) -> None:
        if not self.SECRET_KEY:
            self.SECRET_KEY = self.JWT_SECRET_KEY
        if not self.REFRESH_SECRET_KEY:
            self.REFRESH_SECRET_KEY = self.JWT_REFRESH_SECRET_KEY
        if not self.JWT_SECRET_KEY:
            self.JWT_SECRET_KEY = self.SECRET_KEY
        if not self.JWT_REFRESH_SECRET_KEY:
            self.JWT_REFRESH_SECRET_KEY = self.REFRESH_SECRET_KEY

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
