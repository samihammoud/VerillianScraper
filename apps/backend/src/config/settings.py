from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    rapidapi_key: str
    rapidapi_host: str = "tiktok-api15.p.rapidapi.com"
    database_url: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
