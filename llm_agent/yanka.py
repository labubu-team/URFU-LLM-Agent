import os
import logging
from typing import List, Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError

from fastapi import FastAPI, Request, HTTPException
from llama_cpp import Llama
from dotenv import load_dotenv

# --- Загрузка переменных окружения ---
load_dotenv()

# S3 конфигурация
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "https://storage.yandexcloud.net")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET = os.getenv("S3_BUCKET", "labubu-team")
S3_MODEL_PATH = os.getenv("S3_MODEL_PATH", "dump/file")
LOCAL_MODEL_DIR = os.getenv("LOCAL_MODEL_DIR", "models")
LOCAL_MODEL_FILE = os.getenv("LOCAL_MODEL_FILE", "model.gguf")

if not all([S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET]):
    raise RuntimeError("S3 credentials not found in .env or environment variables")

# --- SYSTEM_PROMPT ---
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT") or ""
if not SYSTEM_PROMPT:
    try:
        with open("system_prompt.txt", "r", encoding="utf-8") as f:
            SYSTEM_PROMPT = f.read()
    except Exception:
        pass

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yanka_api")

# --- Попытка импортировать tiktoken для точного подсчёта токенов (опционально) ---
try:
    import tiktoken
    try:
        TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception:
        TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:
    TOKEN_ENCODER = None
    logger.info("tiktoken не установлен — будет использоваться приближённый подсчёт токенов (1 токен ≈ 4 символа)")

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

def trim_messages_to_fit(system_prompt: str, history: List[Dict[str, str]], llm_obj: Llama, reserved_resp_tokens: int) -> List[Dict[str, str]]:
    """
    Урезает старые сообщения (с начала списка history), чтобы суммарный объём system+history
    не превышал допустимый контекст модели (учитывая reserved_resp_tokens для генерации).
    """
    n_ctx = get_model_context_size(llm_obj)
    n_ctx_available = max(64, int(n_ctx) - int(reserved_resp_tokens) - 8)

    sys_tokens = count_tokens(system_prompt or "")
    if sys_tokens >= n_ctx_available:
        logger.warning("System prompt занимает больше или равен доступному контексту. История будет полностью удалена.")
        return []

    cur = history.copy()
    total = sys_tokens + messages_token_count(cur)
    while total > n_ctx_available and cur:
        cur.pop(0)
        total = sys_tokens + messages_token_count(cur)

    return cur

def download_model_from_s3() -> str:
    """
    Загружает модель из S3 хранилища в локальную директорию.
    Возвращает путь к локальному файлу модели.
    """
    os.makedirs(LOCAL_MODEL_DIR, exist_ok=True)
    local_model_path = os.path.join(LOCAL_MODEL_DIR, LOCAL_MODEL_FILE)
    
    # Если модель уже существует, пропускаем загрузку
    if os.path.exists(local_model_path):
        logger.info(f"Модель уже существует в {local_model_path}, пропускаем загрузку")
        return local_model_path
    
    logger.info(f"Загружаю модель из S3: {S3_BUCKET}/{S3_MODEL_PATH} -> {local_model_path}")
    
    s3 = boto3.client(
        's3',
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY
    )
    
    try:
        # Получаем информацию о файле для прогресс-бара
        head_response = s3.head_object(Bucket=S3_BUCKET, Key=S3_MODEL_PATH)
        file_size = head_response['ContentLength']
        logger.info(f"Размер модели: {file_size / (1024**3):.2f} GB")
        
        # Загрузка с прогресс-баром
        import time
        start_time = time.time()
        uploaded = 0
        
        def progress_callback(bytes_amount):
            nonlocal uploaded
            uploaded += bytes_amount
            percentage = (uploaded / file_size) * 100
            bar_length = 50
            filled_length = int(bar_length * uploaded // file_size)
            bar = '█' * filled_length + '░' * (bar_length - filled_length)
            print(f'\r📤 Прогресс: |{bar}| {percentage:.1f}% ', end='', flush=True)
        
        s3.download_file(
            Bucket=S3_BUCKET,
            Key=S3_MODEL_PATH,
            Filename=local_model_path,
            Callback=progress_callback
        )
        
        total_time = time.time() - start_time
        print(f"\n✅ Загрузка завершена за {total_time:.2f} секунд!")
        print(f"⚡ Средняя скорость: {file_size / total_time / (1024**2):.2f} MB/s")
        
        return local_model_path
        
    except ClientError as e:
        logger.error(f"Ошибка при загрузке модели из S3: {e}")
        raise RuntimeError(f"Failed to download model from S3: {e}")

# --- Загрузка модели из S3 ---
try:
    logger.info("Загружаю модель из S3 хранилища...")
    model_path = download_model_from_s3()
    logger.info(f"Модель загружена в {model_path}")

    llm = Llama(
        model_path=model_path,
        n_ctx=32768,
        n_gpu_layers=-1,  # CPU-only: 0 для GPU
        verbose=False,
    )

    logger.info("Прогрев модели...")
    _ = llm.create_chat_completion(messages=[{"role": "user", "content": "Привет!"}], max_tokens=1)
    logger.info("Модель готова.")
except Exception as e:
    logger.exception("Ошибка при загрузке модели")
    raise

# --- FastAPI ---
app = FastAPI(title="YankaGPT API", version="0.4")

@app.get("/")
def health_check():
    return {"status": "ok", "model": os.path.basename(model_path)}

@app.post("/v1/completion")
async def process_completion(request: Request):
    """
    Принимает JSON с messages (формат Yandex/GPT-like или OpenAI-like),
    возвращает ответ модели через llama-cpp.
    """
    try:
        data = await request.json()
        messages_in = data.get("messages", [])

        if not messages_in or not isinstance(messages_in, list):
            raise HTTPException(status_code=400, detail="No messages provided or incorrect format")

        transformed: List[Dict[str, str]] = []
        for msg in messages_in:
            role = msg.get("role") or msg.get("author") or "user"
            content = msg.get("content") or msg.get("text") or msg.get("message") or ""
            if role and content is not None:
                transformed.append({"role": role, "content": content})

        reserved = int(data.get("max_tokens", 2048))
        temperature = float(data.get("temperature", 0.6))
        top_p = float(data.get("top_p", 0.9))

        ENABLE_SUMMARIZATION = False
        trimmed_history = trim_messages_to_fit(SYSTEM_PROMPT, transformed, llm, reserved)

        final_messages = []
        if SYSTEM_PROMPT:
            final_messages.append({"role": "system", "content": SYSTEM_PROMPT})
        final_messages.extend(trimmed_history)

        try:
            approx_tokens = count_tokens(SYSTEM_PROMPT) + messages_token_count(trimmed_history)
            logger.info("approx tokens for system+history: %d (reserved for response: %d)", approx_tokens, reserved)
        except Exception:
            pass

        chat = llm.create_chat_completion(
            messages=final_messages,
            max_tokens=reserved,
            temperature=temperature,
            top_p=top_p,
        )

        response_text = ""
        try:
            response_text = chat["choices"][0]["message"]["content"].strip()
        except Exception:
            response_text = (chat.get("choices", [{}])[0].get("text") or "").strip()

        if not response_text:
            response_text = (chat.get("text") or chat.get("output") or "").strip()

        if not response_text:
            response_text = "."

        return {
            "result": {
                "alternatives": [
                    {"message": {"role": "assistant", "text": response_text}}
                ]
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка при генерации: %s", e)
        return {"error": "Internal Server Error"}, 500