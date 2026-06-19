"""
Capa de proveedores.

La API expone SIEMPRE el mismo contrato (status / chat / chat_stream), sin importar
si por debajo está OpenRouter o AWS Bedrock. Se elige con PROVIDER en el .env.

Importante sobre "no modificar nada":
- OpenRouter: el body se reenvía EXACTAMENTE como llega (passthrough literal).
- Bedrock: su API (Converse) tiene OTRO formato, así que es OBLIGATORIO traducir el
  "envoltorio" JSON. Pero el CONTENIDO no se toca: el texto de la instrucción va igual,
  y los archivos (PDF/imágenes) se mandan como BYTES crudos en bloques document/image,
  NO se convierten a texto. La respuesta de Bedrock se normaliza al mismo formato que
  devuelve OpenRouter, para que la app que consume la API no note la diferencia.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import threading
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


RETRY_STATUSES = {429, 500, 502, 503, 504}


def _retry_delay(attempt: int, response: httpx.Response | None = None) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
    return settings.retry_base_delay * (2 ** attempt) + random.uniform(0, 0.3)


class BaseProvider:
    name = "base"

    async def status(self) -> dict:
        raise NotImplementedError

    async def chat(self, payload: dict) -> dict:
        raise NotImplementedError

    async def chat_stream(self, payload: dict) -> AsyncIterator[bytes]:
        raise NotImplementedError
        yield b""  # pragma: no cover


# =========================================================================== #
# OpenRouter
# =========================================================================== #
class OpenRouterProvider(BaseProvider):
    name = "openrouter"

    def __init__(self) -> None:
        self.base_url = settings.openrouter_base_url.rstrip("/")
        self.api_key = settings.openrouter_api_key
        self.default_model = settings.openrouter_model

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
                    data = resp.json().get("data", {})
                    info["key"] = {
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
                    match = next((m for m in models if m.get("id") == self.default_model), None)
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
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            for attempt in range(settings.max_retries + 1):
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                if resp.status_code in RETRY_STATUSES and attempt < settings.max_retries:
                    await asyncio.sleep(_retry_delay(attempt, resp))
                    continue
                if resp.status_code >= 400:
                    raise ProviderError(resp.status_code, _safe_json(resp))
                return resp.json()

    async def chat_stream(self, payload: dict) -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=None) as client:
            for attempt in range(settings.max_retries + 1):
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                ) as resp:
                    if resp.status_code in RETRY_STATUSES and attempt < settings.max_retries:
                        await resp.aread()
                        await asyncio.sleep(_retry_delay(attempt, resp))
                        continue
                    if resp.status_code >= 400:
                        body = await resp.aread()
                        raise ProviderError(resp.status_code, body.decode("utf-8", "ignore"))
                    async for chunk in resp.aiter_raw():
                        if chunk:
                            yield chunk
                    return


# =========================================================================== #
# Bedrock — traducción de formato (OpenAI/OpenRouter  <->  Converse)
# =========================================================================== #
_IMG_FMT = {"image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg",
            "image/gif": "gif", "image/webp": "webp"}
_DOC_MIME = {"application/pdf": "pdf", "text/plain": "txt", "text/markdown": "md",
             "text/html": "html", "text/csv": "csv", "application/msword": "doc",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
             "application/vnd.ms-excel": "xls",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx"}
_DOC_EXT = {"pdf": "pdf", "txt": "txt", "md": "md", "html": "html", "csv": "csv",
            "doc": "doc", "docx": "docx", "xls": "xls", "xlsx": "xlsx"}
_STOP_MAP = {"end_turn": "stop", "max_tokens": "length", "stop_sequence": "stop",
             "content_filtered": "content_filter", "tool_use": "tool_calls"}


def _parse_data_url(url: str) -> tuple[str, bytes]:
    if not url.startswith("data:"):
        raise ProviderError(400, "Bedrock requiere los archivos en base64 (data URL), no URLs externas.")
    header, b64 = url.split(",", 1)
    mime = header[5:].split(";")[0] or "application/octet-stream"
    return mime, base64.b64decode(b64)


def _doc_format(mime: str, filename: str) -> str:
    if mime in _DOC_MIME:
        return _DOC_MIME[mime]
    ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    return _DOC_EXT.get(ext, "txt")


def _doc_name(filename: str) -> str:
    base = re.sub(r"\.[^.]+$", "", filename or "")
    base = re.sub(r"[^a-zA-Z0-9\s\-\(\)\[\]]", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base or "document"


def _to_converse(payload: dict, default_model: str) -> dict:
    """OpenAI/OpenRouter body -> kwargs de Converse. Solo adapta el envoltorio."""
    model_id = payload.get("model") or default_model
    system: list[dict] = []
    messages: list[dict] = []

    for msg in payload.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            if isinstance(content, str):
                system.append({"text": content})
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        system.append({"text": part.get("text", "")})
            continue

        blocks: list[dict] = []
        if isinstance(content, str):
            blocks.append({"text": content})
        elif isinstance(content, list):
            for part in content:
                ptype = part.get("type")
                if ptype == "text":
                    blocks.append({"text": part.get("text", "")})
                elif ptype == "image_url":
                    mime, data = _parse_data_url((part.get("image_url") or {}).get("url", ""))
                    blocks.append({"image": {"format": _IMG_FMT.get(mime, "png"),
                                             "source": {"bytes": data}}})
                elif ptype == "file":
                    f = part.get("file") or {}
                    mime, data = _parse_data_url(f.get("file_data", ""))
                    blocks.append({"document": {"format": _doc_format(mime, f.get("filename", "")),
                                                "name": _doc_name(f.get("filename", "document")),
                                                "source": {"bytes": data}}})
        messages.append({"role": role if role in ("user", "assistant") else "user",
                         "content": blocks})

    kwargs: dict[str, Any] = {"modelId": model_id, "messages": messages}
    if system:
        kwargs["system"] = system

    cfg: dict[str, Any] = {}
    if payload.get("max_tokens") is not None:
        cfg["maxTokens"] = int(payload["max_tokens"])
    if payload.get("temperature") is not None:
        cfg["temperature"] = float(payload["temperature"])
    if payload.get("top_p") is not None:
        cfg["topP"] = float(payload["top_p"])
    if payload.get("stop"):
        s = payload["stop"]
        cfg["stopSequences"] = s if isinstance(s, list) else [s]
    if cfg:
        kwargs["inferenceConfig"] = cfg
    return kwargs


def _converse_to_openai(resp: dict, model_id: str) -> dict:
    """Respuesta de Converse -> mismo formato que OpenRouter."""
    msg = resp.get("output", {}).get("message", {})
    text = "".join(b.get("text", "") for b in msg.get("content", []) if "text" in b)
    usage = resp.get("usage", {}) or {}
    return {
        "id": resp.get("ResponseMetadata", {}).get("RequestId", "bedrock"),
        "model": model_id,
        "object": "chat.completion",
        "choices": [{
            "index": 0,
            "message": {"role": msg.get("role", "assistant"), "content": text},
            "finish_reason": _STOP_MAP.get(resp.get("stopReason"), "stop"),
        }],
        "usage": {
            "prompt_tokens": usage.get("inputTokens"),
            "completion_tokens": usage.get("outputTokens"),
            "total_tokens": usage.get("totalTokens"),
        },
    }


def _event_to_sse(event: dict) -> bytes | None:
    """Evento de ConverseStream -> línea SSE con el MISMO formato que OpenRouter."""
    if "contentBlockDelta" in event:
        text = (event["contentBlockDelta"].get("delta") or {}).get("text")
        if text:
            data = {"choices": [{"index": 0, "delta": {"content": text}}]}
            return ("data: " + json.dumps(data) + "\n\n").encode("utf-8")
    if "metadata" in event:
        usage = event["metadata"].get("usage")
        if usage:
            data = {"choices": [{"index": 0, "delta": {}}],
                    "usage": {"prompt_tokens": usage.get("inputTokens"),
                              "completion_tokens": usage.get("outputTokens"),
                              "total_tokens": usage.get("totalTokens")}}
            return ("data: " + json.dumps(data) + "\n\n").encode("utf-8")
    return None


def _bedrock_error(exc: Exception) -> tuple[int, str]:
    try:
        from botocore.exceptions import ClientError
    except Exception:  # pragma: no cover
        ClientError = ()  # type: ignore
    if ClientError and isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        code = err.get("Code", "")
        msg = err.get("Message", str(exc))
        mapping = {
            "ThrottlingException": 429, "TooManyRequestsException": 429,
            "ValidationException": 400, "AccessDeniedException": 403,
            "UnrecognizedClientException": 401, "ResourceNotFoundException": 404,
            "ModelNotReadyException": 503, "ServiceUnavailableException": 503,
            "ModelTimeoutException": 504, "InternalServerException": 500,
            "ModelErrorException": 502,
        }
        return mapping.get(code, 500), f"{code}: {msg}" if code else msg
    return 500, str(exc)


# =========================================================================== #
# Bedrock provider
# =========================================================================== #
class BedrockProvider(BaseProvider):
    name = "bedrock"

    def __init__(self) -> None:
        self.region = settings.aws_region
        self.model_id = settings.bedrock_model_id
        self.bearer_token = settings.aws_bearer_token_bedrock

    def _client_kwargs(self) -> dict:
        # La Bedrock API key se entrega vía variable de entorno (así la lee boto3).
        if self.bearer_token:
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = self.bearer_token
        return {"region_name": self.region}

    def _runtime(self):
        import boto3
        from botocore.config import Config
        return boto3.client(
            "bedrock-runtime",
            config=Config(read_timeout=settings.request_timeout, retries={"max_attempts": 0}),
            **self._client_kwargs(),
        )

    def _control(self):
        import boto3
        return boto3.client("bedrock", **self._client_kwargs())

    async def status(self) -> dict:
        info: dict[str, Any] = {
            "provider": self.name,
            "region": self.region,
            "default_model": self.model_id,
            "reachable": False,
            "note": "Bedrock no expone saldo/crédito; el costo se ve en AWS Billing.",
        }
        t0 = time.perf_counter()
        try:
            def work():
                client = self._control()
                return client.list_foundation_models().get("modelSummaries", [])
            models = await asyncio.to_thread(work)
            info["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            info["reachable"] = True
            short = self.model_id.split(".")[-1]
            match = next((m for m in models if m.get("modelId") == self.model_id
                          or m.get("modelId", "").endswith(short)), None)
            if match:
                info["model"] = {
                    "id": match.get("modelId"),
                    "name": match.get("modelName"),
                    "input_modalities": match.get("inputModalities"),
                    "output_modalities": match.get("outputModalities"),
                }
        except Exception as exc:  # noqa: BLE001
            info["error"] = _bedrock_error(exc)[1]
        return info

    async def chat(self, payload: dict) -> dict:
        kwargs = _to_converse(payload, self.model_id)
        for attempt in range(settings.max_retries + 1):
            try:
                resp = await asyncio.to_thread(lambda: self._runtime().converse(**kwargs))
                return _converse_to_openai(resp, kwargs["modelId"])
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001
                status, msg = _bedrock_error(exc)
                if status in RETRY_STATUSES and attempt < settings.max_retries:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                raise ProviderError(status, msg)

    async def chat_stream(self, payload: dict) -> AsyncIterator[bytes]:
        kwargs = _to_converse(payload, self.model_id)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def worker():
            try:
                resp = self._runtime().converse_stream(**kwargs)
                for event in resp["stream"]:
                    loop.call_soon_threadsafe(queue.put_nowait, ("event", event))
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        threading.Thread(target=worker, daemon=True).start()

        while True:
            kind, item = await queue.get()
            if kind == "done":
                break
            if kind == "error":
                status, msg = _bedrock_error(item)
                raise ProviderError(status, msg)
            sse = _event_to_sse(item)
            if sse:
                yield sse
        yield b"data: [DONE]\n\n"


def get_provider() -> BaseProvider:
    if settings.provider == "bedrock":
        return BedrockProvider()
    return OpenRouterProvider()