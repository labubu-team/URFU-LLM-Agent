import asyncio
import time
import jwt
import json
import logging
import os
import socket
import sys
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
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/tg")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
PORT = int(os.getenv("PORT", "8080"))
LLM_AGENT_URL = os.getenv("LLM_AGENT_URL", "").rstrip("/")
LLM_AGENT_HOST = os.getenv("LLM_AGENT_HOST", "llm-agent")
LLM_AGENT_PORT = int(os.getenv("LLM_AGENT_PORT", "7999"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
STARTED_AT = time.time()

_DEFAULT = {
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
    def format(self, rec: logging.LogRecord) -> str:
        sev = (
            {"WARNING": "WARN", "CRITICAL": "FATAL"}
            .get(rec.levelname, rec.levelname)
            .upper()
        )
        payload: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(rec.created))
            + f".{int(rec.msecs):03d}Z",
            "severity": sev,
            "logger": rec.name,
            "event": getattr(rec, "event", rec.funcName or "log"),
            "message": rec.getMessage(),
        }
        for k, v in rec.__dict__.items():
            if k not in _DEFAULT and not k.startswith("_"):
                payload[k] = v
        if rec.exc_info:
            payload["exception"] = self.formatException(rec.exc_info)
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
    def ask_gpt(self, q: str) -> str:
        t0 = time.perf_counter()
        url = _llm_url()
        try:
            r = requests.post(
                url,
                headers={"Content-Type": "application/json",
                         "x-node-id": "<this>",
                         "Authorization": "Bearer <IAM_TOKEN>",
                         "x-folder-id": "<this>"},
                json={"messages": [{"role": "user", "text": q}]},
                timeout=15,
            )
            dt = round((time.perf_counter() - t0) * 1000, 1)
            jinfo(
                "LLM request done",
                event="llm_request",
                url=url,
                status=r.status_code,
                duration_ms=dt,
                bytes=len(r.content),
            )
            r.raise_for_status()
            j = r.json()
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


llm = YandexGPTBot()


async def start_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    usr = u.effective_user
    jinfo(
        "Start command",
        event="tg_command",
        command="start",
        user_id=getattr(usr, "id", None),
        username=getattr(usr, "username", None),
    )
    await u.message.reply_text("Привет! Я бот для Yandex GPT. Напиши вопрос.")


async def msg_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    msg, usr = u.effective_message, u.effective_user
    text = (msg.text or "").strip()
    jinfo(
        "Incoming message",
        event="tg_message",
        chat_id=getattr(u.effective_chat, "id", None),
        user_id=getattr(usr, "id", None),
        username=getattr(usr, "username", None),
        message_id=getattr(msg, "message_id", None),
        text_len=len(text),
    )
    if not text:
        await msg.reply_text("Пожалуйста, введите вопрос.")
        return
    try:
        await c.bot.send_chat_action(chat_id=u.effective_chat.id, action="typing")
        await msg.reply_text(llm.ask_gpt(text))
    except Exception as e:
        await msg.reply_text("Извините, произошла ошибка. Попробуйте позже.")
        jerror("Reply failed", event="tg_reply_error", error=str(e))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    jerror("Unhandled error", event="unhandled_error", error=str(context.error))


app = web.Application()
ptb: Optional[Application] = None


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


async def tg_webhook(req: web.Request):
    if req.method != "POST":
        return web.Response(status=405)
    if (
        WEBHOOK_SECRET
        and req.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET
    ):
        return web.Response(status=403)
    data = await req.json()
    assert ptb is not None
    upd = Update.de_json(data, ptb.bot)
    await ptb.process_update(upd)
    return web.Response(status=200)


app.router.add_get("/healthz", healthz)
app.router.add_get("/readyz", readyz)
app.router.add_post(WEBHOOK_PATH, tg_webhook)


async def amain():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN must be set")
    global ptb
    ptb = Application.builder().token(TELEGRAM_TOKEN).build()
    ptb.add_handler(CommandHandler("start", start_cmd))
    ptb.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))
    ptb.add_error_handler(error_handler)
    await ptb.initialize()
    await ptb.start()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    jinfo("Started", event="startup", port=PORT, webhook_path=WEBHOOK_PATH)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(amain())
