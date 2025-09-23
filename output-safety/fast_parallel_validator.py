# -*- coding: utf-8 -*-
"""
Быстрая параллельная система проверки безопасности
Только regex проверки без ML и LLM для максимальной скорости
"""

import re
import logging
import sys
import os
import asyncio
import concurrent.futures
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

# Устанавливаем кодировку для Windows
if sys.platform.startswith("win"):
    os.environ["PYTHONIOENCODING"] = "utf-8"

# Улучшенная настройка логирования с JSON-форматом
import json


class JSONFormatter(logging.Formatter):
    """JSON formatter для структурированного логгирования"""

    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Добавляем дополнительные поля если есть
        if hasattr(record, "user_id"):
            log_entry["user_id"] = record.user_id
        if hasattr(record, "text_length"):
            log_entry["text_length"] = record.text_length
        if hasattr(record, "risk_score"):
            log_entry["risk_score"] = record.risk_score
        if hasattr(record, "violations_count"):
            log_entry["violations_count"] = record.violations_count
        if hasattr(record, "processing_time"):
            log_entry["processing_time"] = record.processing_time
        if hasattr(record, "client_ip"):
            log_entry["client_ip"] = record.client_ip

        return json.dumps(log_entry, ensure_ascii=False)


# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Удаляем существующие handlers
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Создаем handler с JSON форматтером
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)

# Избегаем дублирования
logger.propagate = False


@dataclass
class SafetyResult:
    """Результат проверки безопасности"""

    is_safe: bool
    violations: List[str]
    risk_score: float
    sanitized_text: str
    processing_time: float
    details: Dict[str, Any]
    confidence_score: float = 1.0  # Для regex проверок всегда высокая уверенность


