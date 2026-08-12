#!/bin/sh
set -eu

mkdir -p /app/data

# ── Display virtual pro Playwright NÃO-headless (Distribuídos BB) ──────
# O portal do BB (PAJ) bloqueia Chromium headless (anti-bot), então a
# coleta roda não-headless sob um Xvfb persistente. Sobe o display :99 e
# exporta DISPLAY; se o Xvfb não estiver instalado, segue sem travar o
# boot (a coleta avisa que precisa de display).
if command -v Xvfb >/dev/null 2>&1; then
    # LOCK ORFAO: o Xvfb se recusa a subir se achar /tmp/.X99-lock de uma
    # execucao anterior ("Server is already active for display 99"), e esse
    # arquivo sobrevive ao restart do container. Quando acontece, o DISPLAY
    # segue exportado apontando pra um display que nao existe e a coleta do
    # BB morre com "Missing X server" — foi o que deixou o cadastro do BB
    # parado de 07/08 a 10/08/2026 sem ninguem perceber (o erro gravado no
    # run apontava pra asyncio, que era so' sintoma).
    #
    # O Xvfb roda DENTRO deste container e e' o unico dono do :99, entao o
    # lock aqui e' sempre residuo: remover e' seguro por construcao.
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true

    Xvfb :99 -screen 0 1920x1080x24 >/tmp/xvfb.log 2>&1 &
    export DISPLAY=:99

    # Confirma que subiu. Sem esta checagem a falha e' silenciosa: o boot
    # segue normal e so' a coleta quebra, horas depois.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        [ -S /tmp/.X11-unix/X99 ] && break
        sleep 0.5
    done
    if [ -S /tmp/.X11-unix/X99 ]; then
        echo "[start] Xvfb :99 iniciado (DISPLAY=:99) para a coleta do BB."
    else
        echo "[start] ERRO: Xvfb :99 NAO subiu — a coleta do BB vai falhar."
        tail -5 /tmp/xvfb.log 2>/dev/null || true
    fi
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
