FROM python:3.11-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_INDEX_URL=https://pypi.org/simple \
    UV_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu \
    PIP_INDEX_URL=https://pypi.org/simple \
    PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu \
    TMPDIR=/var/tmp

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache \
    uv sync --group moder-nlp --frozen

COPY ./moderation_nlp/ .

EXPOSE 8000
CMD ["uv", "run", "moder_api.py"]
