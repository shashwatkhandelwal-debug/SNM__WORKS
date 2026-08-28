from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    supabase_url: str = "https://eayrmjmzjokeeuwazmjy.supabase.co"
    supabase_key: str = "sb_publishable_MXk1JNE2Fb77myKjBfNLMg_i24vbA2k"
    database_url: Optional[str] = None
    test_supervisor_email: str = "supervisor.test@snmills.com"
    test_supervisor_password: str = ""
    app_env: str = "development"
    secret_key: str = "dev-secret-key"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()