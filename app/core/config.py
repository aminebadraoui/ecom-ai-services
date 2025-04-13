from pydantic_settings import BaseSettings
from typing import Optional
import os

class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "AI Extraction Service"
    
    # OpenAI API Key should be set in environment variables
    OPENAI_API_KEY: Optional[str] = os.environ.get("OPENAI_API_KEY")
    
    # Redis settings
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    
    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    class Config:
        env_file = ".env"
        extra = "allow"  # Allow extra fields from Coolify environment

settings = Settings() 