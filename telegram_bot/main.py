import asyncio
import time
import json
import jwt
import logging
import os
import socket
import sys
import signal
from typing import Any, Dict, Optional

from aiohttp import web
import dotenv
import requests

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
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/tg")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
PORT = int(os.getenv("PORT", "8080"))
LLM_AGENT_URL = os.getenv("LLM_AGENT_URL", "").rstrip("/")
NODE_ID=os.getenv("NODE_ID", "")
FOLDER_ID=os.getenv("FOLDER_ID", "")
SERVICE_ACCOUNT_ID=os.getenv("SERVICE_ACCOUNT_ID", "")
PUBLIC_KEY=os.getenv('PUBLIC_KEY', "")
PRIVATE_KEY=os.getenv('PRIVATE_KEY', "")
KEY_ID=os.getenv('KEY_ID', "")
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
    def __init__(self):
        self.iam_token = None
        self.token_expires = 0

    def get_iam_token(self):
        """Получение IAM-токена (с кэшированием на 1 час)"""
        if self.iam_token and time.time() < self.token_expires:
            return self.iam_token

        try:
            now = int(time.time())
            payload = {
                'aud': 'https://iam.api.cloud.yandex.net/iam/v1/tokens',
                'iss': SERVICE_ACCOUNT_ID,
                'iat': now,
                'exp': now + 3600
            }

            encoded_token = jwt.encode(
                payload,
                PRIVATE_KEY,
                algorithm='PS256',
                headers={'kid': KEY_ID}
            )

            response = requests.post(
                'https://iam.api.cloud.yandex.net/iam/v1/tokens',
                json={'jwt': encoded_token},
                timeout=10
            )

            if response.status_code != 200:
                raise Exception(f"Ошибка генерации токена: {response.text}")

            token_data = response.json()
            self.iam_token = token_data['iamToken']
            self.token_expires = now + 3500  # На 100 секунд меньше срока действия

            jinfo("IAM token generated successfully", event="get_iam_token_llm_request")
            return self.iam_token

        except Exception as e:
            jerror(f"Error generating IAM token: {str(e)}", event="get_iam_token_llm_request")
            raise

    def ask_gpt(self, q: str) -> str:
        iam_token = self.get_iam_token()
        t0 = time.perf_counter()
        url = _llm_url()
        try:
            r = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "x-node-id": NODE_ID,
                    "Authorization": f"Bearer {iam_token}",
                    "x-folder-id": FOLDER_ID,
                },
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
    await u.message.reply_text("Привет! Я бот для Yandex GPT. Напиши вопрос.")


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
        # просто молча игнорируем пустые
        return

    # В каналах часто 400: нет прав/нет смысла слать action. Скипаем.
    if chat and getattr(chat, "type", None) == "channel":
        jinfo(
            "Skip: channel message (no rights to reply)",
            event="skip_channel",
            chat_id=chat.id,
        )
        return

    # Best-effort send_chat_action: не валимся на 400
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
        answer = llm.ask_gpt(text)
        await msg.reply_text(answer, disable_web_page_preview=True)
    except (Forbidden, BadRequest) as e:
        # НИЧЕГО не отправляем повторно — только логируем
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
        await ptb.stop()
        await ptb.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(amain())
