"""
main.py — el gateway en FastAPI.

Endpoints:
  GET  /api/status  -> estado del proveedor (conexión, modelo, latencia, crédito/región).
  GET  /api/usage   -> consumo total de tokens acumulado desde que arrancó el servidor.
  POST /api/chat    -> passthrough: reenvía el body al LLM y devuelve la respuesta
                       (JSON o stream SSE). Registra el consumo de cada consulta.

Sirve además el front estático (carpeta ./front) en "/".
"""
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from providers import ProviderError, get_provider

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

provider = get_provider()


# --------------------------------------------------------------------------- #
# Contador de consumo (en memoria; se reinicia al reiniciar el servidor)
# --------------------------------------------------------------------------- #
class UsageTracker:
    def __init__(self) -> None:
        self.requests = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def record(self, usage: dict | None) -> None:
        self.requests += 1
        if usage:
            p = usage.get("prompt_tokens") or 0
            c = usage.get("completion_tokens") or 0
            t = usage.get("total_tokens") or (p + c)
            self.prompt_tokens += p
            self.completion_tokens += c
            self.total_tokens += t

    def snapshot(self) -> dict:
        cost = (self.prompt_tokens / 1_000_000) * settings.price_input_per_1m \
            + (self.completion_tokens / 1_000_000) * settings.price_output_per_1m
        return {
            "provider": provider.name,
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(cost, 6),
            "price_input_per_1m": settings.price_input_per_1m,
            "price_output_per_1m": settings.price_output_per_1m,
        }


usage_tracker = UsageTracker()


def _scan_usage(buffer: str):
    """Busca 'usage' en las líneas SSE completas. Devuelve (resto_incompleto, ultima_usage)."""
    last = None
    lines = buffer.split("\n")
    rest = lines.pop()  # la última línea puede estar incompleta
    for line in lines:
        s = line.strip()
        if not s.startswith("data:"):
            continue
        data = s[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
            if obj.get("usage"):
                last = obj["usage"]
        except Exception:
            pass
    return rest, last


@app.get("/healthz")
async def healthz():
    """Health-check liviano (no llama al proveedor). Para Docker/monitoreo."""
    return JSONResponse({"ok": True})


@app.get("/api/status")
async def status():
    return JSONResponse(await provider.status())


@app.get("/api/usage")
async def usage():
    """Consumo total de tokens acumulado (entrada, salida, total y nº de consultas)."""
    return JSONResponse(usage_tracker.snapshot())


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    stream = bool(body.get("stream", False))

    try:
        if stream:
            agen = provider.chat_stream(body)
            try:
                first = await agen.__anext__()
            except StopAsyncIteration:
                first = None

            async def event_stream():
                buf = ""
                last_usage = None
                try:
                    if first is not None:
                        buf += first.decode("utf-8", "ignore")
                        buf, u = _scan_usage(buf)
                        if u:
                            last_usage = u
                        yield first
                    async for chunk in agen:
                        buf += chunk.decode("utf-8", "ignore")
                        buf, u = _scan_usage(buf)
                        if u:
                            last_usage = u
                        yield chunk
                finally:
                    usage_tracker.record(last_usage)

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        result = await provider.chat(body)
        if isinstance(result, dict):
            usage_tracker.record(result.get("usage"))
        return JSONResponse(result)

    except ProviderError as exc:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


# El front estático se monta al final para no tapar las rutas /api/*.
FRONT_DIR = Path(__file__).parent / "front"
if FRONT_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONT_DIR), html=True), name="front")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)