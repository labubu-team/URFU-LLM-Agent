# Использование triton https://github.com/triton-inference-server
FROM nvcr.io/nvidia/tritonserver:24.01-py3-sdk
# FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    PATH="/root/.local/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN uv sync --group llm --no-cache --frozen

COPY ./llm_agent/ .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -fsS -m 3 http://127.0.0.1:8000/healthz > /dev/null || exit 1

CMD ["uv", "run", "uvicorn", "yanka:app", "--host", "0.0.0.0", "--port", "8000"]