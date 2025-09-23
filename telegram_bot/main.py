import logging
import os
import socket
import time

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

dotenv.load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/tg")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me")
PORT = int(os.getenv("PORT", "8080"))

LLM_AGENT_HOST = os.getenv("LLM_AGENT_HOST", "llm-agent")
LLM_AGENT_PORT = int(os.getenv("LLM_AGENT_PORT", "7999"))
STARTED_AT = time.time()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)
logger = logging.getLogger("tg-bot")


class YandexGPTBot:
    def ask_gpt(self, question: str) -> str:
        """Запрос к LLM агенту."""
        try:
            headers = {"Content-Type": "application/json"}
            data = {"messages": [{"role": "user", "text": question}]}
            url = f"http://{LLM_AGENT_HOST}:{LLM_AGENT_PORT}/v1/completion"

            resp = requests.post(url, headers=headers, json=data, timeout=30)
            if resp.status_code != 200:
                logger.error("LLM error %s: %s", resp.status_code, resp.text)
                raise RuntimeError(f"LLM API: {resp.status_code}")

            j = resp.json()
            return j["result"]["alternatives"][0]["message"]["text"]
        except Exception as e:
            logger.exception("ask_gpt failed: %s", e)
            raise


yandex_bot = YandexGPTBot()


# ── handlers ──────────────────────────────────────────────────────────────────
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для работы с Yandex GPT. Напиши свой вопрос."
    )


async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Пожалуйста, введите вопрос.")
        return

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
        reply = yandex_bot.ask_gpt(text)
        await update.message.reply_text(reply)
    except Exception:
        await update.message.reply_text(
            "Извините, произошла ошибка при обработке запроса. Попробуйте позже."
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Update %s caused error: %s", update, context.error)


# ── health ────────────────────────────────────────────────────────────────────
def _tcp_ok(host: str, port: int, timeout: float = 0.7) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def healthz(_request: web.Request):
    return web.json_response(
        {"status": "alive", "uptime_seconds": round(time.time() - STARTED_AT, 3)}
    )


async def readyz(_request: web.Request):
    deps = {
        "telegram_token": bool(TELEGRAM_TOKEN),
        "llm_agent_tcp": _tcp_ok(LLM_AGENT_HOST, LLM_AGENT_PORT),
    }
    ok = all(deps.values())
    return web.json_response(
        {
            "status": "ok" if ok else "degraded",
            "uptime_seconds": round(time.time() - STARTED_AT, 3),
            "dependencies": deps,
        },
        status=200 if ok else 503,
    )


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set")
    if not PUBLIC_URL:
        raise RuntimeError("PUBLIC_URL is not set")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))
    app.add_error_handler(error_handler)

    app.web_app.add_routes(
        [
            web.get("/healthz", healthz),
            web.get("/readyz", readyz),
        ]
    )

    webhook_url = f"{PUBLIC_URL.rstrip('/')}{WEBHOOK_PATH if WEBHOOK_PATH.startswith('/') else '/' + WEBHOOK_PATH}"
    logger.info("Starting webhook server: %s -> %s", WEBHOOK_PATH, webhook_url)

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_path=WEBHOOK_PATH,
        webhook_url=webhook_url,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
