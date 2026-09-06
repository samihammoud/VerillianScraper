from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    rapidapi_key: str
    rapidapi_host: str
    database_url: str
    openai_api_key: str
    gemini_api_key: str
    google_application_credentials: str  # service account JSON path; used for register_files() OAuth, not the api_key path
    gcs_bucket: str  # bucket name only, e.g. "my-project-videos" — not a gs:// url

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
