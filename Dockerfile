# syntax=docker/dockerfile:1
# Built for linux/amd64 by .github/workflows/build-and-push.yml (Azure Container Apps runs amd64).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /srv
COPY requirements.txt .
RUN pip install --only-binary=:all: -r requirements.txt

COPY app ./app
COPY db ./db

RUN useradd --system --uid 10001 --no-create-home mcp
USER 10001

EXPOSE 8000
# Liveness only (no DB call), so it never wakes a scaled-to-zero Neon compute.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health',timeout=4)" || exit 1

CMD ["python", "-m", "app.server"]
