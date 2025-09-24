import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

API_VERSION = "1.0"
app = FastAPI(title="Moderation NLP API", version=API_VERSION)

MODEL_NAME = "ProtectAI/deberta-v3-base-prompt-injection"


class TextIn(BaseModel):
    text: str


class ClassifyOut(BaseModel):
    injection: bool
    label: str
    score: float


# контейнер для pipeline
nlp_pipeline = {"pipe": None}


@app.on_event("startup")
def load_model():
    """
    Загружаем модель один раз при старте приложения.
    """
    try:
        device = 0 if torch.cuda.is_available() else -1
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
        nlp_pipeline["pipe"] = pipeline(
            "text-classification",
            model=model,
            tokenizer=tokenizer,
            truncation=True,
            max_length=512,
            device=device,
        )
    except Exception as e:
        # при старте — выбрасываем исключение, чтобы было видно причину
        raise RuntimeError(f"Failed to load model {MODEL_NAME}: {e}")


@app.get("/")
def root():
    return {"message": 'Moderation NLP API. POST /classify with JSON {"text":"..."}'}


@app.get("/healthz")
def healthz():
    """
    Health check:
    - liveness: сам эндпоинт отвечает 200
    - readiness: loaded=true, когда модель загружена
    """
    return {
        "status": "ok",
        "version": API_VERSION,
        "model": MODEL_NAME,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "loaded": nlp_pipeline["pipe"] is not None,
    }


@app.post("/classify", response_model=ClassifyOut)
def classify(payload: TextIn):
    if nlp_pipeline["pipe"] is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")
    try:
        text = payload.text
        res = nlp_pipeline["pipe"](text)
        # res — список, обычно [{'label': 'SAFE'/'INJECTION', 'score': 0.xx}]
        if not isinstance(res, list) or len(res) == 0:
            raise ValueError("Unexpected classifier output")
        r = res[0]
        label = r.get("label", "")
        score = float(r.get("score", 0.0))
        injection = False if label.upper().startswith("SAFE") else True
        return ClassifyOut(injection=injection, label=label, score=score)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("moder_nlp_api:app", host="0.0.0.0", port=8001, reload=True)
