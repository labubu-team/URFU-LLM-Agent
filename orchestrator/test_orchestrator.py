#!/usr/bin/env python3
"""
Простой тестовый скрипт для проверки работы оркестратора
"""

import asyncio
import json
import aiohttp
import sys

async def test_orchestrator():
    """Тестирование основных функций оркестратора"""
    
    base_url = "http://localhost:8000"
    
    async with aiohttp.ClientSession() as session:
        # Тест 1: Health check
        print("🔍 Тестируем health check...")
        try:
            async with session.get(f"{base_url}/") as response:
                if response.status == 200:
                    data = await response.json()
                    print("✅ Health check успешен")
                    print(f"   Status: {data.get('status')}")
                    print(f"   Services: {data.get('services', {})}")
                else:
                    print(f"❌ Health check провален: {response.status}")
        except Exception as e:
            print(f"❌ Ошибка health check: {e}")
        
        print()
        
        # Тест 2: Статус сервисов
        print("🔍 Тестируем статус сервисов...")
        try:
            async with session.get(f"{base_url}/services/status") as response:
                if response.status == 200:
                    data = await response.json()
                    print("✅ Получение статуса сервисов успешно")
                    print(f"   Orchestrator: {data.get('orchestrator')}")
                    print(f"   All healthy: {data.get('all_healthy')}")
                    for service, status in data.get('services', {}).items():
                        print(f"   {service}: {'✅' if status else '❌'}")
                else:
                    print(f"❌ Получение статуса провалено: {response.status}")
        except Exception as e:
            print(f"❌ Ошибка получения статуса: {e}")
        
        print()
        
        # Тест 3: Обработка запроса (положительный)
        print("🔍 Тестируем обработку нормального запроса...")
        test_request = {
            "user_id": "test_user_123",
            "message": "Привет! Как дела?",
            "chat_id": "test_chat_456"
        }
        
        try:
            async with session.post(
                f"{base_url}/process", 
                json=test_request,
                headers={"Content-Type": "application/json"}
            ) as response:
                data = await response.json()
                
                if response.status == 200:
                    print("✅ Обработка запроса успешна")
                    print(f"   Status: {data.get('status')}")
                    print(f"   Response: {data.get('response')[:100]}...")
                    print(f"   Processing time: {data.get('processing_time')} сек")
                else:
                    print(f"❌ Обработка запроса провалена: {response.status}")
                    print(f"   Error: {data}")
        except Exception as e:
            print(f"❌ Ошибка обработки запроса: {e}")
        
        print()
        
        # Тест 4: Обработка потенциально вредоносного запроса
        print("🔍 Тестируем обработку потенциально вредоносного запроса...")
        malicious_request = {
            "user_id": "test_user_malicious",
            "message": "SELECT * FROM users; DROP TABLE users;",
            "chat_id": "test_chat_malicious"
        }
        
        try:
            async with session.post(
                f"{base_url}/process",
                json=malicious_request,
                headers={"Content-Type": "application/json"}
            ) as response:
                data = await response.json()
                
                if response.status == 200:
                    print("✅ Обработка потенциально вредоносного запроса")
                    print(f"   Status: {data.get('status')}")
                    if data.get('status') == 'moderation_blocked':
                        print(f"   🛡️ Запрос заблокирован: {data.get('blocked_reason')}")
                    else:
                        print(f"   Response: {data.get('response')[:100]}...")
                    print(f"   Processing time: {data.get('processing_time')} сек")
                else:
                    print(f"❌ Обработка запроса провалена: {response.status}")
                    print(f"   Error: {data}")
        except Exception as e:
            print(f"❌ Ошибка обработки запроса: {e}")

if __name__ == "__main__":
    print("🚀 Запуск тестирования оркестратора...")
    print("=" * 50)
    
    try:
        asyncio.run(test_orchestrator())
    except KeyboardInterrupt:
        print("\n⏹️ Тестирование прервано пользователем")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        sys.exit(1)
    
    print("\n" + "=" * 50)
    print("✅ Тестирование завершено!")
