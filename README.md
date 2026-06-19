# LLM Gateway

API intermediaria (FastAPI) entre el usuario y un LLM. **No** ejecuta system prompts,
**no** agrega RAG ni conocimiento: recibe el request del cliente y lo reenvía tal cual
al modelo; la respuesta del modelo vuelve sin tocar. Hoy usa **OpenRouter**; mañana,
**Bedrock** (basta implementar el stub y cambiar una variable).

```
llm-gateway/
├── main.py            # FastAPI: /api/status y /api/chat
├── providers.py       # OpenRouter (activo) + Bedrock (stub)
├── config.py          # lee variables desde .env
├── requirements.txt
├── .env.example       # plantilla de variables
└── front/
    └── index.html     # interfaz de chat estática (estilo ChatGPT/Claude)
```

## Puesta en marcha

```bash
pip install -r requirements.txt
cp .env.example .env          # pega tu OPENROUTER_API_KEY dentro
python main.py                # arranca en http://localhost:8000
```

Abre `http://localhost:8000` → el front estático se sirve desde la misma API.

## Endpoints

### `GET /api/status`
Estado del proveedor y del modelo: si responde, latencia, contexto, uso y créditos
restantes de la key. (Para Bedrock devolverá un placeholder hasta tener credenciales.)

### `POST /api/chat`
Passthrough. El body es el formato estándar de chat (compatible OpenAI/OpenRouter):

```json
{
  "model": "anthropic/claude-sonnet-4.5",
  "messages": [{ "role": "user", "content": "Hola" }],
  "stream": true
}
```

- Si `stream: true` → responde un stream SSE (el gateway retransmite el de OpenRouter).
- Si `stream: false` → responde el JSON completo de la respuesta.
- El gateway reenvía **exactamente** lo que recibe (solo rellena `model` si falta).

## Archivos / PDF

PDFs e imágenes se mandan dentro de `messages[].content` como bloques:

```jsonc
"content": [
  { "type": "text", "text": "Resume este documento" },
  { "type": "file", "file": { "filename": "doc.pdf", "file_data": "data:application/pdf;base64,..." } }
]
```

Para que el modelo analice el **PDF directamente** (no texto extraído) se envía el plugin:

```json
"plugins": [{ "id": "file-parser", "pdf": { "engine": "native" } }]
```

El front ya hace esto automáticamente al adjuntar un PDF. Requiere un modelo con PDF
nativo: `anthropic/claude-sonnet-4.5`, `anthropic/claude-opus-4`, `google/gemini-2.5-pro`,
`openai/gpt-4o`. **Word y Excel no se entienden de forma nativa** → habría que convertir
a texto/PDF antes. `.txt` y `.md` se mandan como texto.

## Pasar a Bedrock (más adelante)

1. Implementar `BedrockProvider` en `providers.py` (la firma ya está lista).
   El equivalente del PDF nativo es el bloque `document` de la **Converse API** con los
   bytes crudos del archivo (límite ~10 MB por documento; modelos Claude lo soportan).
2. Poner credenciales en `.env` (`AWS_REGION`, `AWS_ACCESS_KEY_ID`, etc.).
3. Cambiar `PROVIDER=bedrock`. El front y los endpoints no cambian.

## Nota

Tener un intermediario que hoy "solo reenvía" es intencional: es la base sobre la que
después se montará RAG, prompts de sistema, herramientas, etc. El contrato de la API no
cambiará cuando eso llegue.