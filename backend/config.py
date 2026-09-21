import os
from pathlib import Path
from typing import List, Optional

try:
    from pydantic_settings import BaseSettings
    from pydantic import Field

    class Settings(BaseSettings):
        GEMINI_API_KEY: str = Field(default="")
        MAX_RETRIES: int = Field(default=2)
        DEFAULT_TIMEOUT_SECONDS: int = Field(default=60)
        MAX_STEP_TIME: int = Field(default=120)
        MAX_WORKFLOW_TIME: int = Field(default=600)
        MAX_OUTPUT_SIZE: int = Field(default=1_000_000)
        MAX_RECOVERY_ACTIONS: int = Field(default=3)
        WORKSPACE_BASE_DIR: str = Field(
            default=str(Path(__file__).resolve().parent.parent / "workspaces"),
        )
        DATABASE_PATH: str = Field(
            default=str(Path(__file__).resolve().parent.parent / "agentguard.db"),
        )
        USE_SQLITE_PERSISTENCE: bool = Field(default=True)
        CORS_ORIGINS: List[str] = ["*"]
        MOCK_VERIFIER: bool = Field(default=True)
        MEMBER3_VERIFIER_URL: Optional[str] = Field(default=None)

        model_config = {
            "env_file": ".env",
            "env_file_encoding": "utf-8",
            "extra": "ignore",
        }

    settings = Settings()

except ImportError:
    # Fallback if pydantic-settings is not yet installed
    class SimpleSettings:
        def __init__(self):
            self.GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
            self.MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "2"))
            self.DEFAULT_TIMEOUT_SECONDS: int = int(os.getenv("DEFAULT_TIMEOUT_SECONDS", "60"))
            self.WORKSPACE_BASE_DIR: str = os.getenv(
                "WORKSPACE_BASE_DIR",
                str(Path(__file__).resolve().parent.parent / "workspaces"),
            )
            self.CORS_ORIGINS: List[str] = ["*"]
            self.MOCK_VERIFIER: bool = os.getenv("MOCK_VERIFIER", "true").lower() in ("true", "1", "yes")
            self.MEMBER3_VERIFIER_URL: Optional[str] = os.getenv("MEMBER3_VERIFIER_URL", None)

    settings = SimpleSettings()

# Ensure base workspaces directory exists
Path(settings.WORKSPACE_BASE_DIR).mkdir(parents=True, exist_ok=True)
