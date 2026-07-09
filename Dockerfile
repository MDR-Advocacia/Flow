# ─── Stage 1: base — pesado, compartilhado entre api e ajus-runner ────
# Tudo que api e ajus-runner têm em comum mora aqui. BuildKit constrói
# essa stage UMA vez e reaproveita pra ambos os targets, mesmo quando
# Coolify dispara `docker compose build` em paralelo.
#
# Antes (Dockerfile.ajus-runner separado): ~1GB de apt+pip+chromium
# duplicado em paralelo, estourava o build do Coolify (exit 255).
# Agora: stage `base` única, ajus-runner só adiciona o pacote Python
# `playwright` em cima.
FROM python:3.10-slim AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
# Playwright browsers cache (compartilhado entre Node.js e Python)
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN mkdir -p /app/data

# Base utilities needed to fetch Node and run Chromium.
# tesseract-ocr(+por): OCR das páginas-imagem/escaneadas na Análise Recursal
# (só roda nos documentos que saem sem texto do pdfplumber). ~80MB.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg \
        tesseract-ocr tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20 (LTS) via NodeSource — needed by the Playwright runner
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── Layer de Playwright (cacheada) ───────────────────────────────
# Copia SÓ os arquivos de dependência do runner. Assim a instalação
# pesada do Chromium (5-8min) só roda de novo quando package*.json mudam,
# não em toda alteração de código Python.
#
# `--with-deps` aqui também instala TODAS as libs apt do Chromium
# (libnss3, libatk1.0-0, libgbm1, etc.) — então o ajus-runner não
# precisa reinstalar nada disso depois.
COPY app/runners/legalone/package.json app/runners/legalone/package-lock.json /app/app/runners/legalone/
RUN cd /app/app/runners/legalone \
    && npm ci --omit=dev \
    && npx --yes playwright install --with-deps chromium

# ─── Código da aplicação (layer leve) ─────────────────────────────
COPY . .


# ─── Stage 2: api ─────────────────────────────────────────────────
# Imagem servida pelo Uvicorn, escutando 8000.
FROM base AS api

# ── Playwright Python + Xvfb (coleta do Distribuídos BB) ──────────────
# O portal do BB bloqueia headless, então a coleta usa Chromium
# NÃO-headless sob Xvfb (display virtual). O Chromium já veio da base
# (`npx playwright install --with-deps chromium`); o pacote Python do
# Playwright (mesma linha ^1.59 do runner Node) REUSA esse mesmo binário
# via PLAYWRIGHT_BROWSERS_PATH=/ms-playwright — sem download extra.
# `xvfb` provê o binário Xvfb usado pelo docker-api-start.sh.
RUN apt-get update && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright==1.59.0

RUN chmod +x /app/scripts/docker-api-start.sh

EXPOSE 8000

CMD ["/app/scripts/docker-api-start.sh"]


# ─── Stage 3: ajus-runner ─────────────────────────────────────────
# Reaproveita a stage `base` inteira (apt + pip + npm + Chromium +
# libs do sistema + código). Só adiciona o pacote Python `playwright`
# pra rodar `app/services/ajus/classif_runner.py`.
#
# OBS sobre Chromium: o `npx playwright install --with-deps chromium`
# da stage base baixa Chromium da versão Node (^1.59.0). O Python
# `playwright==1.47.0` espera uma revisão diferente, então rodamos
# `playwright install chromium` (sem --with-deps; libs do sistema já
# vieram da stage base) pra baixar a revisão Python. Isso são ~150MB
# extras, contra ~600MB de duplicação total que tinha antes.
FROM base AS ajus-runner

RUN pip install --no-cache-dir playwright==1.47.0
RUN playwright install chromium

RUN mkdir -p /app/data/ajus-session

CMD ["python", "scripts/ajus_runner_worker.py"]
