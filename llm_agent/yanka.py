import os
import logging
from typing import List, Dict, Optional

from fastapi import FastAPI, Request, HTTPException
from huggingface_hub import hf_hub_download, login
from llama_cpp import Llama
from dotenv import load_dotenv

# --- Загрузка переменных окружения ---
load_dotenv()
HF_TOKEN = os.getenv("HF_TOKEN")
MODEL_REPO = os.getenv("MODEL_REPO", "google/gemma-3-4b-it-qat-q4_0-gguf")
MODEL_FILE = os.getenv("MODEL_FILE", "gemma-3-4b-it-qat-q4_0.gguf")

if not HF_TOKEN:
    raise RuntimeError("HF_TOKEN не найден в .env или переменных окружения")

# --- SYSTEM_PROMPT ---
# ВАЖНО: сюда НЕ вкладываем сам prompt. Вставьте ваш SYSTEM_PROMPT в переменную окружения SYSTEM_PROMPT
# или положите его в файл 'system_prompt.txt' рядом с этим скриптом
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT") or ""
if not SYSTEM_PROMPT:
    # попытка загрузить из файла, если он есть
    try:
        with open("system_prompt.txt", "r", encoding="utf-8") as f:
            SYSTEM_PROMPT = f.read()
    except Exception:
        # оставляем пустым и логируем — пользователь вставит сам
        pass

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yanka_api")

# --- Попытка импортировать tiktoken для точного подсчёта токенов (опционально) ---
try:
    import tiktoken

    try:
        # Если работать с неопределённой моделью, используем cl100k_base как fallback
        TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception:
        TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:
    TOKEN_ENCODER = None
    logger.info(
        "tiktoken не установлен — будет использоваться приближённый подсчёт токенов (1 токен ≈ 4 символа)"
    )


def count_tokens(text: str) -> int:
    if not text:
        return 0
    if TOKEN_ENCODER:
        try:
            return len(TOKEN_ENCODER.encode(text))
        except Exception:
            pass
    # fallback приближённый
    return max(1, len(text) // 4)


def messages_token_count(messages: List[Dict[str, str]]) -> int:
    total = 0
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "") or m.get("text", "")
        total += count_tokens(f"{role}: {content}\n")
    return total


def get_model_context_size(llm_obj: Llama, default: int = 32768) -> int:
    # Попытки получить контекст из объекта llama_cpp, иначе использовать default
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
    """
    Урезает старые сообщения (с начала списка history), чтобы суммарный объём system+history
    не превышал допустимый контекст модели (учитывая reserved_resp_tokens для генерации).
    """
    n_ctx = get_model_context_size(llm_obj)
    # безопасный запас 8 токенов
    n_ctx_available = max(64, int(n_ctx) - int(reserved_resp_tokens) - 8)

    sys_tokens = count_tokens(system_prompt or "")
    # если system сама по себе больше доступного — ничего не трогаем (крайний случай),
    # но продолжим и всё равно будем убирать историю целиком
    if sys_tokens >= n_ctx_available:
        logger.warning(
            "System prompt занимает больше или равен доступному контексту. История будет полностью удалена."
        )
        return []

    cur = history.copy()
    total = sys_tokens + messages_token_count(cur)
    # удаляем самые старые сообщения, пока не влезем
    while total > n_ctx_available and cur:
        cur.pop(0)
        total = sys_tokens + messages_token_count(cur)

    return cur


