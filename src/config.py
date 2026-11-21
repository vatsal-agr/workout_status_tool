"""Configuration and environment management."""

import json
import os
from typing import Any, Dict, Optional
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings loaded from environment variables."""

    # Caliber Configuration
    CALIBER_REFRESH_TOKEN: str = os.getenv("CALIBER_REFRESH_TOKEN", "")
    CALIBER_MCP_URL: str = os.getenv("CALIBER_MCP_URL", "https://api.caliberstrong.com/mcp")
    CALIBER_CLIENT_ID: str = os.getenv("CALIBER_CLIENT_ID", "caliber-mcp")
    CALIBER_TOKEN_URL: str = os.getenv(
        "CALIBER_TOKEN_URL", "https://api.caliberstrong.com/oauth/token"
    )
    CALIBER_AUTH_URL: str = os.getenv(
        "CALIBER_AUTH_URL", "https://api.caliberstrong.com/oauth/authorize"
    )

    # Garmin Configuration
    GARMIN_PERSONAL_API_KEY: str = os.getenv("GARMIN_PERSONAL_API_KEY", "")
    GARMIN_API_BASE_URL: str = os.getenv(
        "GARMIN_API_BASE_URL", "https://apis.garmin.com"
    ).rstrip("/")

    # Google Sheets Configuration
    GOOGLE_SERVICE_ACCOUNT_FILE: Optional[str] = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    GOOGLE_SERVICE_ACCOUNT_JSON: Optional[str] = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")
    GOOGLE_SHEET_NAME: str = os.getenv("GOOGLE_SHEET_NAME", "Caliber_Daily_Sync")

    # GitHub Actions Secret Rotation
    GH_PAT: str = os.getenv("GH_PAT", "")
    GITHUB_REPOSITORY: str = os.getenv("GITHUB_REPOSITORY", "")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @classmethod
    def get_google_credentials_info(cls) -> Optional[Dict[str, Any]]:
        """Resolve Google Service Account credentials from JSON string or file path."""
        if cls.GOOGLE_SERVICE_ACCOUNT_JSON:
            try:
                return json.loads(cls.GOOGLE_SERVICE_ACCOUNT_JSON)
            except json.JSONDecodeError as err:
                raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON") from err

        if cls.GOOGLE_SERVICE_ACCOUNT_FILE and os.path.exists(cls.GOOGLE_SERVICE_ACCOUNT_FILE):
            with open(cls.GOOGLE_SERVICE_ACCOUNT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)

        return None


settings = Settings()
