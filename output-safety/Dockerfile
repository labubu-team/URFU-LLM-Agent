FROM python:3.12-slim

WORKDIR /app

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

# Копируем файлы проекта
COPY pyproject.toml uv.lock* ./

# Устанавливаем uv для управления зависимостями
RUN pip install uv

# Устанавливаем зависимости
RUN uv sync --no-dev

# Копируем код приложения
COPY output-safety/fast_parallel_validator.py output-safety/api_service.py ./

# Создаем пользователя для безопасности
RUN useradd --create-home --shell /bin/bash app && \
    chown -R app:app /app

USER app

# Открываем порт
EXPOSE 8003

# Запускаем приложение
CMD ["uv", "run", "uvicorn", "api_service:app", "--host", "0.0.0.0", "--port", "8003"]
