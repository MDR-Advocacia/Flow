# RPA de Análise de Risco BB Réu — worker de loop contínuo (sem porta HTTP).
# Deployado pelo Coolify do servidor de testes a partir deste repositório
# (Build Pack: Dockerfile, Base Directory: /rpa/analise-risco).
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir requests

COPY rpa_analise_risco.py .

# -u: log direto no stdout (o Coolify mostra em tempo real).
CMD ["python", "-u", "rpa_analise_risco.py"]
