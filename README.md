# LLM API

API intermediaria entre tu aplicación y un LLM, más una interfaz de chat web para probarla.

La API actúa como **passthrough**: recibe la petición del cliente y la reenvía al proveedor
del modelo **sin modificar el contenido** (no inyecta prompts, no agrega contexto, no fuerza
formato). Soporta dos proveedores, intercambiables con una variable de entorno:

- **OpenRouter** (acceso a muchos modelos con una sola API key).
- **AWS Bedrock** (modelos como Amazon Nova, vía Bedrock API key).

La respuesta de ambos se normaliza al mismo formato, así que tu aplicación se programa una
sola vez sin importar el proveedor.

---

## Características

- Endpoint de chat passthrough (texto + archivos), con respuesta completa o en streaming.
- Soporte de **PDF e imágenes** enviados directamente al modelo (no se convierten a texto).
- Conteo de **tokens por consulta** (entrada/salida) y **total acumulado**, con costo estimado.
- Reintentos automáticos ante errores transitorios (429 / 5xx).
- Sin límites artificiales de tokens por defecto.
- Interfaz de chat incluida: adjuntar archivos (incluido arrastrar y soltar), streaming,
  cancelar solicitud, panel de estado/consumo, ocultar el panel lateral.

---

## Estructura

```
llm-gateway/
├── main.py            # API FastAPI: /api/status, /api/usage, /api/chat, /healthz
├── providers.py       # Proveedores: OpenRouter y Bedrock (traducción + normalización)
├── config.py          # Configuración leída desde .env
├── requirements.txt   # Dependencias
├── .env.example       # Plantilla de variables (cópiala a .env)
├── API.md             # Documentación detallada de la API (con ejemplos)
└── front/
    └── index.html     # Interfaz de chat estática
```

---

## Requisitos

- Python 3.10 o superior.
- Una API key del proveedor que vayas a usar (OpenRouter o Bedrock).

---

## Instalación

```bash
git clone <URL_DEL_REPO> llm-gateway
cd llm-gateway

# (opcional pero recomendado) entorno virtual
python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate

pip install -r requirements.txt
```

> `boto3` solo se usa si eliges Bedrock; viene incluido en `requirements.txt`.

---

## Configuración

Copia la plantilla y edítala:

```bash
cp .env.example .env
```

Elige el proveedor con `PROVIDER` y completa solo el bloque correspondiente.

### Opción A — OpenRouter

```dotenv
PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-tu-key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=qwen/qwen2.5-vl-72b-instruct
```

| Variable | Descripción |
|---|---|
| `OPENROUTER_API_KEY` | Tu API key de OpenRouter. |
| `OPENROUTER_BASE_URL` | URL base (normalmente no se cambia). |
| `OPENROUTER_MODEL` | Modelo por defecto (formato `proveedor/modelo`). |

### Opción B — AWS Bedrock

```dotenv
PROVIDER=bedrock
AWS_REGION=us-east-1
AWS_BEARER_TOKEN_BEDROCK=tu-bedrock-api-key-que-termina-en=
BEDROCK_MODEL_ID=amazon.nova-lite-v1:0
BEDROCK_MAX_OUTPUT_TOKENS=5000
```

| Variable | Descripción |
|---|---|
| `AWS_REGION` | Región de Bedrock (ej. `us-east-1`). |
| `AWS_BEARER_TOKEN_BEDROCK` | La **Bedrock API key** (el string largo que termina en `=`). No se usa IAM access key/secret. |
| `BEDROCK_MODEL_ID` | ID del modelo. En EE.UU. suele necesitar prefijo cross-region, ej. `us.amazon.nova-lite-v1:0`. |
| `BEDROCK_MAX_OUTPUT_TOKENS` | Tope de salida por defecto. Bedrock corta con un default bajo si no se especifica; súbelo hasta el máximo del modelo (Nova 1 Lite ≈ 5000; Nova 2 hasta 65000). |

### Variables comunes (opcionales)

| Variable | Por defecto | Descripción |
|---|---|---|
| `CORS_ORIGINS` | `["*"]` | Orígenes permitidos para llamar a la API. |
| `MAX_RETRIES` | `3` | Reintentos extra ante errores transitorios. |
| `RETRY_BASE_DELAY` | `1.0` | Espera base entre reintentos (segundos, crece exponencial). |
| `REQUEST_TIMEOUT` | `600` | Timeout para respuestas sin streaming (el streaming no tiene timeout). |
| `PRICE_INPUT_PER_1M` | `0.06` | Precio de entrada (USD/1M tokens) para estimar costo. |
| `PRICE_OUTPUT_PER_1M` | `0.24` | Precio de salida (USD/1M tokens) para estimar costo. |
| `APP_NAME`, `APP_URL` | — | Atribución que envía OpenRouter (opcional). |

---

## Ejecutar

```bash
python main.py
```

Levanta en `http://localhost:8000` (con autorecarga). Equivalente:

```bash
uvicorn main:app --reload
```

Abre `http://localhost:8000` en el navegador para usar la interfaz de chat.

---

## Uso

### Interfaz web

Escribe en el cuadro inferior y envía. Puedes adjuntar **PDF e imágenes** (con el clip o
arrastrándolos sobre el input) para que el modelo los analice. El panel lateral muestra el
estado del proveedor, los tokens de la última consulta y el total de la sesión con costo
estimado. Mientras responde, el botón de enviar se convierte en **cancelar**.

### API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/status` | Estado del proveedor, modelo y (OpenRouter) crédito restante. |
| `GET` | `/api/usage` | Consumo total acumulado: tokens y costo estimado. |
| `POST` | `/api/chat` | Envía una instrucción (+ archivos) y devuelve la respuesta. |
| `GET` | `/healthz` | Chequeo de salud liviano. |

Ejemplo mínimo:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "amazon.nova-lite-v1:0",
    "messages": [{ "role": "user", "content": "Hola, ¿qué puedes hacer?" }]
  }'
```

La documentación completa (formato de mensajes, archivos, streaming vs normal, parámetros,
respuestas y más ejemplos en cURL/Python/JavaScript) está en **[API.md](API.md)**.

---

## Cambiar de proveedor

Solo cambia `PROVIDER` en el `.env` (a `openrouter` o `bedrock`) y completa el bloque
correspondiente. La aplicación que consume la API **no cambia**: el formato de petición y de
respuesta es el mismo en ambos casos.

---

## Notas importantes

- **Sin límites por defecto:** no se envía `max_tokens` salvo que lo indiques. En OpenRouter
  eso deja que el modelo responda hasta terminar. En Bedrock, como su default es bajo, se usa
  `BEDROCK_MAX_OUTPUT_TOKENS` para no cortar respuestas largas.
- **Costo:** el costo mostrado es **estimado** (tokens × precio configurado). El costo real y
  oficial de Bedrock está en AWS Cost Explorer; el de OpenRouter, en su panel.
- **Tokens reales:** los tokens de entrada/salida son los que reporta el proveedor, no un cálculo.
- **Archivos:** PDF e imágenes se envían tal cual al modelo. Word/Excel no se entienden de
  forma nativa; conviértelos a texto o PDF antes.
- **El `.env` no se sube al repo** (está en `.gitignore`); cada quien crea el suyo a partir de
  `.env.example`.
- **Modelo sugerido en Bedrock:** `amazon.nova-lite-v1:0` (multimodal y económico). Para
  respuestas muy largas, conviene Nova 2 Lite (`us.amazon.nova-2-lite-v1:0`, hasta 65000 tokens
  de salida) subiendo `BEDROCK_MAX_OUTPUT_TOKENS`.
