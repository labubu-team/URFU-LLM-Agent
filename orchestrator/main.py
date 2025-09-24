import asyncio
import logging
import os
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Модели данных
class ProcessingStatus(str, Enum):
    SUCCESS = "success"
    MODERATION_BLOCKED = "moderation_blocked"
    ERROR = "error"

class UserRequest(BaseModel):
    user_id: str = Field(..., description="ID пользователя")
    message: str = Field(..., description="Сообщение пользователя")
    chat_id: Optional[str] = Field(None, description="ID чата")

class ProcessingResult(BaseModel):
    status: ProcessingStatus
    response: str = Field(..., description="Ответ системы")
    blocked_reason: Optional[str] = Field(None, description="Причина блокировки")
    processing_time: float = Field(..., description="Время обработки в секундах")

class HealthCheckResponse(BaseModel):
    status: str = "healthy"
    services: Dict[str, bool] = Field(default_factory=dict)

@dataclass
class ServiceEndpoint:
    name: str
    url: str
    health_path: str = "/"

class OrchestratorConfig:
    """Конфигурация оркестратора"""
    
    def __init__(self):
        # Базовые URL сервисов
        self.moderation_regex_url = os.getenv("MODERATION_REGEX_URL", "http://moderation-regex:8000")
        self.moderation_nlp_url = os.getenv("MODERATION_NLP_URL", "http://moderation-nlp:8000")
        self.rag_url = os.getenv("RAG_URL", "http://rag:8000")
        self.llm_agent_url = os.getenv("LLM_AGENT_URL", "http://llm-agent:8000")
        
        # Таймауты
        self.request_timeout = int(os.getenv("REQUEST_TIMEOUT", "30"))
        self.health_check_timeout = int(os.getenv("HEALTH_CHECK_TIMEOUT", "5"))
        
        # Настройки обработки
        self.max_retries = int(os.getenv("MAX_RETRIES", "3"))
        self.retry_delay = float(os.getenv("RETRY_DELAY", "1.0"))
        
        # Сервисы для проверки здоровья
        self.services = [
            ServiceEndpoint("moderation-regex", self.moderation_regex_url, "/"),
            ServiceEndpoint("moderation-nlp", self.moderation_nlp_url, "/"),
            ServiceEndpoint("rag", self.rag_url, "/health"),
            ServiceEndpoint("llm-agent", self.llm_agent_url, "/"),
        ]

