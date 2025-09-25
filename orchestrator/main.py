import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

import aiohttp
import jwt  # PyJWT
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

_DEFAULT = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
}


class YCJsonFormatter(logging.Formatter):
    def format(self, rec: logging.LogRecord) -> str:
        sev = (
            {"WARNING": "WARN", "CRITICAL": "FATAL"}
            .get(rec.levelname, rec.levelname)
            .upper()
        )
        payload: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(rec.created))
            + f".{int(rec.msecs):03d}Z",
            "severity": sev,
            "logger": rec.name,
            "event": getattr(rec, "event", rec.funcName or "log"),
            "message": rec.getMessage(),
        }
        for k, v in rec.__dict__.items():
            if k not in _DEFAULT and not k.startswith("_"):
                payload[k] = v
        if rec.exc_info:
            payload["exception"] = self.formatException(rec.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _setup_logging():
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    h = logging.StreamHandler(stream=sys.stdout)
    h.setFormatter(YCJsonFormatter())
    root.addHandler(h)


_setup_logging()
log = logging.getLogger("app")


def jinfo(msg: str, **extra):
    log.info(msg, extra=extra)


def jerror(msg: str, **extra):
    log.error(msg, extra=extra)


# ---------- config ----------
@dataclass
class ServiceEndpoint:
    name: str
    url: str
    health_path: str = "/"


@dataclass
class OrchestratorConfig:
    NODE_ID: str = os.getenv("NODE_ID", "")
    FOLDER_ID: str = os.getenv("FOLDER_ID", "")
    SERVICE_ACCOUNT_ID: str = os.getenv("SERVICE_ACCOUNT_ID", "")
    PUBLIC_KEY: str = os.getenv("PUBLIC_KEY", "")
    PRIVATE_KEY: str = os.getenv("PRIVATE_KEY", "")
    KEY_ID: str = os.getenv("KEY_ID", "")

    moderation_regex_url: str = os.getenv(
        "MODERATION_REGEX_URL", "http://moderation-regex:8000"
    )
    moderation_nlp_url: str = os.getenv(
        "MODERATION_NLP_URL", "http://moderation-nlp:8000"
    )
    rag_url: str = os.getenv("RAG_URL", "http://rag:8000")
    llm_agent_url: str = os.getenv("LLM_AGENT_URL", "http://llm-agent:8000")

    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "100"))
    health_check_timeout: int = int(os.getenv("HEALTH_CHECK_TIMEOUT", "5"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))
    retry_delay: float = float(os.getenv("RETRY_DELAY", "1.0"))

    @property
    def services(self) -> list[ServiceEndpoint]:
        return [
            ServiceEndpoint("moderation-regex", self.moderation_regex_url, "/"),
            ServiceEndpoint("moderation-nlp", self.moderation_nlp_url, "/"),
            ServiceEndpoint("rag", self.rag_url, "/health"),
            ServiceEndpoint("llm-agent", self.llm_agent_url, "/"),
        ]


config = OrchestratorConfig()


# ---------- models ----------
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


