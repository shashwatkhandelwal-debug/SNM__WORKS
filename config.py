from functools import lru_cache
from typing import Optional
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    supabase_url: str = "https://eayrmjmzjokeeuwazmjy.supabase.co"
    supabase_publishable_key: str = Field(
        default="sb_publishable_MXk1JNE2Fb77myKjBfNLMg_i24vbA2k",
        validation_alias=AliasChoices("SUPABASE_PUBLISHABLE_KEY", "SUPABASE_KEY")
    )
    supabase_jwt_secret: Optional[str] = None
    database_url: Optional[str] = None
    test_supervisor_email: str = "supervisor.test@snmills.com"
    test_supervisor_password: str = ""
    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("ENVIRONMENT", "APP_ENV")
    )
    secret_key: str = "dev-secret-key"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def supabase_key(self) -> str:
        return self.supabase_publishable_key


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()