class RequestProcessor:
    """Основной класс для обработки запросов пользователей"""
    
    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def init_session(self):
        """Инициализация HTTP сессии"""
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=self.config.request_timeout)
            self.session = aiohttp.ClientSession(timeout=timeout)
    
    async def close_session(self):
        """Закрытие HTTP сессии"""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def _make_request(
        self, 
        url: str, 
        method: str = "POST", 
        data: Optional[Dict[str, Any]] = None,
        retries: int = 0
    ) -> Dict[str, Any]:
        """Выполнение HTTP запроса с повторными попытками"""
        await self.init_session()
        
        try:
            if method == "GET":
                async with self.session.get(url) as response:
                    response.raise_for_status()
                    return await response.json()
            else:
                async with self.session.post(url, json=data) as response:
                    response.raise_for_status()
                    return await response.json()
        
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if retries < self.config.max_retries:
                logger.warning(f"Ошибка запроса к {url}, повтор {retries + 1}/{self.config.max_retries}: {e}")
                await asyncio.sleep(self.config.retry_delay * (retries + 1))
                return await self._make_request(url, method, data, retries + 1)
            else:
                logger.error(f"Не удалось выполнить запрос к {url} после {self.config.max_retries} попыток: {e}")
                raise HTTPException(status_code=503, detail=f"Сервис {url} недоступен")
    
    async def check_regex_moderation(self, text: str) -> tuple[bool, str]:
        """Проверка модерации regex"""
        try:
            url = f"{self.config.moderation_regex_url}/detect"
            data = {"text": text}
            
            result = await self._make_request(url, "POST", data)
            
            injection = result.get("injection", False)
            pattern = result.get("detected_pattern", "")
            
            logger.info(f"Regex модерация: injection={injection}, pattern={pattern}")
            return injection, pattern
            
        except Exception as e:
            logger.error(f"Ошибка при проверке regex модерации: {e}")
            raise HTTPException(status_code=500, detail="Ошибка модерации regex")
    
    async def check_nlp_moderation(self, text: str) -> tuple[bool, str, float]:
        """Проверка модерации NLP"""
        try:
            url = f"{self.config.moderation_nlp_url}/classify"
            data = {"text": text}
            
            result = await self._make_request(url, "POST", data)
            
            injection = result.get("injection", False)
            label = result.get("label", "")
            score = result.get("score", 0.0)
            
            logger.info(f"NLP модерация: injection={injection}, label={label}, score={score}")
            return injection, label, score
            
        except Exception as e:
            logger.error(f"Ошибка при проверке NLP модерации: {e}")
            raise HTTPException(status_code=500, detail="Ошибка модерации NLP")
    
    async def get_rag_context(self, query: str) -> str:
        """Получение контекста из RAG"""
        try:
            url = f"{self.config.rag_url}/search"
            data = {"query": query}
            
            result = await self._make_request(url, "POST", data)
            
            context = result.get("context", "")
            logger.info(f"RAG контекст получен: {len(context)} символов")
            return context
            
        except Exception as e:
            logger.error(f"Ошибка при получении RAG контекста: {e}")
            raise HTTPException(status_code=500, detail="Ошибка получения контекста")
    
    async def get_llm_response(self, user_message: str, context: str = "") -> str:
        """Получение ответа от LLM агента"""
        try:
            url = f"{self.config.llm_agent_url}/v1/completion"
            
            # Формируем сообщение с контекстом
            message_text = user_message
            if context:
                message_text = f"Контекст: {context}\n\nВопрос пользователя: {user_message}"
            
            data = {
                "messages": [
                    {"role": "user", "text": message_text}
                ]
            }
            
            result = await self._make_request(url, "POST", data)
            
            # Предполагаем, что LLM возвращает ответ в поле 'response' или 'text'
            response = result.get("response", result.get("text", "Извините, произошла ошибка при генерации ответа"))
            
            logger.info(f"LLM ответ получен: {len(response)} символов")
            return response
            
        except Exception as e:
            logger.error(f"Ошибка при получении ответа от LLM: {e}")
            raise HTTPException(status_code=500, detail="Ошибка генерации ответа")
    
    async def process_request(self, request: UserRequest) -> ProcessingResult:
        """Основная функция обработки запроса пользователя"""
        import time
        start_time = time.time()
        
        logger.info(f"Начинаем обработку запроса от пользователя {request.user_id}")
        
        try:
            # Этап 1: Модерация regex
            logger.info("Этап 1: Проверка regex модерации")
            regex_blocked, regex_pattern = await self.check_regex_moderation(request.message)
            
            if regex_blocked:
                processing_time = time.time() - start_time
                logger.warning(f"Запрос заблокирован regex модерацией: {regex_pattern}")
                return ProcessingResult(
                    status=ProcessingStatus.MODERATION_BLOCKED,
                    response="Ваш запрос содержит недопустимый контент.",
                    blocked_reason=f"Regex: {regex_pattern}",
                    processing_time=processing_time
                )
            
            # Этап 2: Модерация NLP
            logger.info("Этап 2: Проверка NLP модерации")
            nlp_blocked, nlp_label, nlp_score = await self.check_nlp_moderation(request.message)
            
            if nlp_blocked:
                processing_time = time.time() - start_time
                logger.warning(f"Запрос заблокирован NLP модерацией: {nlp_label} (score: {nlp_score})")
                return ProcessingResult(
                    status=ProcessingStatus.MODERATION_BLOCKED,
                    response="Ваш запрос содержит потенциально вредоносный контент.",
                    blocked_reason=f"NLP: {nlp_label} (score: {nlp_score})",
                    processing_time=processing_time
                )
            
            # Этап 3: Получение контекста из RAG
            logger.info("Этап 3: Получение контекста из RAG")
            context = await self.get_rag_context(request.message)
            
            # Этап 4: Получение ответа от LLM
            logger.info("Этап 4: Генерация ответа LLM")
            llm_response = await self.get_llm_response(request.message, context)
            
            processing_time = time.time() - start_time
            logger.info(f"Запрос успешно обработан за {processing_time:.2f} сек")
            
            return ProcessingResult(
                status=ProcessingStatus.SUCCESS,
                response=llm_response,
                processing_time=processing_time
            )
            
        except HTTPException:
            raise
        except Exception as e:
            processing_time = time.time() - start_time
            logger.error(f"Неожиданная ошибка при обработке запроса: {e}")
            return ProcessingResult(
                status=ProcessingStatus.ERROR,
                response="Произошла внутренняя ошибка сервера.",
                processing_time=processing_time
            )