# ---------- shared async HTTP client with retries ----------
class AsyncHTTP:
    def __init__(self, request_timeout: int, max_retries: int, retry_delay: float):
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._max_retries = max_retries
        self._retry_delay = retry_delay

    async def start(self):
        if not self._session:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def _call(
        self,
        method: str,
        url: str,
        *,
        json_body: Any | None = None,
        headers: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        assert self._session, "HTTP session is not started"
        tries = 0
        while True:
            t0 = time.perf_counter()
            try:
                async with self._session.request(
                    method, url, json=json_body, headers=headers
                ) as r:
                    content = await r.text()
                    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                    jinfo(
                        "http_call",
                        event="http_call",
                        url=url,
                        method=method,
                        status=r.status,
                        duration_ms=duration_ms,
                        bytes=len(content),
                    )
                    r.raise_for_status()
                    if content:
                        return json.loads(content)
                    return {}
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                tries += 1
                if tries <= self._max_retries:
                    jinfo(
                        "http_retry",
                        event="http_retry",
                        url=url,
                        method=method,
                        tries=tries,
                        error=str(e),
                    )
                    await asyncio.sleep(self._retry_delay * tries)
                    continue
                jerror(
                    "http_failed",
                    event="http_failed",
                    url=url,
                    method=method,
                    error=str(e),
                )
                raise

    async def get(
        self, url: str, *, headers: Dict[str, str] | None = None
    ) -> Dict[str, Any]:
        return await self._call("GET", url, headers=headers)

    async def post(
        self, url: str, *, json_body: Any, headers: Dict[str, str] | None = None
    ) -> Dict[str, Any]:
        return await self._call("POST", url, json_body=json_body, headers=headers)


http = AsyncHTTP(config.request_timeout, config.max_retries, config.retry_delay)


# ---------- IAM token provider (shared) ----------
class IAMTokenProvider:
    def __init__(self, cfg: OrchestratorConfig):
        self.cfg = cfg
        self._iam_token: Optional[str] = None
        self._expires_at: int = 0  # epoch seconds

    async def get_token(self) -> str:
        now = int(time.time())
        if self._iam_token and now < self._expires_at:
            return self._iam_token

        payload = {
            "aud": "https://iam.api.cloud.yandex.net/iam/v1/tokens",
            "iss": self.cfg.SERVICE_ACCOUNT_ID,
            "iat": now,
            "exp": now + 3600,
        }
        # PS256 requires PRIVATE_KEY in PKCS8
        encoded_jwt = jwt.encode(
            payload,
            self.cfg.PRIVATE_KEY,
            algorithm="PS256",
            headers={"kid": self.cfg.KEY_ID},
        )

        url = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
        data = {"jwt": encoded_jwt}
        # use bare aiohttp for tiny one-off? keep via shared http for uniform logs:
        resp = await http.post(url, json_body=data)
        token = resp.get("iamToken")
        if not token:
            raise RuntimeError("IAM token not in response")
        self._iam_token = token
        # expire slightly earlier
        self._expires_at = now + 3500
        jinfo("iam_token_ok", event="iam_token")
        return token


iam = IAMTokenProvider(config)


# ---------- request processor (no duplication) ----------
class RequestProcessor:
    def __init__(self, cfg: OrchestratorConfig):
        self.cfg = cfg

    async def check_regex_moderation(self, text: str) -> Tuple[bool, str]:
        url = f"{self.cfg.moderation_regex_url}/detect"
        result = await http.post(url, json_body={"text": text})
        inj = bool(result.get("injection", False))
        pattern = result.get("detected_pattern", "") or result.get("pattern", "")
        jinfo("regex_done", event="moderation_regex", injection=inj, pattern=pattern)
        return inj, pattern

    async def check_nlp_moderation(self, text: str) -> Tuple[bool, str, float]:
        url = f"{self.cfg.moderation_nlp_url}/classify"
        result = await http.post(url, json_body={"text": text})
        inj = bool(result.get("injection", False))
        label = result.get("label", "")
        score = float(result.get("score", 0.0) or 0.0)
        jinfo(
            "nlp_done", event="moderation_nlp", injection=inj, label=label, score=score
        )
        return inj, label, score

    async def get_rag_context(self, query: str) -> str:
        url = f"{self.cfg.rag_url}/search"
        result = await http.post(url, json_body={"query": query})
        ctx = result.get("context", "") or result.get("text", "")
        jinfo("rag_done", event="rag_search", ctx_len=len(ctx))
        return ctx

    async def get_llm_response(self, user_message: str, context: str = "") -> str:
        url = f"{self.cfg.llm_agent_url}/v1/completion"
        msg_text = (
            f"Контекст: {context}\n\nВопрос пользователя: {user_message}"
            if context
            else user_message
        )

        # If LLM requires YC IAM/FOLDER headers, attach them; otherwise harmless.
        headers = {
            "x-node-id": self.cfg.NODE_ID,
            "x-folder-id": self.cfg.FOLDER_ID,
        }
        # Optionally add Authorization if your LLM expects YC IAM:
        if self.cfg.SERVICE_ACCOUNT_ID and self.cfg.PRIVATE_KEY and self.cfg.KEY_ID:
            try:
                token = await iam.get_token()
                headers["Authorization"] = f"Bearer {token}"
            except Exception as e:
                # continue without token if not needed
                jerror("iam_fail_for_llm", event="iam_token_llm", error=str(e))

        result = await http.post(
            url,
            json_body={"messages": [{"role": "user", "text": msg_text}]},
            headers=headers,
        )
        # normalize field name
        alt = (
            result.get("result", {})
            .get("alternatives", [{}])[0]
            .get("message", {})
            .get("text")
        )
        resp = result.get("response") or result.get("text") or alt
        if not isinstance(resp, str):
            resp = "Извините, произошла ошибка при генерации ответа"
        jinfo("llm_done", event="llm_completion", resp_len=len(resp))
        return resp

    async def process(self, req: UserRequest) -> ProcessingResult:
        t0 = time.time()
        jinfo("process_start", event="process_start", user_id=req.user_id)

        try:
            # 1) regex (fast block)
            blocked, pattern = await self.check_regex_moderation(req.message)
            if blocked:
                dt = time.time() - t0
                return ProcessingResult(
                    status=ProcessingStatus.MODERATION_BLOCKED,
                    response="Ваш запрос содержит недопустимый контент.",
                    blocked_reason=f"Regex: {pattern}",
                    processing_time=dt,
                )

            # 2) nlp (probabilistic block)
            blocked, label, score = await self.check_nlp_moderation(req.message)
            if blocked:
                dt = time.time() - t0
                return ProcessingResult(
                    status=ProcessingStatus.MODERATION_BLOCKED,
                    response="Ваш запрос содержит потенциально вредоносный контент.",
                    blocked_reason=f"NLP: {label} (score: {score})",
                    processing_time=dt,
                )

            # 3) RAG → 4) LLM
            ctx = await self.get_rag_context(req.message)
            llm_text = await self.get_llm_response(req.message, ctx)

            dt = time.time() - t0
            jinfo("process_ok", event="process_ok", duration_ms=round(dt * 1000, 1))
            return ProcessingResult(
                status=ProcessingStatus.SUCCESS, response=llm_text, processing_time=dt
            )

        except HTTPException:
            raise
        except Exception as e:
            dt = time.time() - t0
            jerror("process_fail", event="process_fail", error=str(e))
            return ProcessingResult(
                status=ProcessingStatus.ERROR,
                response="Произошла внутренняя ошибка сервера.",
                processing_time=dt,
            )


processor = RequestProcessor(config)


# ---------- health checks via shared client ----------
async def check_all_services() -> Dict[str, bool]:
    tasks = []
    for s in config.services:
        url = f"{s.url}{s.health_path}"
        tasks.append(http.get(url))
    results: Dict[str, bool] = {}
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    for s, r in zip(config.services, gathered):
        ok = not isinstance(r, Exception)
        results[s.name] = ok
        if not ok:
            jinfo("svc_down", event="service_health", service=s.name)
    return results


# ---------- FastAPI app ----------
app = FastAPI(
    title="LLM Agent Orchestrator",
    description="Оркестратор для системы защищенного LLM агента с Telegram ботом",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    jinfo("startup", event="startup")
    await http.start()


@app.on_event("shutdown")
async def on_shutdown():
    jinfo("shutdown", event="shutdown")
    await http.close()


@app.get("/", response_model=HealthCheckResponse)
async def health_root():
    try:
        services_status = await check_all_services()
        return HealthCheckResponse(status="healthy", services=services_status)
    except Exception as e:
        jerror("health_fail", event="health_check", error=str(e))
        raise HTTPException(status_code=500, detail="Ошибка проверки здоровья")


@app.get("/services/status")
async def services_status():
    try:
        services_status = await check_all_services()
        return {
            "orchestrator": "healthy",
            "services": services_status,
            "all_healthy": all(services_status.values()),
        }
    except Exception as e:
        jerror("services_status_fail", event="services_status", error=str(e))
        raise HTTPException(status_code=500, detail="Ошибка получения статуса сервисов")


@app.post("/process", response_model=ProcessingResult)
async def process_user_request(request: UserRequest):
    try:
        return await processor.process(request)
    except HTTPException:
        raise
    except Exception as e:
        jerror("endpoint_fail", event="process_endpoint", error=str(e))
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    jinfo("run_uvicorn", event="uvicorn_run", host=host, port=port)
    uvicorn.run("main:app", host=host, port=port, reload=False, log_level="info")
