"""Application configuration loaded from environment variables / .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Defaults target a host-machine run; Docker Compose overrides the URLs
    with the `postgres` / `redis` service names for the api container."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+psycopg2://ate:ate_dev_password@localhost:5432/ate"
    redis_url: str = "redis://localhost:6379/0"

    # Offline GeoLite2 database file (placed manually; never auto-downloaded).
    geoip_db_path: str = "data/geoip/GeoLite2-City.mmdb"

    # Localhost development origins only (React dashboard connects here later).
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


settings = Settings()