class HealthChecker:
    """Класс для проверки здоровья сервисов"""
    
    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def init_session(self):
        """Инициализация HTTP сессии для health check"""
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=self.config.health_check_timeout)
            self.session = aiohttp.ClientSession(timeout=timeout)
    
    async def close_session(self):
        """Закрытие HTTP сессии"""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def check_service(self, service: ServiceEndpoint) -> bool:
        """Проверка здоровья одного сервиса"""
        await self.init_session()
        
        try:
            url = f"{service.url}{service.health_path}"
            async with self.session.get(url) as response:
                return response.status == 200
        except Exception as e:
            logger.warning(f"Сервис {service.name} недоступен: {e}")
            return False
    
    async def check_all_services(self) -> Dict[str, bool]:
        """Проверка здоровья всех сервисов"""
        results = {}
        
        # Проверяем все сервисы параллельно
        tasks = [
            self.check_service(service) 
            for service in self.config.services
        ]
        
        service_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for service, result in zip(self.config.services, service_results):
            if isinstance(result, Exception):
                results[service.name] = False
            else:
                results[service.name] = result
        
        return results

# Инициализация приложения
app = FastAPI(
    title="LLM Agent Orchestrator",
    description="Оркестратор для системы защищенного LLM агента с Telegram ботом",
    version="1.0.0"
)

# Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Глобальные объекты
config = OrchestratorConfig()
processor = RequestProcessor(config)
health_checker = HealthChecker(config)

@app.on_event("startup")
async def startup_event():
    """Инициализация при запуске приложения"""
    logger.info("Запуск оркестратора...")
    await processor.init_session()
    await health_checker.init_session()
    logger.info("Оркестратор успешно запущен")

@app.on_event("shutdown")
async def shutdown_event():
    """Очистка ресурсов при завершении"""
    logger.info("Завершение работы оркестратора...")
    await processor.close_session()
    await health_checker.close_session()
    logger.info("Оркестратор завершен")

@app.get("/", response_model=HealthCheckResponse)
async def health_check():
    """Health check endpoint"""
    try:
        services_status = await health_checker.check_all_services()
        
        return HealthCheckResponse(
            status="healthy",
            services=services_status
        )
    except Exception as e:
        logger.error(f"Ошибка при проверке здоровья: {e}")
        raise HTTPException(status_code=500, detail="Ошибка проверки здоровья")

@app.post("/process", response_model=ProcessingResult)
async def process_user_request(request: UserRequest):
    """
    Главный endpoint для обработки запросов пользователей.
    Выполняет полный пайплайн: модерация -> RAG -> LLM -> ответ
    """
    try:
        logger.info(f"Получен запрос на обработку от пользователя {request.user_id}")
        result = await processor.process_request(request)
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Критическая ошибка при обработке запроса: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

@app.get("/services/status")
async def get_services_status():
    """Получение статуса всех микросервисов"""
    try:
        services_status = await health_checker.check_all_services()
        return {
            "orchestrator": "healthy",
            "services": services_status,
            "all_healthy": all(services_status.values())
        }
    except Exception as e:
        logger.error(f"Ошибка при получении статуса сервисов: {e}")
        raise HTTPException(status_code=500, detail="Ошибка получения статуса сервисов")

if __name__ == "__main__":
    # Запуск сервера
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    
    logger.info(f"Запуск оркестратора на {host}:{port}")
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
