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
    local_test_database_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LOCAL_TEST_DATABASE_URL")
    )
    test_supervisor_email: str = "supervisor.test@snmills.com"
    test_supervisor_password: str = ""
    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("ENVIRONMENT", "APP_ENV")
    )
    secret_key: str = "dev-secret-key"
    encryption_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("ENCRYPTION_KEY", "TOKEN_ENCRYPTION_KEY")
    )
    linkedin_client_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LINKEDIN_CLIENT_ID")
    )
    linkedin_client_secret: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LINKEDIN_CLIENT_SECRET")
    )
    linkedin_redirect_uri: str = Field(
        default="http://localhost:8000/auth/linkedin/callback",
        validation_alias=AliasChoices("LINKEDIN_REDIRECT_URI")
    )
    linkedin_company_page_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LINKEDIN_COMPANY_PAGE_ID", "LINKEDIN_ORGANIZATION_ID")
    )
    gemini_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY")
    )
    gemini_image_model: str = Field(
        default="gemini-3.1-flash-image",
        validation_alias=AliasChoices("GEMINI_IMAGE_MODEL")
    )

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