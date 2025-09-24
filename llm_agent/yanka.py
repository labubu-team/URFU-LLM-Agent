# -*- coding: utf-8 -*-
import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

# Опционально: точный подсчёт токенов
try:
    import tiktoken
    TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:
    TOKEN_ENCODER = None

# llama-cpp (важно: образ собран с CUDA)
from llama_cpp import Llama

# ---------- конфиг ----------
load_dotenv()

MODEL_DIR = Path(os.getenv("MODEL_DIR", "/models"))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

MODEL_FILE = os.getenv("MODEL_FILE", "model.gguf")
MODEL_PATH = Path(os.getenv("MODEL_PATH", str(MODEL_DIR / MODEL_FILE)))

MODEL_REPO = os.getenv("MODEL_REPO", "")     # используется только как fallback
HF_TOKEN = os.getenv("HF_TOKEN")             # fallback для slim-образа/локалки
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "")

# Параметры инференса / GPU
N_CTX = int(os.getenv("N_CTX", "32768"))
# -1 = полный оффлоад на GPU; можно задать конкретное число слоёв
N_GPU_LAYERS = int(os.getenv("N_GPU_LAYERS", "-1"))
N_THREADS = int(os.getenv("N_THREADS", max(1, os.cpu_count() or 1)))

# ---------- логирование ----------
class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": self.formatTime(record),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)

logging.basicConfig(level=logging.INFO)
for h in logging.getLogger().handlers:
    h.setFormatter(JsonFormatter())

log = logging.getLogger("yanka_api")


# ---------- утилиты ----------
def count_tokens(text: str) -> int:
    if not text:
        return 0
    if TOKEN_ENCODER:
        try:
            return len(TOKEN_ENCODER.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def messages_token_count(messages: List[Dict[str, str]]) -> int:
    total = 0
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "") or m.get("text", "")
        total += count_tokens(f"{role}: {content}\n")
    return total


def get_model_context_size(llm_obj: Llama, default: int = 32768) -> int:
    for attr in ("n_ctx", "n_ctx_size", "model_n_ctx", "ctx_len"):
        val = getattr(llm_obj, attr, None)
        if isinstance(val, int) and val > 0:
            return val
    return default


def trim_messages_to_fit(
    system_prompt: str,
    history: List[Dict[str, str]],
    llm_obj: Llama,
    reserved_resp_tokens: int,
) -> List[Dict[str, str]]:
    n_ctx = get_model_context_size(llm_obj)
    n_ctx_available = max(64, int(n_ctx) - int(reserved_resp_tokens) - 8)
    sys_tokens = count_tokens(system_prompt or "")
    if sys_tokens >= n_ctx_available:
        log.warning("system prompt >= available ctx; drop history")
        return []
    cur = history.copy()
    total = sys_tokens + messages_token_count(cur)
    while total > n_ctx_available and cur:
        cur.pop(0)
        total = sys_tokens + messages_token_count(cur)
    return cur


# ---------- опциональный fallback для slim-образа ----------
def maybe_download_model(repo: str, filename: str, token: Optional[str]) -> Optional[Path]:
    """Скачивает модель только если есть токен и MODEL_PATH отсутствует."""
    if MODEL_PATH.exists():
        return MODEL_PATH
    if not (repo and filename and token):
        return None
    try:
        from huggingface_hub import hf_hub_download, login
        login(token)
        path = hf_hub_download(repo_id=repo, filename=filename, local_dir=str(MODEL_DIR), token=token)
        return Path(path)
    except Exception as e:
        log.error("hf download failed: %s", e, exc_info=True)
        return None


# ---------- инициализация модели ----------
if not MODEL_PATH.exists():
    # fallback только для slim/локалки; в DataSphere full-образ уже содержит файл
    # поддержим секрет-файл, если он смонтирован (например, docker swarm/compose)
    sec = Path("/run/secrets/HF_TOKEN")
    if not HF_TOKEN and sec.exists():
        HF_TOKEN = sec.read_text(encoding="utf-8").strip()
    if maybe_download_model(MODEL_REPO, MODEL_FILE, HF_TOKEN) is None:
        raise RuntimeError(
            f"MODEL_PATH '{MODEL_PATH}' не найден и загрузка из HF недоступна. "
            f"Задайте приватный full-образ или предоставьте HF_TOKEN."
        )

log.info(f"loading gguf: {MODEL_PATH}")

llm = Llama(
    model_path=str(MODEL_PATH),
    n_ctx=N_CTX,
    n_gpu_layers=N_GPU_LAYERS,  # -1 => полный оффлоад на GPU при CUDA-сборке
    n_threads=N_THREADS,
    verbose=False,
)

# Прогрев
_ = llm.create_chat_completion(messages=[{"role": "user", "content": "ping"}], max_tokens=1)

# ---------- FastAPI ----------
app = FastAPI(title="LLM API (llama.cpp)", version="1.0")


class InMsg(BaseModel):
    messages: List[Dict[str, str]]
    max_tokens: int = 2048
    temperature: float = 0.6
    top_p: float = 0.9


@app.get("/")
@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "model_file": str(MODEL_PATH.name),
        "ctx": get_model_context_size(llm, N_CTX),
        "gpu_layers": N_GPU_LAYERS,
        "threads": N_THREADS,
    }


@app.post("/v1/completion")
async def completion(req: Request):
    try:
        data = await req.json()
        messages_in = data.get("messages", [])
        if not messages_in or not isinstance(messages_in, list):
            raise HTTPException(status_code=400, detail="No messages or bad format")

        transformed: List[Dict[str, str]] = []
        for m in messages_in:
            role = m.get("role") or m.get("author") or "user"
            content = m.get("content") or m.get("text") or m.get("message") or ""
            transformed.append({"role": role, "content": content})

        reserved = int(data.get("max_tokens", 2048))
        temperature = float(data.get("temperature", 0.6))
        top_p = float(data.get("top_p", 0.9))

        trimmed = trim_messages_to_fit(SYSTEM_PROMPT, transformed, llm, reserved)
        final_messages = []
        if SYSTEM_PROMPT:
            final_messages.append({"role": "system", "content": SYSTEM_PROMPT})
        final_messages.extend(trimmed)

        approx_tokens = count_tokens(SYSTEM_PROMPT) + messages_token_count(trimmed)
        log.info(f"ctx_used={approx_tokens}, reserved={reserved}")

        chat = llm.create_chat_completion(
            messages=final_messages,
            max_tokens=reserved,
            temperature=temperature,
            top_p=top_p,
        )

        text = ""
        try:
            text = chat["choices"][0]["message"]["content"].strip()
        except Exception:
            text = (chat.get("choices", [{}])[0].get("text") or "").strip()
        if not text:
            text = (chat.get("text") or chat.get("output") or ".").strip()

        return {"result": {"alternatives": [{"message": {"role": "assistant", "text": text}}]}}
    except HTTPException:
        raise
    except Exception as e:
        log.error("inference error: %s", e, exc_info=True)
        return {"error": "Internal Server Error"}, 500
