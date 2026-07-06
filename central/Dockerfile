# HexCast central control plane — Cloud Run image.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY store.py main.py update_manifest.json landing.html ./
COPY logo.png ./

# Cloud Run provides $PORT (default 8080).
ENV PORT=8080
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
