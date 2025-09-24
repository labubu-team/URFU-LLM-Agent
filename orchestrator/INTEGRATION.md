# Интеграция с Telegram Bot

## Как подключить Telegram бот к оркестратору

### 1. Обновление кода бота

В коде вашего Telegram бота нужно заменить логику обработки сообщений на отправку запросов к оркестратору.

```python
import aiohttp
import os

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8000")

async def process_user_message(user_id: str, message: str, chat_id: str = None):
    """Обработка сообщения пользователя через оркестратор"""

    async with aiohttp.ClientSession() as session:
        try:
            # Формируем запрос к оркестратору
            request_data = {
                "user_id": str(user_id),
                "message": message,
                "chat_id": str(chat_id) if chat_id else None
            }

            # Отправляем запрос
            async with session.post(
                f"{ORCHESTRATOR_URL}/process",
                json=request_data,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:

                if response.status == 200:
                    result = await response.json()

                    # Проверяем статус обработки
                    if result["status"] == "success":
                        return result["response"]
                    elif result["status"] == "moderation_blocked":
                        return "🚫 Ваш запрос содержит недопустимый контент."
                    else:
                        return "❌ Произошла ошибка при обработке запроса."

                else:
                    return "❌ Сервис временно недоступен. Попробуйте позже."

        except asyncio.TimeoutError:
            return "⏰ Запрос обрабатывается слишком долго. Попробуйте упростить вопрос."
        except Exception as e:
            logger.error(f"Ошибка при обращении к оркестратору: {e}")
            return "❌ Произошла техническая ошибка."

# Пример использования в обработчике сообщений
@bot.message_handler(content_types=['text'])
async def handle_text_message(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_message = message.text

    # Отправляем сообщение "печатает..."
    await bot.send_chat_action(chat_id, 'typing')

    # Обрабатываем через оркестратор
    response = await process_user_message(user_id, user_message, chat_id)

    # Отправляем ответ пользователю
    await bot.send_message(chat_id, response)
```

### 2. Переменные окружения

Добавьте в настройки бота переменную окружения:

```bash
ORCHESTRATOR_URL=http://orchestrator:8000
```

### 3. Зависимости

Убедитесь, что в requirements.txt бота есть:

```txt
aiohttp>=3.9.0
```

### 4. Docker Compose конфигурация

В docker-compose.yml telegram_bot должен иметь зависимость от оркестратора:

```yaml
telegram_bot:
  # ... другие настройки
  environment:
    ORCHESTRATOR_URL: http://orchestrator:8000
  depends_on:
    - orchestrator
```

## Обработка ошибок

### Типы ответов от оркестратора

1. **Успешная обработка**:

```json
{
  "status": "success",
  "response": "Ответ от LLM агента",
  "processing_time": 2.5
}
```

2. **Блокировка модерацией**:

```json
{
  "status": "moderation_blocked",
  "response": "Ваш запрос содержит недопустимый контент.",
  "blocked_reason": "Regex: SQL injection pattern",
  "processing_time": 0.8
}
```

3. **Ошибка обработки**:

```json
{
  "status": "error",
  "response": "Произошла внутренняя ошибка сервера.",
  "processing_time": 1.2
}
```

### Рекомендуемые сообщения для пользователей

- **Блокировка модерацией**: "🚫 К сожалению, ваш запрос содержит недопустимый контент. Пожалуйста, переформулируйте вопрос."
- **Ошибка сервиса**: "❌ Извините, произошла техническая ошибка. Попробуйте позже."
- **Таймаут**: "⏰ Обработка запроса занимает слишком много времени. Попробуйте упростить вопрос."
- **Сервис недоступен**: "🔧 Сервис временно недоступен. Мы работаем над устранением проблемы."

## Мониторинг и логирование

Рекомендуется добавить логирование всех запросов к оркестратору:

```python
import logging

logger = logging.getLogger(__name__)

async def process_user_message(user_id: str, message: str, chat_id: str = None):
    logger.info(f"Отправка запроса к оркестратору: user_id={user_id}, chat_id={chat_id}")

    # ... код обработки ...

    logger.info(f"Получен ответ от оркестратора: status={result['status']}, time={result['processing_time']}")
```

## Health Check

Для мониторинга доступности оркестратора можно периодически проверять его статус:

```python
async def check_orchestrator_health():
    """Проверка здоровья оркестратора"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{ORCHESTRATOR_URL}/") as response:
                return response.status == 200
    except:
        return False
```
