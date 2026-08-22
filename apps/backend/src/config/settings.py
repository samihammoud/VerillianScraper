from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    rapidapi_key: str
    rapidapi_host: str = "tiktok-api15.p.rapidapi.com"
    database_url: str
    openai_api_key: str
    gemini_api_key: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
