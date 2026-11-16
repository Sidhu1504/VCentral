ARG VCENTRAL_VERSION=0.5.1

# --- build the web UI -------------------------------------------------------------------------
FROM node:20.18.0-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- build the agent for every platform -------------------------------------------------------
# Go 1.20 is the last release that runs on Windows Server 2008 R2 / 2012 / 2012 R2 ("windows-legacy").
FROM golang:1.20.14 AS go120
FROM golang:1.23.2 AS agent
ARG VCENTRAL_VERSION
COPY --from=go120 /usr/local/go /usr/local/go120
WORKDIR /src/agent
COPY agent/go.mod agent/go.sum ./
RUN go mod download
COPY agent/ ./
RUN ./build.sh /out "$VCENTRAL_VERSION" /usr/local/go120/bin/go

# --- API + worker image -----------------------------------------------------------------------
FROM python:3.12.7-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    VCENTRAL_STATIC_DIR=/app/static \
    VCENTRAL_AGENT_DIR=/app/agent-bin \
    VCENTRAL_TLS_CERT_FILE=/certs/vcentral.crt \
    VCENTRAL_TASK_SIGNING_KEY_FILE=/keys/task-signing.key
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=ui /ui/dist ./static
COPY --from=agent /out ./agent-bin
RUN useradd --system --uid 10001 vcentral
USER vcentral
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
