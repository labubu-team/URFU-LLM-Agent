import json
import logging
import os
import socket
import time
from typing import Any, Dict

import dotenv
import requests
from aiohttp import web
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

# если есть внешний URL агента — используем его, иначе host:port
LLM_AGENT_URL = os.getenv("LLM_AGENT_URL", "").rstrip("/")
LLM_AGENT_HOST = os.getenv("LLM_AGENT_HOST", "llm-agent")
LLM_AGENT_PORT = int(os.getenv("LLM_AGENT_PORT", "7999"))

STARTED_AT = time.time()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ── JSON-логгер для YC ───────────────────────────────────────────────────────
DEFAULT_KEYS = {
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
        severity = record.levelname.upper()
        if severity == "WARNING":
            severity = "WARN"
        if severity == "CRITICAL":
            severity = "FATAL"

        payload: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "severity": severity,
            "logger": record.name,
            "event": getattr(record, "event", record.funcName or "log"),
            "message": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in DEFAULT_KEYS and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _setup_logging():
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    h = logging.StreamHandler()
    h.setFormatter(YCJsonFormatter())
    root.addHandler(h)


_setup_logging()
log = logging.getLogger("app")


def jinfo(message: str, **fields):
    log.info(message, extra=fields)


def jerror(message: str, **fields):
    log.error(message, extra=fields)


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
            headers = {"Content-Type": "application/json"}
            data = {"messages": [{"role": "user", "text": question}]}

            resp = requests.post(url, headers=headers, json=data, timeout=15)
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


# ── Health ───────────────────────────────────────────────────────────────────
async def healthz(_req: web.Request):
    return web.json_response(
        {"status": "alive", "uptime_seconds": round(time.time() - STARTED_AT, 3)}
    )


async def readyz(_req: web.Request):
    deps = {
        "telegram_token": bool(TELEGRAM_TOKEN),
        "llm_url": LLM_AGENT_URL or f"http://{LLM_AGENT_HOST}:{LLM_AGENT_PORT}",
        "llm_agent_tcp": (
            True if LLM_AGENT_URL else _tcp_ok(LLM_AGENT_HOST, LLM_AGENT_PORT)
        ),
    }
    ok = bool(TELEGRAM_TOKEN) and deps["llm_agent_tcp"]
    return web.json_response(
        {
            "status": "ok" if ok else "degraded",
            "uptime_seconds": round(time.time() - STARTED_AT, 3),
            "dependencies": deps,
        },
        status=200 if ok else 503,
    )


@web.middleware
async def access_log_middleware(request: web.Request, handler):
    t0 = time.perf_counter()
    rid = request.headers.get("x-request-id") or request.headers.get("x-requestid")
    status = 200
    try:
        resp = await handler(request)
        status = getattr(resp, "status", 200)
        return resp
    except web.HTTPException as ex:
        status = ex.status
        raise
    except Exception:
        status = 500
        raise
    finally:
        dt = round((time.perf_counter() - t0) * 1000, 1)
        remote = request.headers.get("x-forwarded-for") or request.remote
        jinfo(
            "HTTP access",
            event="http_access",
            method=request.method,
            path=request.path_qs,
            status=status,
            duration_ms=dt,
            remote=remote,
            request_id=rid,
        )


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set")
    if not PUBLIC_URL:
        raise RuntimeError("PUBLIC_URL is not set")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler)
    )
    application.add_error_handler(error_handler)

    application.web_app.middlewares.append(access_log_middleware)
    application.web_app.add_routes(
        [web.get("/healthz", healthz), web.get("/readyz", readyz)]
    )

    webhook_url = f"{PUBLIC_URL.rstrip('/')}{WEBHOOK_PATH if WEBHOOK_PATH.startswith('/') else '/' + WEBHOOK_PATH}"
    jinfo(
        "Starting webhook",
        event="startup",
        public_url=PUBLIC_URL,
        webhook_path=WEBHOOK_PATH,
        webhook_url=webhook_url,
    )

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_path=WEBHOOK_PATH,
        webhook_url=webhook_url,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
