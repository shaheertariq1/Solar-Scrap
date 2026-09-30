import os
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Solar Scrap API"
    ENV: str = "development"

    # Emulator Settings
    USE_EMULATOR: bool = True
    FIREBASE_PROJECT_ID: str = "solar-scrap-demo"
    FIREBASE_AUTH_EMULATOR_HOST: str = "localhost:9099"
    FIRESTORE_EMULATOR_HOST: str = "localhost:8080"
    FIREBASE_STORAGE_EMULATOR_HOST: str = "localhost:9199"
    FIREBASE_STORAGE_BUCKET: str = "solar-scrap-demo.appspot.com"

    # Production Firebase Settings
    FIREBASE_CREDENTIALS_PATH: str = ""
    FIREBASE_WEB_API_KEY: str = ""

    # Server Settings
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "*",
    ]

    # Meta / Facebook Lead Google Sheets (Sync Sources)
    META_LEAD_SHEET_URLS: List[str] = [
        "https://docs.google.com/spreadsheets/d/1kM9utaJQ0f94UxK-HYcUgcuOxxTm5-XnSMdfacDzTUY/export?format=csv&gid=0",
        "https://docs.google.com/spreadsheets/d/1mzU8XwBpEc7WW9-F8zhpQJxI52vpSbGogQPFc1mimsQ/export?format=csv&gid=0",
        "https://docs.google.com/spreadsheets/d/1Trk9Ugipzc78g5ND_dfUSzGEDyunb8TqOz7Bh7NKN5A/export?format=csv&gid=0",
        "https://docs.google.com/spreadsheets/d/1Trk9Ugipzc78g5ND_dfUSzGEDyunb8TqOz7Bh7NKN5A/export?format=csv&gid=1179489281",
    ]

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
