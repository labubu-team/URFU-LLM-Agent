FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock* ./

RUN pip install uv

RUN uv sync --no-dev

COPY output-safety/fast_parallel_validator.py output-safety/api_service.py ./

RUN useradd --create-home --shell /bin/bash app && \
    chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import json,sys,urllib.request; \
u='http://127.0.0.1:8000/healthz'; \
try: \
    r=urllib.request.urlopen(u,timeout=3); \
    ok=(r.getcode()==200); \
    body=r.read() or b'{}'; \
    s=json.loads(body.decode('utf-8','ignore')); \
    sys.exit(0 if ok and s.get('status')=='ok' else 1); \
except Exception: \
    sys.exit(1)"

CMD ["uv", "run", "uvicorn", "api_service:app", "--host", "0.0.0.0", "--port", "8000"]
