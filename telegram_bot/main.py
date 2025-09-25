import asyncio
import time
import json
import logging
import os
import socket
import sys
import signal
from typing import Any, Dict, Optional

import dotenv
import aiohttp
from aiohttp import web

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest, Forbidden, TimedOut, NetworkError

dotenv.load_dotenv()

# --- ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/tg")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
PORT = int(os.getenv("PORT", "8080"))

# Оркестратор (из предыдущего файла): base-url или host:port
ORCH_URL = os.getenv("ORCHESTRATOR_URL", "").rstrip("/")
ORCH_HOST = os.getenv("ORCHESTRATOR_HOST", "orchestrator")
ORCH_PORT = int(os.getenv("ORCHESTRATOR_PORT", "8000"))

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))
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


# --- helpers ---
def _tcp_ok(host: str, port: int, timeout: float = 0.7) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _orch_base() -> str:
    return ORCH_URL if ORCH_URL else f"http://{ORCH_HOST}:{ORCH_PORT}"


# --- global aiohttp session ---
http: Optional[aiohttp.ClientSession] = None


async def orch_process(user_id: str, chat_id: str, text: str) -> str:
    """
    Делает единый запрос к оркестратору /process и нормализует ответ для Telegram.
    """
    assert http is not None, "HTTP session not initialized"
    url = _orch_base() + "/process"
    payload = {"user_id": user_id, "message": text, "chat_id": chat_id}

    t0 = time.perf_counter()
    try:
        async with http.post(url, json=payload) as r:
            body = await r.text()
            dt = round((time.perf_counter() - t0) * 1000, 1)
            jinfo(
                "orch_call",
                event="orch_call",
                url=url,
                status=r.status,
                duration_ms=dt,
                bytes=len(body),
            )
            r.raise_for_status()
            data = json.loads(body)
    except Exception as e:
        jerror("orch_failed", event="orch_failed", url=url, error=str(e))
        return "Сервис недоступен. Попробуйте позже."

    # Единый формат (совпадает с моделями из оркестратора)
    status = str(data.get("status", "")).lower()
    response = data.get("response") or ""
    blocked_reason = data.get("blocked_reason") or ""

    if status == "success":
        return str(response) if isinstance(response, str) else "Готово."
    if status == "moderation_blocked":
        reason = f"\nПричина: {blocked_reason}" if blocked_reason else ""
        return f"Запрос заблокирован модерацией.{reason}"
    # error / fallback
    return "Произошла внутренняя ошибка. Попробуйте позже."


# --- Telegram handlers ---
async def start_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    chat = u.effective_chat
    usr = u.effective_user
    jinfo(
        "Start command",
        event="tg_command",
        command="start",
        user_id=getattr(usr, "id", None),
        username=getattr(usr, "username", None),
        chat_id=getattr(chat, "id", None),
        chat_type=getattr(chat, "type", None),
    )
    await u.message.reply_text("ну привет, скуф, чего тебе нужно?")


async def msg_handler(u: Update, c: ContextTypes.DEFAULT_TYPE):
    msg, usr, chat = u.effective_message, u.effective_user, u.effective_chat
    text = (msg.text or "").strip()
    jinfo(
        "Incoming message",
        event="tg_message",
        chat_id=getattr(chat, "id", None),
        chat_type=getattr(chat, "type", None),
        user_id=getattr(usr, "id", None),
        username=getattr(usr, "username", None),
        message_id=getattr(msg, "message_id", None),
        text_len=len(text),
    )
    if not text:
        return

    # Каналы: не пытаемся слать action
    if chat and getattr(chat, "type", None) == "channel":
        jinfo(
            "Skip: channel message (no rights to reply)",
            event="skip_channel",
            chat_id=chat.id,
        )
        return

    # "печатает..."
    try:
        await c.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
    except BadRequest as e:
        jinfo(
            "send_chat_action ignored",
            event="tg_action_ignored",
            reason=str(e),
            chat_id=chat.id,
        )
    except Forbidden as e:
        jinfo(
            "send_chat_action forbidden",
            event="tg_action_forbidden",
            reason=str(e),
            chat_id=chat.id,
        )
        return

    try:
        answer = await orch_process(str(usr.id), str(chat.id), text)
        await msg.reply_text(answer, disable_web_page_preview=True)
    except (Forbidden, BadRequest) as e:
        jerror(
            "Reply failed",
            event="tg_reply_error",
            chat_id=getattr(chat, "id", None),
            chat_type=getattr(chat, "type", None),
            error=str(e),
        )
    except (TimedOut, NetworkError) as e:
        jerror("Reply network error", event="tg_reply_neterr", error=str(e))
    except Exception as e:
        jerror("Reply unexpected error", event="tg_reply_unexpected", error=str(e))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    chat_id = None
    chat_type = None
    try:
        if isinstance(update, Update) and update.effective_chat:
            chat_id = update.effective_chat.id
            chat_type = update.effective_chat.type
    except Exception:
        pass
    jerror(
        "Unhandled error",
        event="unhandled_error",
        error=str(getattr(context, "error", None)),
        chat_id=chat_id,
        chat_type=chat_type,
    )


# --- aiohttp app (webhook + health) ---
app = web.Application()
ptb: Optional[Application] = None


async def healthz(_: web.Request):
    return web.json_response(
        {"status": "alive", "uptime_seconds": round(time.time() - STARTED_AT, 3)}
    )


async def readyz(_: web.Request):
    # если указан ORCH_URL — пытаемся GET / для статуса; если нет, проверим TCP host:port
    deps = {
        "telegram_token": bool(TELEGRAM_TOKEN),
        "telegram_tcp": _tcp_ok("api.telegram.org", 443),
        "orchestrator_url": _orch_base(),
        "orchestrator_tcp": True if ORCH_URL else _tcp_ok(ORCH_HOST, ORCH_PORT),
        "orchestrator_health": False,
    }
    ok_tcp = deps["orchestrator_tcp"]
    health_ok = False
    if http and ok_tcp:
        try:
            async with http.get(_orch_base() + "/") as r:
                health_ok = r.status == 200
        except Exception:
            health_ok = False
    deps["orchestrator_health"] = health_ok
    ok = deps["telegram_token"] and deps["telegram_tcp"] and ok_tcp and health_ok
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


# --- main lifecycle ---
async def amain():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN must be set")

    global http
    http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT))

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
    jinfo(
        "Started",
        event="startup",
        port=PORT,
        webhook_path=WEBHOOK_PATH,
        orch_base=_orch_base(),
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    finally:
        jinfo("Shutting down...", event="shutdown")
        try:
            await ptb.stop()
            await ptb.shutdown()
        finally:
            if http:
                await http.close()
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(amain())
