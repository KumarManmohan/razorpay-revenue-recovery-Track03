import os
from typing import List
from dotenv import load_dotenv

# Load key-value pairs from .env file into os.environ
load_dotenv()


class Settings:
    """Application configuration loaded from environment variables."""
    PORT: int = int(os.getenv("PORT", 8000))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    
    # Razorpay configurations
    RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")
    RAZORPAY_WEBHOOK_SECRET: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    
    # Security & API configurations
    MERCHANT_API_KEY: str = os.getenv("MERCHANT_API_KEY", "")
    ALLOWED_ORIGINS: str = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    )
    MAX_REQUEST_BODY_SIZE_BYTES: int = int(os.getenv("MAX_REQUEST_BODY_SIZE_BYTES", 524288))  # 512 KB
    
    # LLM configurations
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Retry Exhaustion & Stopping Rules configurations
    MAX_FAILED_ATTEMPTS: int = int(os.getenv("MAX_FAILED_ATTEMPTS", 3))
    MAX_IGNORED_RECOVERY_LINKS: int = int(os.getenv("MAX_IGNORED_RECOVERY_LINKS", 2))
    IGNORED_RECOVERY_TIMEOUT_HOURS: int = int(os.getenv("IGNORED_RECOVERY_TIMEOUT_HOURS", 48))

    @property
    def cors_origins(self) -> List[str]:
        """Parses comma-separated allowed origins into a clean list."""
        if not self.ALLOWED_ORIGINS:
            return ["http://localhost:5173", "http://127.0.0.1:5173"]
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]


settings = Settings()
