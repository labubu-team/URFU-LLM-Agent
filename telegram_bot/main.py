import asyncio
import json
import logging
import os
import socket
import sys
import time
from typing import Any, Dict

from aiohttp import web
import dotenv
import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ── ENV ───────────────────────────────────────────────────────────────────────
dotenv.load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/tg")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me")

PORT = int(os.getenv("PORT", "8080"))
LLM_AGENT_URL = os.getenv("LLM_AGENT_URL", "").rstrip("/")
LLM_AGENT_HOST = os.getenv("LLM_AGENT_HOST", "llm-agent")
LLM_AGENT_PORT = int(os.getenv("LLM_AGENT_PORT", "7999"))

STARTED_AT = time.time()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ── JSON-логгер для YC ───────────────────────────────────────────────────────
_DEFAULT_KEYS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
}


class YCJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        sev = record.levelname.upper()
        if sev == "WARNING":
            sev = "WARN"
        if sev == "CRITICAL":
            sev = "FATAL"
        payload: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "severity": sev,
            "logger": record.name,
            "event": getattr(record, "event", record.funcName or "log"),
            "message": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _DEFAULT_KEYS and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _setup_logging():
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    handler = logging.StreamHandler(stream=sys.stdout)  # stdout важно для YC
    handler.setFormatter(YCJsonFormatter())
    root.addHandler(handler)


_setup_logging()
log = logging.getLogger("app")


def jinfo(m, **f):
    return log.info(m, extra=f)


def jerror(m, **f):
    return log.error(m, extra=f)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _tcp_ok(host: str, port: int, timeout: float = 0.7) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _llm_url() -> str:
    return (
        f"{LLM_AGENT_URL}/v1/completion"
        if LLM_AGENT_URL
        else f"http://{LLM_AGENT_HOST}:{LLM_AGENT_PORT}/v1/completion"
    )


# ── LLM клиент ───────────────────────────────────────────────────────────────
class YandexGPTBot:
    def ask_gpt(self, question: str) -> str:
        t0 = time.perf_counter()
        url = _llm_url()
        try:
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json={"messages": [{"role": "user", "text": question}]},
                timeout=15,
            )
            dt = round((time.perf_counter() - t0) * 1000, 1)
            jinfo(
                "LLM request done",
                event="llm_request",
                url=url,
                status=resp.status_code,
                duration_ms=dt,
                bytes=len(resp.content),
            )
            resp.raise_for_status()
            j = resp.json()
            return j["result"]["alternatives"][0]["message"]["text"]
        except Exception as e:
            dt = round((time.perf_counter() - t0) * 1000, 1)
            jerror(
                "LLM request failed",
                event="llm_error",
                url=url,
                duration_ms=dt,
                error=str(e),
            )
            raise


yandex_bot = YandexGPTBot()


# ── Telegram handlers ────────────────────────────────────────────────────────
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    jinfo(
        "Start command",
        event="tg_command",
        command="start",
        user_id=getattr(u, "id", None),
        username=getattr(u, "username", None),
    )
    await update.message.reply_text(
        "Привет! Я бот для работы с Yandex GPT. Напиши свой вопрос."
    )


async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    text = (msg.text or "").strip()

    jinfo(
        "Incoming message",
        event="tg_message",
        chat_id=getattr(update.effective_chat, "id", None),
        user_id=getattr(user, "id", None),
        username=getattr(user, "username", None),
        message_id=getattr(msg, "message_id", None),
        text_len=len(text),
    )

    if not text:
        await msg.reply_text("Пожалуйста, введите вопрос.")
        return

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
        reply = yandex_bot.ask_gpt(text)
        await msg.reply_text(reply)
    except Exception as e:
        await msg.reply_text(
            "Извините, произошла ошибка при обработке запроса. Попробуйте позже."
        )
        jerror("Reply failed", event="tg_reply_error", error=str(e))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    jerror("Unhandled error", event="unhandled_error", error=str(context.error))


# ── AIOHTTP APP (единый порт 8080) ───────────────────────────────────────────
app = web.Application()
application: Application | None = None  # заполнится в amain()


async def healthz(_: web.Request):
    return web.json_response(
        {"status": "alive", "uptime_seconds": round(time.time() - STARTED_AT, 3)}
    )


async def readyz(_: web.Request):
    deps = {
        "telegram_token": bool(TELEGRAM_TOKEN),
        "llm_url": _llm_url(),
        "llm_agent_tcp": (
            True if LLM_AGENT_URL else _tcp_ok(LLM_AGENT_HOST, LLM_AGENT_PORT)
        ),
    }
    ok = deps["telegram_token"] and deps["llm_agent_tcp"]
    return web.json_response(
        {"status": "ok" if ok else "degraded", "dependencies": deps},
        status=200 if ok else 503,
    )


async def tg_webhook(request: web.Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return web.Response(status=403)
    data = await request.json()
    assert application is not None
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return web.Response(status=200)


app.router.add_get("/healthz", healthz)
app.router.add_get("/readyz", readyz)


# ── Boot ─────────────────────────────────────────────────────────────────────
async def amain():
    global application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler)
    )
    application.add_error_handler(error_handler)

    app.router.add_post(WEBHOOK_PATH, tg_webhook)

    webhook_url = (
        f"{PUBLIC_URL.rstrip('/')}"
        f"{WEBHOOK_PATH if WEBHOOK_PATH.startswith('/') else '/' + WEBHOOK_PATH}"
    )

    await application.initialize()
    await application.start()
    await application.bot.set_webhook(
        url=webhook_url, secret_token=WEBHOOK_SECRET, drop_pending_updates=True
    )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    jinfo("Started", event="startup", port=PORT, webhook_url=webhook_url)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(amain())
