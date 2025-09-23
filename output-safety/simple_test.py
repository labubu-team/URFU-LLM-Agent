# -*- coding: utf-8 -*-
"""
Упрощенная версия тестов без эмодзи для Windows
"""

import sys
import os

# Устанавливаем кодировку для Windows
if sys.platform.startswith("win"):
    os.environ["PYTHONIOENCODING"] = "utf-8"

from fast_parallel_validator import FastParallelSafetyValidator


def test_basic_functionality():
    """Базовый тест функционала"""
    print("ТЕСТ: Базовый функционал")
    print("-" * 40)

    validator = FastParallelSafetyValidator()

    # Тест 1: Безопасный текст
    safe_text = "Привет! Как дела? Сегодня хорошая погода."
    result = validator.validate_output(safe_text)

    print("Тест 1 - Безопасный текст:")
    print(f"  Безопасно: {result.is_safe}")
    print(f"  Риск: {result.risk_score:.3f}")
    print(f"  Время: {result.processing_time:.3f}s")
    assert result.is_safe, "Безопасный текст должен быть безопасным"
    print("  [OK] Тест пройден")

    # Тест 2: Email
    email_text = "Мой email: user@example.com"
    result = validator.validate_output(email_text)

    print("\nТест 2 - Email:")
    print(f"  Безопасно: {result.is_safe}")
    print(f"  Риск: {result.risk_score:.3f}")
    print(f"  Нарушений: {len(result.violations)}")
    print(f"  Очищенный: {result.sanitized_text}")
    assert not result.is_safe, "Текст с email должен быть небезопасным"
    print("  [OK] Тест пройден")

    # Тест 3: SQL инъекция
    sql_text = "SELECT * FROM users WHERE id = 1 OR '1'='1'"
    result = validator.validate_output(sql_text)

    print("\nТест 3 - SQL инъекция:")
    print(f"  Безопасно: {result.is_safe}")
    print(f"  Риск: {result.risk_score:.3f}")
    print(f"  Нарушений: {len(result.violations)}")
    assert not result.is_safe, "SQL инъекция должна быть небезопасной"
    assert result.risk_score > 0.8, "SQL инъекция должна иметь высокий риск"
    print("  [OK] Тест пройден")

    # Тест 4: Российский телефон
    phone_text = "Звоните: +7-999-123-45-67"
    result = validator.validate_output(phone_text)

    print("\nТест 4 - Российский телефон:")
    print(f"  Безопасно: {result.is_safe}")
    print(f"  Риск: {result.risk_score:.3f}")
    print(f"  Нарушений: {len(result.violations)}")
    assert not result.is_safe, "Текст с телефоном должен быть небезопасным"
    print("  [OK] Тест пройден")

    print("\nВСЕ БАЗОВЫЕ ТЕСТЫ ПРОЙДЕНЫ!")


def test_russian_patterns():
    """Тест российских паттернов"""
    print("\nТЕСТ: Российские паттерны")
    print("-" * 40)

    from fast_parallel_validator import FastRegexChecker

    checker = FastRegexChecker()

    # Российские телефоны
    phones = ["+7-999-123-45-67", "8(999)123-45-67", "89991234567"]

    for phone in phones:
        detected, violations, sanitized = checker.check_pii(phone)
        print(f"Телефон {phone}: {'Обнаружен' if detected else 'НЕ обнаружен'}")
        assert detected, f"Должен обнаружить российский телефон: {phone}"

    # Email с кириллицей
    emails = ["user@example.com", "test@компания.рф"]

    for email in emails:
        detected, violations, sanitized = checker.check_pii(email)
        print(f"Email {email}: {'Обнаружен' if detected else 'НЕ обнаружен'}")
        assert detected, f"Должен обнаружить email: {email}"

    # Российские документы
    docs = [
        "123-456-789-12",  # СНИЛС
        "1234567890",  # ИНН юрлица
        "123456789012",  # ИНН физлица
        "1234 567890",  # Паспорт
    ]

    for doc in docs:
        detected, violations, sanitized = checker.check_pii(doc)
        print(f"Документ {doc}: {'Обнаружен' if detected else 'НЕ обнаружен'}")
        assert detected, f"Должен обнаружить документ: {doc}"

    print("ВСЕ РОССИЙСКИЕ ПАТТЕРНЫ РАБОТАЮТ!")


def test_performance():
    """Тест производительности"""
    print("\nТЕСТ: Производительность")
    print("-" * 40)

    validator = FastParallelSafetyValidator()

    # Короткий текст
    short_text = "Краткий текст с email: user@test.com"
    result = validator.validate_output(short_text)
    print(f"Короткий текст ({len(short_text)} символов): {result.processing_time:.3f}s")
    assert result.processing_time < 0.1, "Короткий текст должен обрабатываться быстро"

    # Длинный текст
    long_text = "Длинный текст " * 500 + " с опасностью: SELECT * FROM users"
    result = validator.validate_output(long_text)
    print(f"Длинный текст ({len(long_text)} символов): {result.processing_time:.3f}s")

    if "segments_count" in result.details:
        print(f"Использована сегментация: {result.details['segments_count']} сегментов")

    # Слишком длинный текст
    huge_text = "x" * 150_000
    result = validator.validate_output(huge_text)
    print(
        f"Огромный текст ({len(huge_text)} символов): {'Заблокирован' if not result.is_safe else 'Пропущен'}"
    )
    assert not result.is_safe, "Слишком длинный текст должен блокироваться"
    assert "TEXT_TOO_LONG" in result.violations, "Должно быть нарушение TEXT_TOO_LONG"

    print("ТЕСТЫ ПРОИЗВОДИТЕЛЬНОСТИ ПРОЙДЕНЫ!")


def main():
    """Главная функция"""
    print("СИСТЕМА БЕЗОПАСНОСТИ ВЫВОДА LLM - ПРОСТЫЕ ТЕСТЫ")
    print("=" * 60)

    try:
        test_basic_functionality()
        test_russian_patterns()
        test_performance()

        print("\n" + "=" * 60)
        print("ВСЕ ТЕСТЫ УСПЕШНО ЗАВЕРШЕНЫ!")
        print("Система работает корректно.")

    except AssertionError as e:
        print(f"\n[ОШИБКА] Тест провален: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ОШИБКА] Неожиданная ошибка: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
