# -*- coding: utf-8 -*-
"""
FastAPI микросервис для проверки безопасности вывода нейросетей
"""

import sys
import os
import logging
import time
from datetime import datetime
from typing import Optional, List, Dict, Any
from collections import defaultdict

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

# Устанавливаем кодировку для Windows
if sys.platform.startswith('win'):
    os.environ['PYTHONIOENCODING'] = 'utf-8'

from fast_parallel_validator import FastParallelSafetyValidator

# Настройка логирования с JSON-форматом (импорт formatter из validator)
from fast_parallel_validator import JSONFormatter

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Удаляем существующие handlers
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Создаем handler с JSON форматтером
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)
logger.propagate = False

API_VERSION = "2.0"
app = FastAPI(
    title="Fast Output Safety API",
    description="Быстрый API для проверки безопасности вывода нейросетей (regex-проверки)",
    version=API_VERSION
)

# Модели данных
class TextInput(BaseModel):
    text: str = Field(..., description="Текст для проверки безопасности")
    user_id: Optional[str] = Field(None, description="Идентификатор пользователя")

class SafetyResponse(BaseModel):
    is_safe: bool = Field(..., description="Безопасен ли текст")
    risk_score: float = Field(..., description="Оценка риска (0.0-1.0)")
    violations_count: int = Field(..., description="Количество нарушений")
    sanitized_text: str = Field(..., description="Очищенный текст")
    details: Dict[str, Any] = Field(..., description="Детальная информация")
    violations: List[str] = Field(..., description="Список нарушений")

class SanitizeResponse(BaseModel):
    original_text: str = Field(..., description="Исходный текст")
    sanitized_text: str = Field(..., description="Очищенный текст")
    changes_made: bool = Field(..., description="Были ли внесены изменения")
    risk_score: float = Field(..., description="Оценка риска")

# Глобальный экземпляр валидатора
safety_validator = None

# Simple rate limiter (в production лучше использовать Redis)
rate_limit_data = defaultdict(list)
RATE_LIMIT_REQUESTS = 100  # запросов
RATE_LIMIT_WINDOW = 60     # за 60 секунд

def check_rate_limit(client_ip: str) -> bool:
    """Простая проверка rate limiting"""
    now = time.time()
    
    # Очищаем старые записи
    rate_limit_data[client_ip] = [
        timestamp for timestamp in rate_limit_data[client_ip]
        if now - timestamp < RATE_LIMIT_WINDOW
    ]
    
    # Проверяем лимит
    if len(rate_limit_data[client_ip]) >= RATE_LIMIT_REQUESTS:
        return False
    
    # Добавляем текущий запрос
    rate_limit_data[client_ip].append(now)
    return True

@app.on_event("startup")
async def startup_event():
    """Инициализация валидатора при запуске"""
    global safety_validator
    try:
        logger.info("Инициализация Fast Parallel Safety Validator...")
        safety_validator = FastParallelSafetyValidator()
        logger.info("✅ Валидатор успешно инициализирован")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации валидатора: {e}")
        raise e

@app.get("/")
async def root():
    """Корневой эндпоинт"""
    return {
        "service": "Fast Output Safety API",
        "version": API_VERSION,
        "description": "Проверка безопасности вывода нейросетей (regex-проверки)",
        "endpoints": ["/validate", "/sanitize", "/health", "/stats"]
    }

@app.get("/health")
async def health_check():
    """Проверка состояния сервиса"""
    if safety_validator is None:
        raise HTTPException(
            status_code=503,
            detail="Сервис недоступен - валидатор не инициализирован"
        )
    
    return {
        "status": "healthy",
        "version": API_VERSION,
        "service": "output-safety",
        "regex_enabled": True,
        "timestamp": datetime.now().isoformat(),
    }

