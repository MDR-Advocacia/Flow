#!/bin/sh
set -eu

mkdir -p /app/data

# ── Display virtual pro Playwright NÃO-headless (Distribuídos BB) ──────
# O portal do BB (PAJ) bloqueia Chromium headless (anti-bot), então a
# coleta roda não-headless sob um Xvfb persistente. Sobe o display :99 e
# exporta DISPLAY; se o Xvfb não estiver instalado, segue sem travar o
# boot (a coleta avisa que precisa de display).
if command -v Xvfb >/dev/null 2>&1; then
    Xvfb :99 -screen 0 1920x1080x24 >/tmp/xvfb.log 2>&1 &
    export DISPLAY=:99
    echo "[start] Xvfb :99 iniciado (DISPLAY=:99) para a coleta do BB."
else
    echo "[start] AVISO: Xvfb não encontrado — a coleta do BB (não-headless) não funcionará."
fi

python /app/scripts/run_migrations.py

# UVICORN_WORKERS permite overrride via painel do Coolify.
# Regra de bolso: 2-4 por vCPU, limitado pela RAM (cada worker replica o
# Python + engine do SQLAlchemy). Em EC2 com 4 vCPUs use 4; em 8, 6-8.
WORKERS="${UVICORN_WORKERS:-4}"

exec python -m uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "$WORKERS" \
    --proxy-headers \
    --forwarded-allow-ips="*"
