from __future__ import annotations

import os
from pathlib import Path

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

        gemini_api_key: str = ""
        anthropic_api_key: str = ""
        supabase_url: str = ""
        supabase_service_key: str = ""
        database_url: str = ""
        redis_url: str = "redis://localhost:6379/0"
        telegram_bot_token: str = ""
        telegram_allowed_chat_id: int = 0
        gmail_address: str = ""
        gmail_app_password: str = ""
        profiles_base_dir: str = "~/.autoapply/profiles"
        default_browser_engine: str = "patchright"
        profile_vault_path: str = "data/profile_vault.json"
        vnc_preview_url: str = "http://127.0.0.1:6080/vnc.html"
        supervised_mode_default: bool = True
        match_score_threshold: float = 80.0
        confidence_threshold: float = 0.85
        max_daily_linkedin: int = 32
        max_daily_naukri: int = 42
        max_daily_direct_ats: int = 100

        def get_profile_dir(self, portal: str) -> Path:
            base = Path(self.profiles_base_dir).expanduser().resolve()
            portal_dir = base / portal
            portal_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            return portal_dir

except ImportError:
    from dataclasses import dataclass

    @dataclass
    class Settings:  # type: ignore[no-redef]
        gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
        anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
        supabase_url: str = os.getenv("SUPABASE_URL", "")
        supabase_service_key: str = os.getenv("SUPABASE_SERVICE_KEY", "")
        database_url: str = os.getenv("DATABASE_URL", "")
        redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        telegram_allowed_chat_id: int = int(os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "0"))
        gmail_address: str = os.getenv("GMAIL_ADDRESS", "")
        gmail_app_password: str = os.getenv("GMAIL_APP_PASSWORD", "")
        profiles_base_dir: str = os.getenv("PROFILES_BASE_DIR", "~/.autoapply/profiles")
        default_browser_engine: str = os.getenv("DEFAULT_BROWSER_ENGINE", "patchright")
        profile_vault_path: str = os.getenv("PROFILE_VAULT_PATH", "data/profile_vault.json")
        vnc_preview_url: str = os.getenv("VNC_PREVIEW_URL", "http://127.0.0.1:6080/vnc.html")
        supervised_mode_default: bool = True
        match_score_threshold: float = 80.0
        confidence_threshold: float = 0.85
        max_daily_linkedin: int = 32
        max_daily_naukri: int = 42
        max_daily_direct_ats: int = 100

        def get_profile_dir(self, portal: str) -> Path:
            base = Path(self.profiles_base_dir).expanduser().resolve()
            portal_dir = base / portal
            portal_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            return portal_dir


settings = Settings()
