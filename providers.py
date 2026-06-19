"""
Capa de proveedores.

La API expone SIEMPRE el mismo contrato (status / chat), sin importar si por debajo
está OpenRouter o, en el futuro, Bedrock. Cuando AWS entregue el modelo y la key,
solo se implementa BedrockProvider y se cambia PROVIDER en .env.

PASSTHROUGH PURO: para OpenRouter el body del cliente se reenvía EXACTAMENTE como
llega (mismos parámetros, mismos archivos). No se inyecta modelo, ni system prompt,
ni RAG, ni nada. Es como conectarse directo a OpenRouter.
"""
from __future__ import annotations

import time
from typing import Any, AsyncIterator

import httpx

from config import settings


class ProviderError(Exception):
    """Error normalizado para que main.py lo traduzca a una respuesta HTTP."""

    def __init__(self, status_code: int, detail: Any):
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


class BaseProvider:
    name = "base"

    async def status(self) -> dict:
        raise NotImplementedError

    async def chat(self, payload: dict) -> dict:
        raise NotImplementedError

    async def chat_stream(self, payload: dict) -> AsyncIterator[bytes]:
        raise NotImplementedError
        yield b""  # pragma: no cover  (mantiene la firma de async generator)


# --------------------------------------------------------------------------- #
# OpenRouter
# --------------------------------------------------------------------------- #
class OpenRouterProvider(BaseProvider):
    name = "openrouter"

    def __init__(self) -> None:
        self.base_url = settings.openrouter_base_url.rstrip("/")
        self.api_key = settings.openrouter_api_key
        self.default_model = settings.default_model

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if settings.app_url:
            headers["HTTP-Referer"] = settings.app_url
        if settings.app_name:
            headers["X-Title"] = settings.app_name
        return headers

    async def status(self) -> dict:
        """Estado del proveedor + datos de la key y del modelo por defecto."""
        info: dict[str, Any] = {
            "provider": self.name,
            "base_url": self.base_url,
            "default_model": self.default_model,
            "reachable": False,
        }

        async with httpx.AsyncClient(timeout=15) as client:
            t0 = time.perf_counter()
            try:
                resp = await client.get(f"{self.base_url}/auth/key", headers=self._headers())
                info["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                if resp.status_code == 200:
                    info["reachable"] = True
                    body = resp.json()
                    data = body.get("data", body)
                    info["key"] = {
                        "label": data.get("label"),
                        "usage": data.get("usage"),
                        "limit": data.get("limit"),
                        "limit_remaining": data.get("limit_remaining"),
                        "is_free_tier": data.get("is_free_tier"),
                        "rate_limit": data.get("rate_limit"),
                    }
                else:
                    info["error"] = f"/auth/key respondió {resp.status_code}"
            except Exception as exc:  # noqa: BLE001
                info["error"] = str(exc)

            try:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
                if resp.status_code == 200:
                    models = resp.json().get("data", [])
                    match = next(
                        (m for m in models if m.get("id") == self.default_model), None
                    )
                    if match:
                        arch = match.get("architecture") or {}
                        info["model"] = {
                            "id": match.get("id"),
                            "name": match.get("name"),
                            "context_length": match.get("context_length"),
                            "pricing": match.get("pricing"),
                            "input_modalities": arch.get("input_modalities"),
                        }
            except Exception:  # noqa: BLE001
                pass

        return info

    async def chat(self, payload: dict) -> dict:
        """Reenvía el body tal cual y devuelve la respuesta cruda del modelo."""
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                raise ProviderError(resp.status_code, _safe_json(resp))
            return resp.json()

    async def chat_stream(self, payload: dict) -> AsyncIterator[bytes]:
        """Reenvía el body tal cual y retransmite el SSE del modelo sin tocarlo."""
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise ProviderError(resp.status_code, body.decode("utf-8", "ignore"))
                async for chunk in resp.aiter_raw():
                    if chunk:
                        yield chunk


# --------------------------------------------------------------------------- #
# Bedrock (stub — pendiente de credenciales de AWS)
# --------------------------------------------------------------------------- #
class BedrockProvider(BaseProvider):
    name = "bedrock"

    def __init__(self) -> None:
        self.region = settings.aws_region
        self.model_id = settings.bedrock_model_id

    async def status(self) -> dict:
        return {
            "provider": self.name,
            "region": self.region,
            "model_id": self.model_id or None,
            "reachable": False,
            "error": "Proveedor Bedrock aún no configurado (faltan key/modelo de AWS).",
        }

    async def chat(self, payload: dict) -> dict:
        raise ProviderError(
            503, "Bedrock todavía no implementado. Usa PROVIDER=openrouter por ahora."
        )

    async def chat_stream(self, payload: dict) -> AsyncIterator[bytes]:
        raise ProviderError(
            503, "Bedrock todavía no implementado. Usa PROVIDER=openrouter por ahora."
        )
        yield b""  # pragma: no cover


def get_provider() -> BaseProvider:
    if settings.provider == "bedrock":
        return BedrockProvider()
    return OpenRouterProvider()