FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencias primero (mejor cache de capas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código
COPY . .

EXPOSE 8000

# 1 worker: el contador de uso vive en memoria y debe ser consistente.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
