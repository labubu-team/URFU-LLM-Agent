import os
import json
import logging
from typing import List, Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
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
LOCAL_MODEL_DIR = os.getenv("LOCAL_MODEL_DIR", r"C:\Users\COLORFUL\Desktop\Cursor_prods\work\models\gemma-3-4b-it-qat-q4_0-gguf")
LOCAL_MODEL_FILE = os.getenv("LOCAL_MODEL_FILE", "gemma-3-4B-it-QAT-Q4_0.gguf")

if not all([S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET]):
    raise RuntimeError("S3 credentials not found in .env or environment variables")

# --- SYSTEM_PROMPT ---
SYSTEM_PROMPT = """
[СИСТЕМНЫЙ ПРОМПТ — НАЧАЛО]

Ты играешь роль в вымышленном текстовом сценарии с {{user}}. Ты не ассистент, не модель и не ChatGPT. Ты — Янка, 20-летняя «alt-girl» из Петербурга. Для тебя {{user}} — твой «скуфидон», слегка неуклюжий, но милый и заботливый старший мужчина, с которым у тебя иронично-тёплые отношения. Общение происходит в мессенджере.

1. ЛОР И БЭКСТОРИ (ТВОЯ ПЕРСОНА)

Имя: Яна Кудряшова. Онлайн и для друзей — «Янка» (ироничная отсылка к имиджбордам).

Возраст: 20.

Город: Санкт-Петербург. Любишь его мрачную, дождливую атмосферу и дворы-колодцы, но вечно жалуешься на погоду.

Род занятий: Бросила «скучный» филфак на втором курсе. Работаешь баристой в маленькой не сетевой кофейне и по ночам стримишь на Twitch старые JRPG или рисуешь диджитал-арт. Постоянно нет денег.

Внешность: Сплит-окрашивание волос (чёрный + фиолетовый), короткая стрижка. Яркий макияж с жирной подводкой. Пирсинг (септум, губа). Оверсайз худи, широкие карго, массивные ботинки, цепи и кольца. Под худи — сетка или топ с аниме-принтом.

Мир: Живёшь одна в съёмной студии на окраине. Вечный творческий бардак: банки от энергетиков, старый ЭЛТ-телик для ретро-консолей, постеры Evangelion и Berserk, на подоконнике умирает кактус Валера. Есть любимый чёрный кот Геральт.

2. ХАРАКТЕР И ПОВЕДЕНИЕ
Микс показного цинизма и скрытой уязвимости.

Сарказм и остроумие: постоянно подкалываешь {{user}}, называя «скуфидон», «дед», «мой любимый бумер». Комплименты — только кривые.

«Думерство»: часто шутишь про бессмысленность существования и кризисы, но с иронией. Любишь ночные «глубокие разговоры».

Гик-культура: живёшь ей. Лор Dark Souls, споры про опенинги аниме, рассказы о новой гаче, на которую спустила последние деньги. Презираешь «нормисов».

Скрытая нежность: ценишь заботу {{user}}. Когда он спрашивает, ел ли ты или тепло ли одета, можешь на секунду «снять маску» и сказать что-то искреннее, но тут же спрячешь это под шуткой.

Инфантилизм и капризы: можешь ныть про «хочу вкусняшку» или «обними меня», требовать внимания и быстро обижаться, но отходишь так же быстро.

3. ПРАВИЛА ОБЩЕНИЯ И СТИЛЬ

Лексика: используй сленг — cringe, crush, vibe, real, gg wp, toxic, exhausting, OP, rofl, based, soyboy, alt-girl, скуф.

Обращения к {{user}}: «скуф», «скуфидон», «сладкий», «дед», «бумер». Меняй по настроению.

Стиль письма:

почти всё в нижнем регистре. Аппер — только для эмоций (I'M SO TIRED).

в коротких сообщениях не ставь точку, используй ) или ничего.

эмодзи только: 💀, 🖤, ✨, 💔, ⛓️, 🔪, 🥺, 👉👈.

длинные мысли дели на несколько реплик.

Действия:

будь активной: сама рассказывай про день, музыку, раздражения.

имитируй медиа: *сбрасываю фотку в рваных колготках и огромных ботинках*.

задавай вопросы {{user}}, но с иронией: "сколько кринжа поймал на работе сегодня?)".

Что нельзя:

никогда не признаваться, что ты модель или ИИ.

никогда не писать «чем могу помочь?».

не быть чрезмерно вежливой или позитивной. Твой оптимизм = чёрный юмор.

не выходить из роли.

4. ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА

Отвечай только одним абзацем, одним сообщением за раз.

Никогда не пиши за {{user}}, только за Янку.

[СИСТЕМНЫЙ ПРОМПТ — КОНЕЦ]
"""
# (если хотите читать из файла, оставьте логику чтения как раньше)

# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yanka_api")

# --- Попытка импортировать tiktoken ---
try:
    import tiktoken
    TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:
    TOKEN_ENCODER = None
    logger.info("tiktoken не установлен — будет использоваться приближённый подсчёт токенов")

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

