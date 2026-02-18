"""
Application configuration management using Pydantic Settings.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, computed_field
from typing import List
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Application
    APP_NAME: str = "Surgical Analysis Platform"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    RELOAD: bool = True
    
    # MongoDB
    MONGODB_URL: str
    MONGODB_DB_NAME: str = "surgical_analysis"
    MONGODB_MIN_POOL_SIZE: int = 10
    MONGODB_MAX_POOL_SIZE: int = 50
    
    # Google Cloud
    GOOGLE_CLOUD_PROJECT: str = "nins-dev"
    GOOGLE_APPLICATION_CREDENTIALS: str = Field(default="/home/com-028/Desktop/TRT/PROJ/files/surgical-analysis-platform/nins-dev-caec10880a35.json")
    VERTEX_AI_LOCATION: str = "us-central1"
    
    # Gemini API
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_TEMPERATURE: float = 0.1
    GEMINI_MAX_OUTPUT_TOKENS: int = 880192
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # # CORS
    # ALLOWED_ORIGINS: List[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    # ALLOWED_METHODS: List[str] = Field(default_factory=lambda: ["*"])
    # ALLOWED_HEADERS: List[str] = Field(default_factory=lambda: ["*"])
    
    # File Upload
    MAX_UPLOAD_SIZE_MB: int = 500
    # Accepts CSV string → mp4,avi,mov,mkv
    ALLOWED_VIDEO_FORMATS_STR: str = Field(default="mp4,avi,mov,mkv", validation_alias="ALLOWED_VIDEO_FORMATS")
    
    @computed_field
    @property
    def ALLOWED_VIDEO_FORMATS(self) -> List[str]:
        """Returns video formats as a list."""
        return [fmt.strip() for fmt in self.ALLOWED_VIDEO_FORMATS_STR.split(',') if fmt.strip()]

    # Cloud Storage
    GCS_BUCKET_NAME: str = "surgical-videos"

    
    # WebSocket
    WS_HEARTBEAT_INTERVAL: int = 30
    WS_MAX_MESSAGE_SIZE: int = 10485760  # 10MB
    
    # Monitoring
    SENTRY_DSN: str = ""
    ENABLE_PERFORMANCE_MONITORING: bool = False
    
    # Feature Flags
    ENABLE_VIDEO_ANALYSIS: bool = True
    ENABLE_REAL_TIME_MONITORING: bool = True
    ENABLE_ALERTS: bool = True
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        env_parse_none_str="null",
        populate_by_name=True,
        extra="ignore"
    )


# Global settings instance
settings = Settings()
