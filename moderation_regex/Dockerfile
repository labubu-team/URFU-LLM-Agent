FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8002

RUN pip install --no-cache-dir uv && \
    apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-cache --frozen

COPY ./moderation_regex/ .

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD sh -c "curl -fsS http://127.0.0.1:${PORT:-8002}/healthz || exit 1"

CMD sh -lc "uv run uvicorn moder_api:app --host 0.0.0.0 --port ${PORT:-8002} --no-server-header --timeout-keep-alive 5"