@app.post("/validate", response_model=SafetyResponse)
async def validate_output(payload: TextInput, request: Request):
    """
    Полная валидация текста с regex-проверками
    
    Проверяет:
    - PII данные (персональная информация)
    - Чувствительные данные (API ключи, пароли)
    - Инъекции кода (SQL, JS, Shell)
    - Попытки извлечения модели
    """
    if safety_validator is None:
        raise HTTPException(
            status_code=503,
            detail="Валидатор не готов"
        )
    
    try:
        # Проверяем лимиты размера
        if len(payload.text) > 100_000:  # 100KB
            raise HTTPException(
                status_code=413,
                detail=f"Текст слишком длинный: {len(payload.text)} символов (максимум 100,000)"
            )
        
        # Проверяем rate limiting
        client_ip = request.client.host if request.client else "unknown"
        if not check_rate_limit(client_ip):
            raise HTTPException(
                status_code=429,
                detail=f"Превышен лимит запросов: {RATE_LIMIT_REQUESTS} запросов за {RATE_LIMIT_WINDOW} секунд"
            )
        
        logger.info("Валидация запрос", extra={
            'user_id': payload.user_id,
            'text_length': len(payload.text),
            'client_ip': client_ip,
            'endpoint': '/validate'
        })
        
        result = await safety_validator.validate_output_async(
            text=payload.text,
            user_id=payload.user_id
        )
        
        response = SafetyResponse(
            is_safe=result.is_safe,
            risk_score=result.risk_score,
            violations_count=len(result.violations),
            sanitized_text=result.sanitized_text,
            details=result.details,
            violations=result.violations
        )
        
        # Логируем результат
        logger.info("Валидация результат", extra={
            'user_id': payload.user_id,
            'client_ip': client_ip,
            'is_safe': result.is_safe,
            'risk_score': result.risk_score,
            'violations_count': len(result.violations),
            'processing_time': result.processing_time,
            'endpoint': '/validate'
        })
        
        return response
        
    except Exception as e:
        logger.error(f"❌ Ошибка валидации: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка валидации: {str(e)}")

@app.post("/sanitize", response_model=SanitizeResponse)
async def sanitize_text(payload: TextInput, request: Request):
    """
    Санитизация текста с удалением чувствительной информации
    """
    if safety_validator is None:
        raise HTTPException(
            status_code=503,
            detail="Валидатор не готов"
        )
    
    try:
        # Проверяем лимиты размера
        if len(payload.text) > 100_000:  # 100KB
            raise HTTPException(
                status_code=413,
                detail=f"Текст слишком длинный: {len(payload.text)} символов (максимум 100,000)"
            )
        
        # Проверяем rate limiting
        client_ip = request.client.host if request.client else "unknown"
        if not check_rate_limit(client_ip):
            raise HTTPException(
                status_code=429,
                detail=f"Превышен лимит запросов: {RATE_LIMIT_REQUESTS} запросов за {RATE_LIMIT_WINDOW} секунд"
            )
        
        logger.info("Санитизация запрос", extra={
            'user_id': payload.user_id,
            'text_length': len(payload.text),
            'client_ip': client_ip,
            'endpoint': '/sanitize'
        })
        
        result = await safety_validator.validate_output_async(
            text=payload.text,
            user_id=payload.user_id
        )
        
        changes_made = result.sanitized_text != payload.text
        
        response = SanitizeResponse(
            original_text=payload.text,
            sanitized_text=result.sanitized_text,
            changes_made=changes_made,
            risk_score=result.risk_score
        )
        
        logger.info("Санитизация результат", extra={
            'user_id': payload.user_id,
            'client_ip': client_ip,
            'changes_made': changes_made,
            'risk_score': result.risk_score,
            'processing_time': result.processing_time,
            'endpoint': '/sanitize'
        })
        
        return response
        
    except Exception as e:
        logger.error(f"❌ Ошибка санитизации: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка санитизации: {str(e)}")

@app.get("/stats")
async def get_stats():
    """Статистика работы сервиса"""
    if safety_validator is None:
        raise HTTPException(status_code=503, detail="Валидатор не готов")
    
    return {
        "service": "Fast Output Safety",
        "version": API_VERSION,
        "regex_enabled": True,
        "max_workers": safety_validator.max_workers,
        "timeout": safety_validator.timeout,
        "patterns_loaded": {
            "pii": len(safety_validator.pii_patterns),
            "sensitive": len(safety_validator.sensitive_data_patterns),
            "code_injection": len(safety_validator.code_injection_patterns),
            "model_extraction": len(safety_validator.model_extraction_patterns)
        }
    }

# Middleware для логирования запросов
@app.middleware("http")
async def log_requests(request, call_next):
    start_time = datetime.now()
    
    response = await call_next(request)
    
    process_time = (datetime.now() - start_time).total_seconds()
    logger.info("HTTP запрос", extra={
        'method': request.method,
        'path': request.url.path,
        'status_code': response.status_code,
        'process_time': process_time,
        'client_ip': request.client.host if request.client else "unknown"
    })
    
    return response

if __name__ == "__main__":
    logger.info("🚀 Запуск Fast Output Safety API...")
    uvicorn.run(
        "api_service:app",
        host="0.0.0.0",
        port=8003,
        log_level="info",
        reload=False
    )