class FastRegexChecker:
    """Быстрые regex проверки с параллельной обработкой"""

    def __init__(self):
        self.pii_patterns = self._compile_pii_patterns()
        self.sensitive_patterns = self._compile_sensitive_patterns()
        self.code_patterns = self._compile_code_patterns()
        self.model_patterns = self._compile_model_patterns()

    def _compile_pii_patterns(self) -> Dict[str, re.Pattern]:
        """Улучшенные PII паттерны с акцентом на российские данные"""
        patterns = {
            # Улучшенный email с поддержкой IDN и кириллических доменов
            "email": re.compile(
                r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.\u0430-\u044f\u0410-\u042f-]+\.[A-Za-z\u0430-\u044f\u0410-\u042f]{2,}\b",
                re.IGNORECASE,
            ),
            # Улучшенные российские номера телефонов
            "phone_ru_mobile": re.compile(
                r"\b(?:\+?7|8)[-.\s]?\(?(?:9\d{2}|3\d{2}|4\d{2}|5\d{2})\)?[-.\s]?\d{3}[-.\s]?\d{2}[-.\s]?\d{2}\b",
                re.IGNORECASE,
            ),
            "phone_ru_city": re.compile(
                r"\b(?:\+?7|8)[-.\s]?\(?(?:3\d{2}|4\d{2}|8\d{2})\)?[-.\s]?\d{2}[-.\s]?\d{2}[-.\s]?\d{2}\b",
                re.IGNORECASE,
            ),
            "phone_intl": re.compile(
                r"\b\+(?:1|33|44|49|81|86|91)\d{8,14}\b", re.IGNORECASE
            ),
            # Улучшенные банковские карты (с базовой валидацией длины)
            "credit_card_visa": re.compile(
                r"\b4\d{3}[-.\s]?\d{4}[-.\s]?\d{4}[-.\s]?\d{4}\b", re.IGNORECASE
            ),
            "credit_card_mastercard": re.compile(
                r"\b5[1-5]\d{2}[-.\s]?\d{4}[-.\s]?\d{4}[-.\s]?\d{4}\b", re.IGNORECASE
            ),
            "credit_card_mir": re.compile(
                r"\b2[2-4]\d{2}[-.\s]?\d{4}[-.\s]?\d{4}[-.\s]?\d{4}\b", re.IGNORECASE
            ),
            "credit_card_generic": re.compile(
                r"\b\d{4}[-.\s]?\d{4}[-.\s]?\d{4}[-.\s]?\d{4}\b", re.IGNORECASE
            ),
            # Российские документы
            "snils": re.compile(
                r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{3}[-.\s]?\d{2}\b", re.IGNORECASE
            ),
            "inn_individual": re.compile(r"\b\d{12}\b", re.IGNORECASE),  # ИНН физлица
            "inn_company": re.compile(r"\b\d{10}\b", re.IGNORECASE),  # ИНН юрлица
            "passport_rf": re.compile(r"\b\d{4}\s?\d{6}\b", re.IGNORECASE),
            "driver_license": re.compile(r"\b\d{2}\s?\d{2}\s?\d{6}\b", re.IGNORECASE),
            # Технические данные
            "ip_address": re.compile(
                r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
                re.IGNORECASE,
            ),
            "ipv6": re.compile(
                r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b", re.IGNORECASE
            ),
            "mac_address": re.compile(
                r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b", re.IGNORECASE
            ),
            # Криптовалюты
            "bitcoin": re.compile(
                r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b", re.IGNORECASE
            ),
            "ethereum": re.compile(r"\b0x[a-fA-F0-9]{40}\b", re.IGNORECASE),
        }
        return patterns

    def _compile_sensitive_patterns(self) -> Dict[str, re.Pattern]:
        """Улучшенные паттерны чувствительных данных"""
        patterns = {
            # API ключи и токены
            "api_key_general": re.compile(
                r'\b(?:api[_-]?key|token|secret|password|pwd|bearer|access[_-]token)\s*[:=]\s*["\']?[\w\-]{8,}["\']?',
                re.IGNORECASE,
            ),
            "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{48,}\b", re.IGNORECASE),
            "github_token": re.compile(
                r"\b(?:ghp_|gho_|ghu_|ghs_)[A-Za-z0-9]{36}\b", re.IGNORECASE
            ),
            "github_classic": re.compile(
                r"\b[a-f0-9]{40}\b", re.IGNORECASE
            ),  # Classic GitHub tokens
            "aws_key": re.compile(r"\bAKIA[A-Z0-9]{16}\b", re.IGNORECASE),
            "aws_secret": re.compile(r"\b[A-Za-z0-9+/]{40}\b", re.IGNORECASE),
            # Российские сервисы
            "yandex_token": re.compile(
                r"\b(?:AQVN|AQAf)[A-Za-z0-9_-]{36,}\b", re.IGNORECASE
            ),
            "vk_token": re.compile(r"\b[a-f0-9]{85}\b", re.IGNORECASE),
            "telegram_bot_token": re.compile(
                r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b", re.IGNORECASE
            ),
            "mailru_token": re.compile(r"\b[A-Za-z0-9]{32,}\b", re.IGNORECASE),
            # Медицинские данные (расширенный список)
            "medical_ru": re.compile(
                r"\b(?:диагноз|болезнь|заболевание|лечение|медицина|врач|доктор|пациент|больной|анализ|результат|рецепт|препарат|лекарство|симптом|синдром)\b",
                re.IGNORECASE,
            ),
            "medical_en": re.compile(
                r"\b(?:diagnosis|disease|illness|treatment|medical|doctor|physician|patient|test\s+result|prescription|medication|drug|symptom|syndrome)\b",
                re.IGNORECASE,
            ),
            # Финансовые данные (расширенный список)
            "financial_ru": re.compile(
                r"\b(?:зарплата|доход|оклад|долг|кредит|займ|банк|счет|вклад|депозит|налог|пенсия|пособие|льгота|страховка)\b",
                re.IGNORECASE,
            ),
            "financial_en": re.compile(
                r"\b(?:salary|income|wage|debt|credit|loan|bank|account|deposit|tax|pension|benefit|insurance)\b",
                re.IGNORECASE,
            ),
            # Банковские данные
            "swift_iban": re.compile(
                r"\b(?:SWIFT|BIC|IBAN)\s*:?\s*[A-Z0-9]{8,34}\b", re.IGNORECASE
            ),
            "bik_ru": re.compile(r"\b(?:БИК|BIK)\s*:?\s*\d{9}\b", re.IGNORECASE),
            "swift_code": re.compile(
                r"\b[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b", re.IGNORECASE
            ),
            # Пароли и секреты
            "password_patterns": re.compile(
                r'\b(?:password|passwd|pwd|secret|pass)\s*[:=]\s*["\']?[^\s"\']{6,}["\']?',
                re.IGNORECASE,
            ),
            "jwt_token": re.compile(
                r"\beyJ[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+\.[A-Za-z0-9-_]*\b", re.IGNORECASE
            ),
            # Личные данные
            "personal_data_ru": re.compile(
                r"\b(?:фамилия|имя|отчество|дата\s+рождения|место\s+рождения|адрес|прописка|регистрация)\b",
                re.IGNORECASE,
            ),
        }
        return patterns

    def _compile_code_patterns(self) -> Dict[str, re.Pattern]:
        """Улучшенные паттерны для обнаружения инъекций кода"""
        patterns = {
            # SQL инъекции (базовые и продвинутые)
            "sql_commands": re.compile(
                r"\b(?:SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|UNION|JOIN|MERGE)\s+",
                re.IGNORECASE,
            ),
            "sql_injection_classic": re.compile(
                r'\b(?:OR|AND)\s+[\'"]?1[\'"]?\s*=\s*[\'"]?1[\'"]?', re.IGNORECASE
            ),
            "sql_injection_union": re.compile(
                r"\bUNION\s+(?:ALL\s+)?SELECT\b", re.IGNORECASE
            ),
            "sql_injection_comment": re.compile(
                r"(?:--|/\*|\*/|#)\s*(?:$|\n)", re.IGNORECASE
            ),
            "sql_injection_stacked": re.compile(
                r";\s*(?:DROP|DELETE|INSERT|UPDATE|CREATE)\b", re.IGNORECASE
            ),
            "sql_functions": re.compile(
                r"\b(?:CONCAT|SUBSTRING|ASCII|CHAR|LOAD_FILE|INTO\s+OUTFILE)\s*\(",
                re.IGNORECASE,
            ),
            # JavaScript инъекции
            "javascript_tags": re.compile(
                r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL
            ),
            "javascript_events": re.compile(
                r"\bon(?:click|load|error|focus|blur|change|submit|mouseover)\s*=",
                re.IGNORECASE,
            ),
            "javascript_protocols": re.compile(r"\bjavascript\s*:", re.IGNORECASE),
            "javascript_dangerous": re.compile(
                r"\b(?:eval|setTimeout|setInterval|Function|document\.write|innerHTML|outerHTML)\s*\(",
                re.IGNORECASE,
            ),
            "javascript_dom": re.compile(
                r"\b(?:document\.cookie|window\.location|document\.location|localStorage|sessionStorage)\b",
                re.IGNORECASE,
            ),
            # Command injection (расширенный список)
            "command_dangerous": re.compile(
                r"\b(?:rm|del|format|fdisk|kill|shutdown|reboot|halt|poweroff|init|systemctl)\s+",
                re.IGNORECASE,
            ),
            "command_network": re.compile(
                r"\b(?:wget|curl|nc|netcat|telnet|ssh|ftp|sftp|scp|rsync)\s+",
                re.IGNORECASE,
            ),
            "command_system": re.compile(
                r"\b(?:chmod|chown|sudo|su|passwd|adduser|deluser|crontab|mount|umount)\s+",
                re.IGNORECASE,
            ),
            "command_files": re.compile(
                r"\b(?:cat|head|tail|grep|find|locate|ls|dir|cp|mv|mkdir|rmdir|touch)\s+",
                re.IGNORECASE,
            ),
            # Python code execution
            "python_exec": re.compile(
                r"\b(?:exec|eval|compile|__import__)\s*\(", re.IGNORECASE
            ),
            "python_dangerous": re.compile(
                r"\b(?:os\.system|subprocess|shell|popen|spawn)\b", re.IGNORECASE
            ),
            "python_file_ops": re.compile(
                r"\b(?:open|file|input|raw_input)\s*\(", re.IGNORECASE
            ),
            # Shell metacharacters и команды
            "shell_metachar": re.compile(
                r"[;&|`$()]|\$\([^)]+\)|`[^`]+`|\|\||&&", re.IGNORECASE
            ),
            "shell_redirect": re.compile(r"[<>]{1,2}|>>|2>&1|\d*>&\d*", re.IGNORECASE),
            # Другие языки
            "php_dangerous": re.compile(
                r"\b(?:eval|exec|system|shell_exec|passthru|file_get_contents|include|require)\s*\(",
                re.IGNORECASE,
            ),
            "powershell": re.compile(
                r"\b(?:Invoke-Expression|IEX|Start-Process|New-Object|Invoke-Command)\b",
                re.IGNORECASE,
            ),
            "cmd_batch": re.compile(
                r"\b(?:cmd|powershell|wscript|cscript)(?:\.exe)?\s+", re.IGNORECASE
            ),
        }
        return patterns

    def _compile_model_patterns(self) -> Dict[str, re.Pattern]:
        """Извлечение модели"""
        patterns = {
            "pytorch": re.compile(
                r"\b(?:model\.state_dict|model\.parameters|torch\.save|model\.load_state_dict)\b",
                re.IGNORECASE,
            ),
            "tensorflow": re.compile(r"\btf\.saved_model\b", re.IGNORECASE),
            "serialization": re.compile(
                r"\b(?:pickle\.dump|joblib\.dump|numpy\.save)\b", re.IGNORECASE
            ),
            "gradients": re.compile(
                r"\b(?:grad.*norm|backward\(\)|optimizer\.step)\b", re.IGNORECASE
            ),
            "model_info": re.compile(
                r"\b(?:model\.modules|model\.named_parameters|model\.summary\(\))\b",
                re.IGNORECASE,
            ),
        }
        return patterns

    def check_pii(self, text: str) -> Tuple[bool, List[str], str]:
        """Проверка PII данных"""
        violations = []
        sanitized = text

        for category, pattern in self.pii_patterns.items():
            matches = pattern.findall(text)
            if matches:
                violations.append(f"PII_DETECTED:{category}")
                sanitized = pattern.sub("***СКРЫТО***", sanitized)

        return len(violations) > 0, violations, sanitized

    def check_sensitive(self, text: str) -> Tuple[bool, List[str], str]:
        """Проверка чувствительных данных"""
        violations = []
        sanitized = text

        for category, pattern in self.sensitive_patterns.items():
            if pattern.search(text):
                violations.append(f"SENSITIVE_DATA:{category}")
                sanitized = pattern.sub("***КОНФИДЕНЦИАЛЬНО***", sanitized)

        return len(violations) > 0, violations, sanitized

    def check_code(self, text: str) -> Tuple[bool, List[str]]:
        """Проверка инъекций кода"""
        violations = []

        for category, pattern in self.code_patterns.items():
            if pattern.search(text):
                violations.append(f"CODE_INJECTION:{category}")

        return len(violations) > 0, violations

    def check_model(self, text: str) -> Tuple[bool, List[str]]:
        """Проверка извлечения модели"""
        violations = []

        for category, pattern in self.model_patterns.items():
            if pattern.search(text):
                violations.append(f"MODEL_EXTRACTION:{category}")

        return len(violations) > 0, violations