# Опциональная функция суммаризации старой истории (по желанию)
def summarize_messages(
    llm_obj: Llama, messages_to_summarize: List[Dict[str, str]], max_tokens: int = 128
) -> Optional[str]:
    """
    Краткая суммаризация старых сообщений. Использует ту же модель, поэтому учитывайте расход токенов.
    Возвращает строку с суммаризацией или None при ошибке.
    """
    if not messages_to_summarize:
        return None

    prompt_parts = []
    for m in messages_to_summarize:
        role = m.get("role", "")
        content = m.get("content", "") or m.get("text", "")
        prompt_parts.append(f"{role}: {content}")
    summary_system = (
        "Сжать следующие сообщения в 1-2 коротких предложения, сохранив ключевые факты и имена. "
        "Не добавлять ничего лишнего.\n\n"
    )
    try:
        chat = llm_obj.create_chat_completion(
            messages=[
                {"role": "user", "content": summary_system + "\n".join(prompt_parts)}
            ],
            max_tokens=max_tokens,
            temperature=0.2,
            top_p=0.9,
        )
        try:
            return chat["choices"][0]["message"]["content"].strip()
        except Exception:
            return (chat.get("choices", [{}])[0].get("text") or "").strip()
    except Exception as e:
        logger.exception("Ошибка при суммаризации истории: %s", e)
        return None


# --- Авторизация и загрузка модели ---
try:
    logger.info("Логинимся в HuggingFace Hub...")
    login(HF_TOKEN)

    logger.info(f"Скачиваю {MODEL_FILE} из {MODEL_REPO}...")
    model_path = hf_hub_download(
        repo_id=MODEL_REPO,
        filename=MODEL_FILE,
        local_dir="models",
        token=HF_TOKEN,
    )
    logger.info(f"Модель скачана в {model_path}")

    llm = Llama(
        model_path=model_path,
        n_ctx=32768,
        n_gpu_layers=-1,  # CPU-only: 0 для GPU
        verbose=False,
    )

    logger.info("Прогрев модели...")
    _ = llm.create_chat_completion(
        messages=[{"role": "user", "content": "Привет!"}], max_tokens=1
    )
    logger.info("Модель готова.")
except Exception:
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
            raise HTTPException(
                status_code=400, detail="No messages provided or incorrect format"
            )

        # Нормализуем входящие сообщения в формат {'role','content'}
        transformed: List[Dict[str, str]] = []
        for msg in messages_in:
            # Поддерживаем разные ключи: content/text
            role = msg.get("role") or msg.get("author") or "user"
            content = msg.get("content") or msg.get("text") or msg.get("message") or ""
            if role and content is not None:
                transformed.append({"role": role, "content": content})

        reserved = int(data.get("max_tokens", 2048))
        temperature = float(data.get("temperature", 0.6))
        top_p = float(data.get("top_p", 0.9))

        # --- Обрезка истории по токенам ---
        # Вы можете включить суммаризатор, если хотите сохранить смысл старых сообщений
        ENABLE_SUMMARIZATION = (
            False  # смените на True если хотите сначала сжать старую историю
        )
        if ENABLE_SUMMARIZATION and len(transformed) > 6:
            # например, суммируем первую треть истории
            split_idx = max(1, len(transformed) // 3)
            to_summarize = transformed[:split_idx]
            summary_text = summarize_messages(llm, to_summarize, max_tokens=128)
            if summary_text:
                # заменяем старые сообщения на одну короткую сводку от system (или assistant)
                transformed = [
                    {"role": "system", "content": f"history_summary: {summary_text}"}
                ] + transformed[split_idx:]

        trimmed_history = trim_messages_to_fit(
            SYSTEM_PROMPT, transformed, llm, reserved
        )

        # Формируем итоговый список сообщений, system всегда первым
        final_messages = []
        if SYSTEM_PROMPT:
            final_messages.append({"role": "system", "content": SYSTEM_PROMPT})
        final_messages.extend(trimmed_history)

        # Лог уровня info — сколько токенов примерно занимает история
        try:
            approx_tokens = count_tokens(SYSTEM_PROMPT) + messages_token_count(
                trimmed_history
            )
            logger.info(
                "approx tokens for system+history: %d (reserved for response: %d)",
                approx_tokens,
                reserved,
            )
        except Exception:
            pass

        # Вызов модели
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
