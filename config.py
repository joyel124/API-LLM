"""
Configuración central del gateway.

Lee variables desde el archivo .env (o variables de entorno del sistema).
Todo lo sensible (API keys) vive aquí, nunca en el código.
"""
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Proveedor activo: "openrouter" (ahora) o "bedrock" (más adelante).
    provider: str = "openrouter"

    # ----- OpenRouter -----
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Modelo por defecto. Este soporta PDF nativo (también sirven los de Gemini/OpenAI).
    default_model: str = "anthropic/claude-sonnet-4.5"

    # Cabeceras de atribución que pide OpenRouter (opcionales pero recomendadas).
    app_name: str = "LLM Gateway"
    app_url: str = "http://localhost:8000"

    # ----- Bedrock (placeholders para cuando AWS entregue las credenciales) -----
    aws_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    bedrock_model_id: str = ""

    # ----- CORS -----
    # Orígenes permitidos para que el front estático pueda llamar a la API.
    cors_origins: List[str] = ["*"]


settings = Settings()