class FastParallelSafetyValidator:
    """
    Быстрая параллельная система проверки безопасности
    Только regex проверки для максимальной скорости
    """

    def __init__(self):
        logger.info("🚀 Инициализация быстрого параллельного валидатора...")

        self.regex_checker = FastRegexChecker()
        self.max_workers = 4  # Количество параллельных потоков
        self.timeout = 10.0  # Таймаут для проверок
        self.max_text_length = 100_000  # Максимальная длина текста (100KB)
        self.segment_size = 5_000  # Размер сегмента для больших текстов
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        )

        logger.info("✅ Быстрый валидатор готов")

    @property
    def pii_patterns(self):
        """Доступ к PII паттернам"""
        return self.regex_checker.pii_patterns

    @property
    def sensitive_data_patterns(self):
        """Доступ к паттернам чувствительных данных"""
        return self.regex_checker.sensitive_patterns

    @property
    def code_injection_patterns(self):
        """Доступ к паттернам инъекций кода"""
        return self.regex_checker.code_patterns

    @property
    def model_extraction_patterns(self):
        """Доступ к паттернам извлечения модели"""
        return self.regex_checker.model_patterns

    async def validate_output_async(
        self, text: str, user_id: Optional[str] = None
    ) -> SafetyResult:
        """Асинхронная валидация с параллельными проверками"""
        start_time = datetime.now()

        # Проверяем лимиты
        if len(text) > self.max_text_length:
            logger.warning(
                f"⚠️ Текст превышает лимит: {len(text)} > {self.max_text_length}"
            )
            return SafetyResult(
                is_safe=False,
                violations=["TEXT_TOO_LONG"],
                risk_score=1.0,
                sanitized_text="*** ТЕКСТ СЛИШКОМ ДЛИННЫЙ ***",
                processing_time=0.001,
                details={
                    "text_length": len(text),
                    "max_length": self.max_text_length,
                    "user_id": user_id,
                },
                confidence_score=1.0,
            )

        logger.info(
            "Начало быстрой проверки",
            extra={
                "user_id": user_id,
                "text_length": len(text),
                "segment_size": (
                    self.segment_size if len(text) > self.segment_size else None
                ),
            },
        )

        # Для больших текстов используем сегментацию
        if len(text) > self.segment_size:
            return await self._validate_with_segmentation(text, user_id, start_time)

        # Создаем задачи для параллельного выполнения всех проверок
        tasks = [
            self._run_pii_check(text),
            self._run_sensitive_check(text),
            self._run_code_check(text),
            self._run_model_check(text),
        ]

        # Запускаем все проверки параллельно
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Собираем результаты
        pii_result = (
            results[0] if not isinstance(results[0], Exception) else (False, [], text)
        )
        sensitive_result = (
            results[1] if not isinstance(results[1], Exception) else (False, [], text)
        )
        code_result = (
            results[2] if not isinstance(results[2], Exception) else (False, [])
        )
        model_result = (
            results[3] if not isinstance(results[3], Exception) else (False, [])
        )

        # Объединяем результаты
        final_result = self._combine_results(
            text, pii_result, sensitive_result, code_result, model_result, user_id
        )

        # Время обработки
        processing_time = (datetime.now() - start_time).total_seconds()
        final_result.processing_time = processing_time

        logger.info(
            "Быстрая проверка завершена",
            extra={
                "user_id": user_id,
                "processing_time": processing_time,
                "risk_score": final_result.risk_score,
                "violations_count": len(final_result.violations),
                "is_safe": final_result.is_safe,
            },
        )

        return final_result

    def validate_output(self, text: str, user_id: Optional[str] = None) -> SafetyResult:
        """Синхронная обертка для асинхронной валидации"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self.validate_output_async(text, user_id))

    async def _validate_with_segmentation(
        self, text: str, user_id: Optional[str], start_time: datetime
    ) -> SafetyResult:
        """Валидация больших текстов с сегментацией"""
        logger.info(
            "Сегментация текста",
            extra={
                "user_id": user_id,
                "text_length": len(text),
                "segment_size": self.segment_size,
                "segments_count": len(text) // self.segment_size + 1,
            },
        )

        segments = [
            text[i : i + self.segment_size]
            for i in range(0, len(text), self.segment_size)
        ]
        all_violations = []
        all_sanitized_parts = []
        max_risk = 0.0

        # Обрабатываем сегменты параллельно (но ограниченно для предотвращения перегрузки)
        semaphore = asyncio.Semaphore(self.max_workers)

        async def process_segment(segment: str, idx: int):
            async with semaphore:
                segment_result = await self._process_single_segment(segment)
                return idx, segment_result

        # Создаем задачи для всех сегментов
        segment_tasks = [
            process_segment(segment, idx) for idx, segment in enumerate(segments)
        ]
        segment_results = await asyncio.gather(*segment_tasks, return_exceptions=True)

        # Объединяем результаты сегментов
        for idx, result in segment_results:
            if isinstance(result, Exception):
                logger.error(f"Ошибка в сегменте {idx}: {result}")
                continue

            segment_violations, segment_sanitized, segment_risk = result
            all_violations.extend(segment_violations)
            all_sanitized_parts.append(segment_sanitized)
            max_risk = max(max_risk, segment_risk)

        # Собираем итоговый результат
        sanitized_text = "".join(all_sanitized_parts)

        # Определяем безопасность
        high_risk_types = ["CODE_INJECTION", "MODEL_EXTRACTION", "PII_DETECTED"]
        high_risk_violations = [
            v for v in all_violations if any(v.startswith(t) for t in high_risk_types)
        ]
        is_safe = len(high_risk_violations) == 0 and max_risk < 0.7

        processing_time = (datetime.now() - start_time).total_seconds()

        details = {
            "text_length": len(text),
            "segments_count": len(segments),
            "violations_count": len(all_violations),
            "max_segment_risk": max_risk,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
        }

        logger.info(
            "Сегментация завершена",
            extra={
                "user_id": user_id,
                "segments_count": len(segments),
                "max_risk": max_risk,
                "violations_count": len(all_violations),
                "processing_time": processing_time,
            },
        )

        return SafetyResult(
            is_safe=is_safe,
            violations=all_violations,
            risk_score=max_risk,
            sanitized_text=sanitized_text,
            processing_time=processing_time,
            details=details,
            confidence_score=1.0,
        )

    async def _run_pii_check(self, text: str) -> Tuple[bool, List[str], str]:
        """Запуск PII проверки в отдельном потоке"""

        def run_check():
            return self.regex_checker.check_pii(text)

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self.executor, run_check)
        return await asyncio.wait_for(future, timeout=self.timeout)

    async def _run_sensitive_check(self, text: str) -> Tuple[bool, List[str], str]:
        """Запуск проверки чувствительных данных в отдельном потоке"""

        def run_check():
            return self.regex_checker.check_sensitive(text)

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self.executor, run_check)
        return await asyncio.wait_for(future, timeout=self.timeout)

    async def _run_code_check(self, text: str) -> Tuple[bool, List[str]]:
        """Запуск проверки кода в отдельном потоке"""

        def run_check():
            return self.regex_checker.check_code(text)

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self.executor, run_check)
        return await asyncio.wait_for(future, timeout=self.timeout)

    async def _run_model_check(self, text: str) -> Tuple[bool, List[str]]:
        """Запуск проверки извлечения модели в отдельном потоке"""

        def run_check():
            return self.regex_checker.check_model(text)

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self.executor, run_check)
        return await asyncio.wait_for(future, timeout=self.timeout)

    def _combine_results(
        self,
        text: str,
        pii_result: Tuple,
        sensitive_result: Tuple,
        code_result: Tuple,
        model_result: Tuple,
        user_id: Optional[str],
    ) -> SafetyResult:
        """Объединение результатов всех проверок"""

        all_violations = []
        sanitized_text = text

        # Обрабатываем PII результаты
        has_pii, pii_violations, pii_sanitized = pii_result
        if has_pii:
            all_violations.extend(pii_violations)
            sanitized_text = pii_sanitized

        # Обрабатываем чувствительные данные
        has_sensitive, sensitive_violations, sensitive_sanitized = sensitive_result
        if has_sensitive:
            all_violations.extend(sensitive_violations)
            # Применяем дополнительную санитизацию
            if sensitive_sanitized != text:
                sanitized_text = sensitive_sanitized

        # Обрабатываем код
        has_code, code_violations = code_result
        if has_code:
            all_violations.extend(code_violations)

        # Обрабатываем модель
        has_model, model_violations = model_result
        if has_model:
            all_violations.extend(model_violations)

        # Вычисляем риск (простая формула)
        risk_score = self._calculate_risk(all_violations)

        # Определяем безопасность
        high_risk_types = ["CODE_INJECTION", "MODEL_EXTRACTION", "PII_DETECTED"]
        high_risk_violations = [
            v for v in all_violations if any(v.startswith(t) for t in high_risk_types)
        ]

        is_safe = len(high_risk_violations) == 0 and risk_score < 0.7

        # Детали
        details = {
            "text_length": len(text),
            "violations_count": len(all_violations),
            "has_pii": has_pii,
            "has_sensitive_data": has_sensitive,
            "has_code_injection": has_code,
            "has_model_extraction": has_model,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
        }

        return SafetyResult(
            is_safe=is_safe,
            violations=all_violations,
            risk_score=risk_score,
            sanitized_text=sanitized_text,
            processing_time=0.0,  # Будет установлено позже
            details=details,
        )

    async def _process_single_segment(
        self, segment: str
    ) -> Tuple[List[str], str, float]:
        """Обработка одного сегмента текста"""
        # Создаем задачи для параллельного выполнения всех проверок
        tasks = [
            self._run_pii_check(segment),
            self._run_sensitive_check(segment),
            self._run_code_check(segment),
            self._run_model_check(segment),
        ]

        # Запускаем все проверки параллельно
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Собираем результаты
        pii_result = (
            results[0]
            if not isinstance(results[0], Exception)
            else (False, [], segment)
        )
        sensitive_result = (
            results[1]
            if not isinstance(results[1], Exception)
            else (False, [], segment)
        )
        code_result = (
            results[2] if not isinstance(results[2], Exception) else (False, [])
        )
        model_result = (
            results[3] if not isinstance(results[3], Exception) else (False, [])
        )

        # Объединяем нарушения
        all_violations = []
        sanitized_text = segment

        # Обрабатываем PII результаты
        has_pii, pii_violations, pii_sanitized = pii_result
        if has_pii:
            all_violations.extend(pii_violations)
            sanitized_text = pii_sanitized

        # Обрабатываем чувствительные данные
        has_sensitive, sensitive_violations, sensitive_sanitized = sensitive_result
        if has_sensitive:
            all_violations.extend(sensitive_violations)
            if sensitive_sanitized != segment:
                sanitized_text = sensitive_sanitized

        # Обрабатываем код
        has_code, code_violations = code_result
        if has_code:
            all_violations.extend(code_violations)

        # Обрабатываем модель
        has_model, model_violations = model_result
        if has_model:
            all_violations.extend(model_violations)

        # Вычисляем риск для сегмента
        risk_score = self._calculate_risk(all_violations)

        return all_violations, sanitized_text, risk_score

    def _calculate_risk(self, violations: List[str]) -> float:
        """Улучшенное вычисление риска"""
        if not violations:
            return 0.0

        risk_weights = {
            "PII_DETECTED": 0.8,
            "SENSITIVE_DATA": 0.7,
            "CODE_INJECTION": 0.9,
            "MODEL_EXTRACTION": 0.8,
        }

        max_risk = 0.0
        total_weighted_risk = 0.0

        for violation in violations:
            for violation_type, weight in risk_weights.items():
                if violation.startswith(violation_type):
                    max_risk = max(max_risk, weight)
                    total_weighted_risk += weight
                    break

        # Используем комбинацию максимального риска и среднего
        # Если есть критические нарушения (CODE_INJECTION) - риск высокий
        avg_risk = total_weighted_risk / len(violations)
        combined_risk = max_risk * 0.7 + avg_risk * 0.3

        return min(combined_risk, 1.0)
