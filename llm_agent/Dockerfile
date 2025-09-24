# syntax=docker/dockerfile:1.7-labs

FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv git curl ca-certificates build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

RUN CMAKE_ARGS="-DGGML_CUDA=ON" pip3 install --no-cache-dir --upgrade pip \
 && CMAKE_ARGS="-DGGML_CUDA=ON" pip3 install --no-cache-dir \
      "llama-cpp-python>=0.3.4" \
      "fastapi>=0.110" "uvicorn[standard]>=0.29" \
      "huggingface_hub>=0.24" "python-dotenv>=1.0" "tiktoken>=0.7"

ENV MODEL_DIR=/models
RUN mkdir -p ${MODEL_DIR}

ARG DOWNLOAD_MODEL=1
ARG MODEL_REPO="google/gemma-3-4b-it-qat-q4_0-gguf"
ARG MODEL_FILE="gemma-3-4b-it-qat-q4_0.gguf"

RUN --mount=type=secret,id=HF_TOKEN \
    bash -lc 'set -euo pipefail; \
      if [ "${DOWNLOAD_MODEL}" = "1" ]; then \
        if [ -f /run/secrets/HF_TOKEN ]; then \
          export HF_TOKEN="$(cat /run/secrets/HF_TOKEN)"; \
          python3 - <<PY
from huggingface_hub import login, hf_hub_download
import os
login(os.environ["HF_TOKEN"])
p = hf_hub_download(repo_id=os.environ.get("MODEL_REPO","'$MODEL_REPO'"),
                    filename=os.environ.get("MODEL_FILE","'$MODEL_FILE'"),
                    local_dir=os.environ.get("MODEL_DIR","'$MODEL_DIR'"),
                    token=os.environ["HF_TOKEN"])
print("Downloaded:", p)
PY
        else \
          echo "HF_TOKEN secret not provided; skipping model download"; \
        fi; \
      else \
        echo "DOWNLOAD_MODEL=0; skipping model download"; \
      fi'

WORKDIR /app
COPY llm_agent/yanka_api.py /app/yanka_api.py

ENV MODEL_FILE=${MODEL_FILE} \
    MODEL_PATH=${MODEL_DIR}/${MODEL_FILE} \
    N_CTX=32768 \
    N_GPU_LAYERS=-1 \
    N_THREADS=8

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS -m 3 http://127.0.0.1:8000/healthz >/dev/null || exit 1

CMD ["python3", "-m", "uvicorn", "yanka_api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
