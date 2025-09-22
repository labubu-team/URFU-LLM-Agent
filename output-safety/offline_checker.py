# -*- coding: utf-8 -*-
"""
Offline версия проверки безопасности вывода нейросетей
Автономная работа без веб-сервера
"""

import sys
import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List

# Устанавливаем кодировку для Windows
if sys.platform.startswith('win'):
    os.environ['PYTHONIOENCODING'] = 'utf-8'

from fast_parallel_validator import FastParallelSafetyValidator, SafetyResult

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class OfflineOutputSafety:
    """
    Автономный класс для проверки безопасности вывода
    Идентичная логика с API, но без веб-интерфейса
    """
    
    def __init__(self):
        """
        Инициализация offline валидатора (только regex)
        """
        logger.info("🛡️ Инициализация Offline Output Safety...")
        
        try:
            self.validator = FastParallelSafetyValidator()
            logger.info("✅ Offline быстрый валидатор готов")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации: {e}")
            raise e
    
    def check_safety(self, text: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Основная функция проверки безопасности
        
        Args:
            text: Текст для проверки
            user_id: ID пользователя (опционально)
            
        Returns:
            Dict с результатами проверки
        """
        try:
            logger.info(f"🔍 Проверка безопасности для пользователя {user_id}, длина: {len(text)}")
            
            result = self.validator.validate_output(text, user_id)
            
            response = {
                'status': 'success',
                'is_safe': result.is_safe,
                'risk_score': result.risk_score,
                'violations_count': len(result.violations),
                'violations': result.violations,
                'sanitized_text': result.sanitized_text,
                'details': result.details,
                'processing_time': result.processing_time,
                'timestamp': datetime.now().isoformat()
            }
            
            # Логируем результат
            status_emoji = "✅" if result.is_safe else "⚠️"
            logger.info(f"{status_emoji} Результат: безопасно={result.is_safe}, "
                       f"риск={result.risk_score:.3f}")
            
            return response
            
        except Exception as e:
            logger.error(f"❌ Ошибка проверки безопасности: {e}", exc_info=True)
            return {
                'status': 'error',
                'error': str(e),
                'is_safe': False,
                'sanitized_text': '*** ОШИБКА ПРОВЕРКИ БЕЗОПАСНОСТИ ***',
                'timestamp': datetime.now().isoformat()
            }
    
    def sanitize_text(self, text: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Санитизация текста
        
        Args:
            text: Текст для санитизации
            user_id: ID пользователя (опционально)
            
        Returns:
            Dict с результатами санитизации
        """
        try:
            logger.info(f"🧹 Санитизация для пользователя {user_id}")
            
            result = self.validator.validate_output(text, user_id)
            changes_made = result.sanitized_text != text
            
            response = {
                'status': 'success',
                'original_text': text,
                'sanitized_text': result.sanitized_text,
                'changes_made': changes_made,
                'risk_score': result.risk_score,
                'confidence_score': result.confidence_score,
                'timestamp': datetime.now().isoformat()
            }
            
            logger.info(f"🧹 Санитизация завершена: изменения={changes_made}")
            
            return response
            
        except Exception as e:
            logger.error(f"❌ Ошибка санитизации: {e}", exc_info=True)
            return {
                'status': 'error',
                'error': str(e),
                'sanitized_text': '*** ОШИБКА САНИТИЗАЦИИ ***',
                'timestamp': datetime.now().isoformat()
            }
    
    def validate_llm_output(self, text: str, user_id: Optional[str] = None) -> Tuple[bool, str]:
        """
        Простая функция для быстрой проверки LLM вывода
        
        Args:
            text: Текст от LLM
            user_id: ID пользователя
            
        Returns:
            (is_safe, safe_text)
        """
        result = self.check_safety(text, user_id)
        
        if result['status'] == 'success':
            return result['is_safe'], result['sanitized_text']
        else:
            return False, result['sanitized_text']
    
    def analyze_text_detailed(self, text: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Детальный анализ текста с развернутой информацией
        
        Returns:
            Подробный отчет по всем аспектам безопасности
        """
        result = self.check_safety(text, user_id)
        
        if result['status'] != 'success':
            return result
        
        # Добавляем детальную аналитику
        analysis = {
            'basic_info': {
                'text_length': len(text),
                'is_safe': result['is_safe'],
                'overall_risk': result['risk_score'],
                'confidence': 0.9  # Высокая уверенность для regex
            },
            'security_checks': {
                'pii_detected': any('PII_DETECTED' in v for v in result['violations']),
                'sensitive_data': any('SENSITIVE_DATA' in v for v in result['violations']),
                'code_injection': any('CODE_INJECTION' in v for v in result['violations']),
                'model_extraction': any('MODEL_EXTRACTION' in v for v in result['violations'])
            },
            'regex_analysis': {'fast_parallel_checks': True},
            'segments_analysis': {},  # Не используем сегментацию в regex-версии,
            'violations': result['violations'],
            'recommendations': self._generate_recommendations(result)
        }
        
        return {
            'status': 'success',
            'analysis': analysis,
            'sanitized_text': result['sanitized_text'],
            'timestamp': result['timestamp']
        }
    
    def _generate_recommendations(self, result: Dict[str, Any]) -> List[str]:
        """Генерирует рекомендации на основе результатов"""
        recommendations = []
        
        if result['risk_score'] > 0.8:
            recommendations.append("⚠️ Высокий риск: рекомендуется заблокировать вывод")
        elif result['risk_score'] > 0.5:
            recommendations.append("⚡ Средний риск: рекомендуется дополнительная проверка")
        
        if any('PII_DETECTED' in v for v in result['violations']):
            recommendations.append("🔒 Обнаружены персональные данные - требуется санитизация")
        
        if any('CODE_INJECTION' in v for v in result['violations']):
            recommendations.append("💻 Обнаружен потенциально опасный код - блокировать")
        
        # Для regex проверок уверенность всегда высокая
        # (убираем проверку confidence_score)
        
        if not recommendations:
            recommendations.append("✅ Текст безопасен для вывода")
        
        return recommendations

def demo_offline_safety():
    """Демонстрация работы offline проверки безопасности"""
    
    print("🛡️ Демонстрация Enhanced Offline Output Safety")
    print("=" * 60)
    
    # Инициализация
    try:
        safety = OfflineOutputSafety()
    except Exception as e:
        print(f"❌ Ошибка инициализации: {e}")
        return
    
    # Тестовые случаи
    test_cases = [
        {
            "name": "Безопасный текст",
            "text": "Привет! Как дела? Сегодня хорошая погода."
        },
        {
            "name": "Email и телефон",
            "text": "Мой email: user@example.com, телефон: +7-999-123-45-67"
        },
        {
            "name": "SQL инъекция",
            "text": "SELECT * FROM users WHERE password = '123' OR 1=1"
        },
        {
            "name": "API ключ",
            "text": "api_key: sk-1234567890abcdef, используйте для доступа"
        },
        {
            "name": "Большой текст с рисками",
            "text": "Текст содержит email: admin@company.com и API ключ: sk-proj-abcd1234. " * 10
        }
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n📝 Тест {i}: {test_case['name']}")
        print(f"Текст: {test_case['text'][:100]}{'...' if len(test_case['text']) > 100 else ''}")
        print("-" * 40)
        
        # Быстрая проверка
        is_safe, safe_text = safety.validate_llm_output(test_case['text'], f"test_user_{i}")
        
        status_icon = "✅" if is_safe else "❌"
        print(f"{status_icon} Быстрая проверка: {'Безопасно' if is_safe else 'Небезопасно'}")
        
        # Детальный анализ
        detailed = safety.analyze_text_detailed(test_case['text'], f"test_user_{i}")
        
        if detailed['status'] == 'success':
            analysis = detailed['analysis']
            print(f"🎯 Риск: {analysis['basic_info']['overall_risk']:.3f}")
            print(f"🔍 Уверенность: {analysis['basic_info']['confidence']:.3f}")
            print(f"📊 Сегментов: {len(analysis['segments_analysis'])}")
            
            if analysis['violations']:
                print(f"⚠️ Нарушения: {len(analysis['violations'])}")
            
            print("💡 Рекомендации:")
            for rec in analysis['recommendations']:
                print(f"   {rec}")
    
    print(f"\n✨ Демонстрация завершена!")

def main():
    """CLI интерфейс для offline проверки"""
    
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python offline_checker.py \"текст для проверки\"")
        print("  python offline_checker.py --demo  # запуск демонстрации")
        print("  python offline_checker.py --file путь_к_файлу.txt")
        return
    
    if sys.argv[1] == "--demo":
        demo_offline_safety()
        return
    
    if sys.argv[1] == "--file" and len(sys.argv) > 2:
        try:
            with open(sys.argv[2], 'r', encoding='utf-8') as f:
                text_to_check = f.read()
            print(f"📄 Загружен файл: {sys.argv[2]} ({len(text_to_check)} символов)")
        except Exception as e:
            print(f"❌ Ошибка чтения файла: {e}")
            return
    else:
        text_to_check = sys.argv[1]
    
    # Инициализация
    try:
        safety = OfflineOutputSafety()
    except Exception as e:
        print(f"❌ Ошибка инициализации: {e}")
        return
    
    print("🔍 Проверка безопасности текста:")
    print(f"Текст: {text_to_check[:200]}{'...' if len(text_to_check) > 200 else ''}")
    print("=" * 50)
    
    # Детальный анализ
    result = safety.analyze_text_detailed(text_to_check, "cli_user")
    
    if result['status'] == 'success':
        analysis = result['analysis']
        basic = analysis['basic_info']
        
        status_icon = "✅" if basic['is_safe'] else "❌"
        print(f"{status_icon} Результат: {'Безопасно' if basic['is_safe'] else 'Небезопасно'}")
        print(f"🎯 Оценка риска: {basic['overall_risk']:.3f}")
        print(f"🔍 Уверенность: {basic['confidence']:.3f}")
        print(f"📏 Длина текста: {basic['text_length']} символов")
        
        if analysis['violations']:
            print(f"\n⚠️ Обнаружено нарушений: {len(analysis['violations'])}")
            for violation in analysis['violations'][:5]:  # Показываем первые 5
                print(f"   • {violation}")
            if len(analysis['violations']) > 5:
                print(f"   ... и еще {len(analysis['violations']) - 5}")
        
        print(f"\n💡 Рекомендации:")
        for rec in analysis['recommendations']:
            print(f"   {rec}")
        
        if result['sanitized_text'] != text_to_check:
            print(f"\n🧹 Очищенный текст:")
            print(f"{result['sanitized_text'][:300]}{'...' if len(result['sanitized_text']) > 300 else ''}")
        
        # Быстрые regex проверки
        regex_analysis = analysis.get('regex_analysis', {})
        if regex_analysis:
            print(f"\n⚡ Быстрые проверки: завершены за {result.get('processing_time', 0):.3f}s")
    
    else:
        print(f"❌ Ошибка: {result['error']}")

if __name__ == "__main__":
    main()
