import os

import uvicorn
from fastapi import FastAPI, HTTPException

from moder import detect_injection, get_detected_pattern
from pydantic import BaseModel

app = FastAPI(title="Moderation Patterns API", version="1.0")


class TextIn(BaseModel):
    text: str


class DetectOut(BaseModel):
    injection: bool
    detected_pattern: str = ""


@app.get("/")
def root():
    return {
        "message": 'Moderation patterns API. POST /detect with JSON {"text": "..."}'
    }


@app.get("/healthz")
def healthz():
    try:
        _ = bool(detect_injection(""))
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/detect", response_model=DetectOut)
def detect(payload: TextIn):
    try:
        text = payload.text
        inj = detect_injection(text)
        pattern = get_detected_pattern(text) if inj else ""
        return DetectOut(injection=inj, detected_pattern=pattern)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(
        "moder_api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=bool(int(os.getenv("RELOAD", "0"))),
    )
