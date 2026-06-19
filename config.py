"""
Configuración central del gateway.

Variables separadas por proveedor para no tener que reescribirlas al cambiar.
Se elige el proveedor con PROVIDER (openrouter | bedrock).
"""
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Proveedor activo: "openrouter" o "bedrock".
    provider: str = "openrouter"

    # ----- OpenRouter -----
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "qwen/qwen2.5-vl-72b-instruct"  # modelo por defecto

    # Cabeceras de atribución de OpenRouter (opcionales).
    app_name: str = "LLM Gateway"
    app_url: str = "http://localhost:8000"

    # ----- AWS Bedrock -----
    # Bedrock se conecta SOLO con región + Bedrock API key (no se usa IAM access key/secret).
    aws_region: str = "us-east-1"
    # Bedrock API key: el string largo que termina en "=".
    aws_bearer_token_bedrock: str = ""
    bedrock_model_id: str = "amazon.nova-lite-v1:0"   # modelo por defecto

    # ----- CORS -----
    cors_origins: List[str] = ["*"]

    # ----- Reintentos automáticos ante errores transitorios (429 / 5xx) -----
    max_retries: int = 3
    retry_base_delay: float = 1.0

    # Timeout (segundos) para respuestas SIN streaming. El streaming no tiene timeout.
    request_timeout: float = 600.0

    # Precio del modelo activo (USD por 1M de tokens) para ESTIMAR costo.
    # Por defecto: Amazon Nova Lite. Ajústalo al precio de tu modelo.
    price_input_per_1m: float = 0.06
    price_output_per_1m: float = 0.24


settings = Settings()