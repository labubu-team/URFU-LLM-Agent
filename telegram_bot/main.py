import logging
import os
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

import json
import time
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

dotenv.load_dotenv()

SERVICE_ACCOUNT_ID = os.getenv("SERVICE_ACCOUNT_ID")
PUBLIC_KEY = os.getenv("PUBLIC_KEY")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
KEY_ID = os.getenv("KEY_ID")
FOLDER_ID = os.getenv("FOLDER_ID")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# --- health ---
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8080"))
STARTED_AT = time.time()
LLM_AGENT_HOST = os.getenv("LLM_AGENT_HOST", "llm-agent")
LLM_AGENT_PORT = int(os.getenv("LLM_AGENT_PORT", "7999"))
# --- /health ---

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


class YandexGPTBot:
    def __init__(self):
        self.iam_token = None
        self.token_expires = 0

    def ask_gpt(self, question):
        """Запрос к Yandex GPT API"""
        try:
            headers = {"Content-Type": "application/json"}
            data = {"messages": [{"role": "user", "text": question}]}

            response = requests.post(
                "http://llm-agent:7999/v1/completion",
                headers=headers,
                json=data,
                timeout=30,
            )

            if response.status_code != 200:
                logger.error(f"Yandex GPT API error: {response.text}")
                raise Exception(f"Ошибка API: {response.status_code}")

            return response.json()["result"]["alternatives"][0]["message"]["text"]

        except Exception as e:
            logger.error(f"Error in ask_gpt: {str(e)}")
            raise


yandex_bot = YandexGPTBot()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для работы с Yandex GPT. Просто напиши мне свой вопрос"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text

    if not user_message.strip():
        await update.message.reply_text("Пожалуйста, введите вопрос")
        return

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
        response = yandex_bot.ask_gpt(user_message)
        await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"Error handling message: {str(e)}")
        await update.message.reply_text(
            "Извините, произошла ошибка при обработке вашего запроса. "
            "Пожалуйста, попробуйте позже."
        )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Произошла ошибка. Пожалуйста, попробуйте позже."
        )


# --- health: helpers ---
def _tcp_check(host: str, port: int, timeout: float = 0.7) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _readiness_probe() -> tuple[dict, bool]:
    deps = {
        "telegram_token": bool(TELEGRAM_TOKEN),
        "llm_agent_tcp": _tcp_check(LLM_AGENT_HOST, LLM_AGENT_PORT),
    }
    ok = all(deps.values())
    payload = {
        "status": "ok" if ok else "degraded",
        "uptime_seconds": round(time.time() - STARTED_AT, 3),
        "dependencies": deps,
    }
    return payload, ok


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/healthz"):
            payload = {
                "status": "alive",
                "uptime_seconds": round(time.time() - STARTED_AT, 3),
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        if self.path.startswith("/readyz"):
            payload, ok = _readiness_probe()
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server(host: str = "0.0.0.0", port: int = HEALTH_PORT):
    server = HTTPServer((host, port), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"Health server started on http://{host}:{port}")
    return server


def main():
    try:
        start_health_server()

        application = Application.builder().token(TELEGRAM_TOKEN).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
        )
        application.add_error_handler(error_handler)

        logger.info("Бот запускается...")
        application.run_polling()

    except Exception as e:
        logger.error(f"Failed to start bot: {str(e)}")


if __name__ == "__main__":
    main()
