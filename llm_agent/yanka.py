# import os
import logging
from fastapi import FastAPI, Request
from transformers import pipeline

# 1. Загрузка модели из huggingface
# Убедитесь, что у вас достаточно памяти (RAM/VRAM)
try:
    pipe = pipeline("text-generation", model="secretmoon/YankaGPT-8B-v0.1")
    logging.info("Model loaded successfully")
except Exception as e:
    logging.error(f"Error loading model: {e}")
    raise

# 2. Инициализация FastAPI
app = FastAPI()


# 3. Маршрут для проверки работоспособности
@app.get("/")
def health_check():
    """Проверка доступности сервиса"""
    return {"status": "ok", "model": "YankaGPT-8B-v0.1"}


# 4. Основной маршрут для API
@app.post("/v1/completion")
async def process_completion(request: Request):
    """
    Обработка запроса к модели, аналогичная Yandex GPT API.
    Принимает JSON с полем `messages`.
    """
    try:
        data = await request.json()
        messages = data.get("messages", [])

        if not messages:
            return {"error": "No messages provided"}, 400

        # Получаем только текст последнего сообщения
        user_question = messages[-1].get("text", "")
        if not user_question:
            return {"error": "User message is empty"}, 400

        # Генерация ответа с помощью модели
        generated_response = pipe(user_question)
        response_text = generated_response[0]["generated_text"]

        # 5. Формирование ответа в формате, похожем на Yandex GPT
        response_data = {
            "result": {"alternatives": [{"message": {"text": response_text}}]}
        }
        return response_data

    except Exception as e:
        logging.error(f"Error processing completion: {e}")
        return {"error": "Internal Server Error"}, 500
