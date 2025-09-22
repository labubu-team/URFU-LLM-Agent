# Использование triton https://github.com/triton-inference-server
FROM nvcr.io/nvidia/tritonserver:22.01-py3

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    PATH="/root/.local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --group llm --no-cache --frozen

COPY ./llm_agent/ .

# Определение порта, на котором будет работать контейнер
EXPOSE 8000

# Команда для запуска Uvicorn (ASGI-сервер для FastAPI)
CMD ["uv", "run", "uvicorn", "yanka:app", "--host", "0.0.0.0", "--port", "8000"]