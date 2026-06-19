"""
main.py — el gateway (intermediario) en FastAPI.

Dos endpoints:
  GET  /api/status  -> estado del proveedor, datos de la key y del modelo.
  POST /api/chat    -> passthrough: recibe el body del usuario, lo reenvía al LLM
                       y devuelve la respuesta (soporta streaming SSE).

Además sirve el front estático (carpeta ./front) en la raíz "/".
"""
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


@app.get("/api/status")
async def status():
    """Información del LLM / proveedor: si responde, latencia, créditos, etc."""
    return JSONResponse(await provider.status())


@app.post("/api/chat")
async def chat(request: Request):
    """
    Intermediario puro. Reenvía EXACTAMENTE lo que llega del cliente al LLM.
    Si el body trae "stream": true, responde con un stream SSE.
    """
    body = await request.json()
    stream = bool(body.get("stream", False))

    try:
        if stream:
            agen = provider.chat_stream(body)
            # Pedimos el primer fragmento ANTES de abrir el StreamingResponse:
            # así un error del proveedor (p. ej. 401) se devuelve como JSON limpio
            # en vez de romper la respuesta a mitad de stream.
            try:
                first = await agen.__anext__()
            except StopAsyncIteration:
                first = None

            async def event_stream():
                if first is not None:
                    yield first
                async for chunk in agen:
                    yield chunk

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        result = await provider.chat(body)
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