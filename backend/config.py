import os
from pathlib import Path
from typing import Dict, List, Optional

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
        CORS_ORIGINS: List[str] = Field(default=["http://localhost:3000"])
        MOCK_VERIFIER: bool = Field(default=True)
        MEMBER3_VERIFIER_URL: Optional[str] = Field(default=None)
        VERIFY_TOKEN: Optional[str] = Field(default=None)
        VERIFY_HMAC_SECRET: str = Field(default="agentguard-hmac-secret-key-prod")
        REQUIRE_EVIDENCE_DIGEST: bool = Field(default=False)
        REQUIRE_AUTH: bool = Field(default=True)
        RATE_LIMIT_PER_MINUTE: int = Field(default=60)
        API_KEYS: Dict[str, str] = Field(
            default={
                "test-api-key": "default-owner",
                "tenant-a-secret-key-12345": "tenant-a",
                "tenant-b-secret-key-67890": "tenant-b",
                "admin-secret-key": "admin",
            }
        )

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
            self.MAX_STEP_TIME: int = int(os.getenv("MAX_STEP_TIME", "120"))
            self.MAX_WORKFLOW_TIME: int = int(os.getenv("MAX_WORKFLOW_TIME", "600"))
            self.MAX_OUTPUT_SIZE: int = int(os.getenv("MAX_OUTPUT_SIZE", "1000000"))
            self.MAX_RECOVERY_ACTIONS: int = int(os.getenv("MAX_RECOVERY_ACTIONS", "3"))
            self.WORKSPACE_BASE_DIR: str = os.getenv(
                "WORKSPACE_BASE_DIR",
                str(Path(__file__).resolve().parent.parent / "workspaces"),
            )
            self.DATABASE_PATH: str = os.getenv(
                "DATABASE_PATH",
                str(Path(__file__).resolve().parent.parent / "agentguard.db"),
            )
            self.USE_SQLITE_PERSISTENCE: bool = os.getenv("USE_SQLITE_PERSISTENCE", "true").lower() in ("true", "1", "yes")
            self.CORS_ORIGINS: List[str] = ["http://localhost:3000"]
            self.MOCK_VERIFIER: bool = os.getenv("MOCK_VERIFIER", "true").lower() in ("true", "1", "yes")
            self.MEMBER3_VERIFIER_URL: Optional[str] = os.getenv("MEMBER3_VERIFIER_URL", None)
            self.VERIFY_TOKEN: Optional[str] = os.getenv("VERIFY_TOKEN", "agentguard-verify-token-secret")
            self.VERIFY_HMAC_SECRET: str = os.getenv("VERIFY_HMAC_SECRET", "agentguard-hmac-secret-key-prod")
            self.REQUIRE_EVIDENCE_DIGEST: bool = os.getenv("REQUIRE_EVIDENCE_DIGEST", "false").lower() in ("true", "1", "yes")
            self.REQUIRE_AUTH: bool = os.getenv("REQUIRE_AUTH", "true").lower() in ("true", "1", "yes")
            self.RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
            self.API_KEYS: Dict[str, str] = {
                "test-api-key": "default-owner",
                "tenant-a-secret-key-12345": "tenant-a",
                "tenant-b-secret-key-67890": "tenant-b",
                "admin-secret-key": "admin",
            }

    settings = SimpleSettings()

# Ensure base workspaces directory exists
Path(settings.WORKSPACE_BASE_DIR).mkdir(parents=True, exist_ok=True)