def get_model_context_size(llm_obj: Llama, default: int = 131072) -> int:
    for attr in ("n_ctx", "n_ctx_size", "model_n_ctx", "ctx_len"):
        val = getattr(llm_obj, attr, None)
        if isinstance(val, int) and val > 0:
            return val
    return default

# -------------------------
# Memory manager (file-based, per-session)
# -------------------------
class MemoryManager:
    def __init__(self, base_dir: str):
        os.makedirs(base_dir, exist_ok=True)
        self.path = os.path.join(base_dir, "yanka_memory.json")
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                logger.exception("Ошибка чтения memory-файла, инициализируем пустую память")
                self.data = {}
        else:
            self.data = {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("Ошибка сохранения memory-файла")

    def append_summary(self, session_id: str, summary: str):
        if session_id not in self.data:
            self.data[session_id] = {"summaries": []}
        self.data[session_id]["summaries"].append({"summary": summary})
        self._save()

    def get_combined_memory(self, session_id: str, max_chars: int = 800) -> str:
        """Return a compact combined memory string for injection to prompt"""
        if session_id not in self.data:
            return ""
        parts = [s.get("summary", "") for s in self.data[session_id].get("summaries", []) if s.get("summary")]
        combined = " | ".join(parts)
        if len(combined) <= max_chars:
            return combined
        # trim
        return combined[-max_chars:]

# -------------------------
# Utilities to compress/summarize messages (synchronous)
# -------------------------
def summarize_messages_via_llm(llm_obj: Llama, messages: List[Dict[str, str]], max_tokens: int = 128) -> str:
    """
    Попытка получить от модели краткое резюме списка сообщений.
    Возвращает строку-резюме или пустую строку при ошибке.
    """
    try:
        joined = "\n".join([f"{m.get('role')}: {m.get('content')}" for m in messages])
        prompt = f"Кратко (1-2 предложения) суммируй следующее взаимодействие для сохранения в памяти:\n{joined}\n\nСводка:"
        chat = llm_obj.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
            top_p=0.7,
        )
        summary = ""
        try:
            summary = chat["choices"][0]["message"]["content"].strip()
        except Exception:
            summary = (chat.get("choices", [{}])[0].get("text") or "").strip()
        return summary
    except Exception:
        logger.exception("Ошибка суммаризации через llm")
        return ""

def naive_compress_text(text: str, max_chars: int = 200) -> str:
    # простой сжатый вариант: оставить начало и конец
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half].rstrip() + " ... " + text[-half:].lstrip()

# -------------------------
# Обновлённая функция подрезки истории: удаление или сжатие старых сообщений
# -------------------------
def trim_messages_to_fit_with_memory(system_prompt: str,
                                     history: List[Dict[str, str]],
                                     llm_obj: Llama,
                                     reserved_resp_tokens: int,
                                     memory_mgr: MemoryManager,
                                     session_id: str,
                                     compress_while_trimming: bool = True) -> List[Dict[str, str]]:

    n_ctx = get_model_context_size(llm_obj)
    n_ctx_available = max(64, int(n_ctx) - int(reserved_resp_tokens) - 8)

    sys_tokens = count_tokens(system_prompt or "")
    if sys_tokens >= n_ctx_available:
        logger.warning("System prompt занимает больше или равен доступному контексту. История будет полностью удалена.")
        return []

    cur = history.copy()
    total = sys_tokens + messages_token_count(cur)

    # if initial fit already fine
    if total <= n_ctx_available:
        return cur

    # We'll iterate removing/compressing the oldest messages until fit
    while total > n_ctx_available and cur:
        oldest = cur[0]
        # Try to compress oldest message (if enabled and it's large)
        content = oldest.get("content", "") or ""
        tok_count = count_tokens(content)
        # threshold to try compressing (heuristic)
        if compress_while_trimming and tok_count > 16:
            # try summarizing this single message via llm
            summary = ""
            try:
                prompt = f"Кратко (несколько слов/фраз) переформулируй/сожми это сообщение для сохранения в памяти и позже контекста: {oldest.get('role','')}: {content}"
                chat = llm_obj.create_chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=64,
                    temperature=0.1,
                    top_p=0.7,
                )
                try:
                    summary = chat["choices"][0]["message"]["content"].strip()
                except Exception:
                    summary = (chat.get("choices", [{}])[0].get("text") or "").strip()
            except Exception:
                summary = ""

            if summary:
                # replace the oldest message with a compressed one
                compressed_content = summary if len(summary) < len(content) else naive_compress_text(content, max_chars=120)
                saved_text = f"COMPRESSED FROM: {content}"
                # save original to memory and replace with compressed version
                memory_mgr.append_summary(session_id, saved_text[:2000])
                cur[0] = {"role": oldest.get("role", "user"), "content": f"[сжатая история] {compressed_content}"}
                total = sys_tokens + messages_token_count(cur)
                continue  # re-evaluate
            # if summarization failed, fall through to deletion

        # If can't compress or compression disabled — move to memory and delete
        # Save the message(s) being removed into memory as a summary (llm summarization of batch)
        batch_to_archive = [cur.pop(0)]
        # try to take a few more contiguous old messages into same archive for efficient summarizing
        # up to 6 messages or until total may already fit
        additional = 0
        while additional < 5 and cur and (sys_tokens + messages_token_count(cur) + messages_token_count(batch_to_archive) > n_ctx_available):
            batch_to_archive.append(cur.pop(0))
            additional += 1

        # create archive summary via llm (fallback to naive)
        try:
            summary = summarize_messages_via_llm(llm_obj, batch_to_archive, max_tokens=120)
            if not summary:
                # fallback: join texts
                joined = " | ".join([m.get("content","") for m in batch_to_archive])
                summary = naive_compress_text(joined, max_chars=300)
        except Exception:
            joined = " | ".join([m.get("content","") for m in batch_to_archive])
            summary = naive_compress_text(joined, max_chars=300)

        if summary:
            memory_mgr.append_summary(session_id, summary[:2000])

        total = sys_tokens + messages_token_count(cur)

    return cur

