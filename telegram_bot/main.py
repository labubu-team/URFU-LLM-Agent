import asyncio
import json
import logging
import os
import socket
import sys
import time
from typing import Any, Dict, Optional

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

dotenv.load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
PORT = int(os.getenv("PORT", "8080"))
LLM_AGENT_URL = os.getenv("LLM_AGENT_URL", "").rstrip("/")
LLM_AGENT_HOST = os.getenv("LLM_AGENT_HOST", "llm-agent")
LLM_AGENT_PORT = int(os.getenv("LLM_AGENT_PORT", "7999"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
STARTED_AT = time.time()

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
        sev = (
            {"WARNING": "WARN", "CRITICAL": "FATAL"}
            .get(record.levelname, record.levelname)
            .upper()
        )
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
    h = logging.StreamHandler(stream=sys.stdout)
    h.setFormatter(YCJsonFormatter())
    root.addHandler(h)


_setup_logging()
log = logging.getLogger("app")
def jinfo(m, **f):
    return log.info(m, extra=f)
def jerror(m, **f):
    return log.error(m, extra=f)


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


app = web.Application()
ptb_app: Optional[Application] = None


async def healthz(_: web.Request):
    return web.json_response(
        {"status": "alive", "uptime_seconds": round(time.time() - STARTED_AT, 3)}
    )


async def readyz(_: web.Request):
    deps = {
        "telegram_token": bool(TELEGRAM_TOKEN),
        "telegram_tcp": _tcp_ok("api.telegram.org", 443),
        "llm_url": _llm_url(),
        "llm_agent_tcp": (
            True if LLM_AGENT_URL else _tcp_ok(LLM_AGENT_HOST, LLM_AGENT_PORT)
        ),
    }
    ok = deps["telegram_token"] and deps["telegram_tcp"] and deps["llm_agent_tcp"]
    return web.json_response(
        {"status": "ok" if ok else "degraded", "dependencies": deps},
        status=200 if ok else 503,
    )


app.router.add_get("/healthz", healthz)
app.router.add_get("/readyz", readyz)


async def _polling_loop():
    assert ptb_app is not None
    offset: Optional[int] = None
    backoff = 1.0
    while True:
        try:
            updates = await ptb_app.bot.get_updates(
                offset=offset, timeout=50, request_timeout=60
            )
            for upd in updates:
                offset = upd.update_id + 1
                await ptb_app.process_update(upd)
            backoff = 1.0
        except Exception as e:
            jerror("Polling failed", event="polling_error", error=str(e))
            await asyncio.sleep(min(backoff, 30.0))
            backoff = min(backoff * 2, 30.0)


async def amain():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN must be set")

    global ptb_app
    ptb_app = Application.builder().token(TELEGRAM_TOKEN).build()
    ptb_app.add_handler(CommandHandler("start", start_cmd))
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))
    ptb_app.add_error_handler(error_handler)

    await ptb_app.initialize()
    await ptb_app.start()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    jinfo("Started", event="startup", port=PORT, mode="polling")
    asyncio.create_task(_polling_loop())
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(amain())
