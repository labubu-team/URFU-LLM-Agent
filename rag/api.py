import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from rag import RAG

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- FastAPI приложение ---
app = FastAPI(
    title="RAG Service API",
    description="API для поиска релевантной информации в документах из S3.",
    version="1.0.0",
)


# --- Модели данных (Pydantic) ---


class SearchRequest(BaseModel):
    query: str = Field(
        ..., min_length=3, description="Поисковый запрос для поиска по документам."
    )


class SearchResponse(BaseModel):
    context: str = Field(
        ..., description="Найденный контекст из документов, релевантный запросу."
    )
    message: str = Field("success", description="Статус ответа.")


# Создаем один глобальный экземпляр RAG.
# Это ключевой момент для serverless: класс инициализируется один раз
# во время "холодного старта" контейнера. Все последующие запросы
# будут использовать уже готовый, прогретый экземпляр с загруженным индексом.


try:
    logger.info("Запуск инициализации RAG-системы...")
    rag_instance = RAG()
    logger.info("RAG-система успешно инициализирована.")
except Exception as e:
    logger.error(f"Критическая ошибка при инициализации RAG: {e}", exc_info=True)
    rag_instance = None


# --- API эндпоинты ---


@app.get("/health", summary="Проверка состояния сервиса")
def health_check():
    """Эндпоинт для проверки, что сервис запущен и работает."""
    if rag_instance is None:
        raise HTTPException(
            status_code=503,
            detail="Сервис недоступен, не удалось инициализировать RAG.",
        )
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse, summary="Поиск по документам")
def search(request: SearchRequest):
    """
    Принимает поисковый запрос и возвращает наиболее релевантный контекст
    из проиндексированных документов.
    """
    if rag_instance is None:
        raise HTTPException(
            status_code=503,
            detail="Сервис временно недоступен из-за ошибки инициализации.",
        )
    logger.info(f"Получен поисковый запрос: '{request.query}'")
    try:
        context = rag_instance.search_engine(request.query)
        if not context:
            logger.info("Релевантных фрагментов не найдено.")
            return SearchResponse(context="", message="No relevant context found.")

        logger.info("Контекст успешно найден.")
        return SearchResponse(context=context)
    except Exception as e:
        logger.error(f"Ошибка во время поиска: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Внутренняя ошибка сервера при поиске."
        )