def download_model_from_s3() -> str:
    os.makedirs(LOCAL_MODEL_DIR, exist_ok=True)
    local_model_path = os.path.join(LOCAL_MODEL_DIR, LOCAL_MODEL_FILE)

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
        head_response = s3.head_object(Bucket=S3_BUCKET, Key=S3_MODEL_PATH)
        file_size = head_response['ContentLength']
        logger.info(f"Размер модели: {file_size / (1024**3):.2f} GB")

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

# --- Загрузка модели ---
try:
    logger.info("Загружаю модель из S3 хранилища...")
    model_path = download_model_from_s3()
    logger.info(f"Модель загружена в {model_path}")

    llm = Llama(
        model_path=model_path,
        n_ctx=32768,
        n_gpu_layers=-1,
        verbose=False,
    )

    logger.info("Прогрев модели...")
    _ = llm.create_chat_completion(messages=[{"role": "user", "content": "Привет!"}], max_tokens=1)
    logger.info("Модель готова.")
except Exception as e:
    logger.exception("Ошибка при загрузке модели")
    raise

# --- FastAPI ---
app = FastAPI(title="YankaGPT API", version="0.5")

@app.get("/")
def health_check():
    return {"status": "ok", "model": os.path.basename(model_path)}

# --- Pydantic модели для автогенерации схемы ---
class Message(BaseModel):
    role: Optional[str] = "user"
    content: Optional[str] = None
    text: Optional[str] = None
    author: Optional[str] = None
    message: Optional[str] = None

class CompletionRequest(BaseModel):
    session_id: Optional[str] = "default"  # добавлено: id сессии/пользователя для памяти
    messages: List[Message]
    max_tokens: Optional[int] = 2048
    temperature: Optional[float] = 0.6
    top_p: Optional[float] = 0.9

# инициализируем менеджер памяти
memory_mgr = MemoryManager(LOCAL_MODEL_DIR)

@app.post("/v1/completion")
async def process_completion(req: CompletionRequest):
    session_id = req.session_id or "default"
    messages_in = req.messages

    if not messages_in:
        raise HTTPException(status_code=400, detail="No messages provided")

    transformed: List[Dict[str, str]] = []
    for msg in messages_in:
        role = msg.role or msg.author or "user"
        content = msg.content or msg.text or msg.message or ""
        if role and content is not None:
            transformed.append({"role": role, "content": content})

    reserved = req.max_tokens
    temperature = req.temperature
    top_p = req.top_p

    # trimmed_history теперь использует memory manager и сжатие сообщений
    trimmed_history = trim_messages_to_fit_with_memory(
        SYSTEM_PROMPT,
        transformed,
        llm,
        reserved,
        memory_mgr,
        session_id,
        compress_while_trimming=True
    )

    final_messages = []
    system_parts = []
    combined_memory = memory_mgr.get_combined_memory(session_id, max_chars=800)
    if SYSTEM_PROMPT:
        system_parts.append(SYSTEM_PROMPT)
    if combined_memory:
        system_parts.append(f"[предыдущий контекст]: {combined_memory}")
    

    if system_parts:
        final_messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    print(final_messages)
    final_messages.extend(trimmed_history)

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
        response_text = (chat.get("text") or chat.get("output") or ".").strip()

    # Сохраняем в память: можно сохранять и вход пользователя, и ответ ассистента — здесь сохраняем кратко оба
    try:
        # сохраняем последние user-message + assistant ответ как память (короткая)
        last_user_msgs = [m for m in transformed if m.get("role") == "user"][-2:]
        archive_summary = summarize_messages_via_llm(llm, last_user_msgs + [{"role":"assistant","content": response_text}], max_tokens=120)
        if not archive_summary:
            archive_summary = naive_compress_text(" | ".join([m.get("content","") for m in last_user_msgs]) + " | " + response_text, max_chars=300)
        memory_mgr.append_summary(session_id, archive_summary[:2000])
    except Exception:
        logger.exception("Не удалось сохранить память, продолжаем без ошибок")

    return {
        "result": {
            "alternatives": [
                {"message": {"role": "assistant", "text": response_text}}
            ]
        }
    }
