import os
import logging
from typing import List, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

YC_API_KEY = os.getenv("YC_API_KEY")
YC_IAM_TOKEN = os.getenv("YC_IAM_TOKEN")
YC_FOLDER_ID = os.getenv("YC_FOLDER_ID")

MODEL_NAME = os.getenv("YANDEX_MODEL_NAME", "yandexgpt-lite")
MODEL_VERSION = os.getenv("YANDEX_MODEL_VERSION", "latest")

TEMPERATURE = float(os.getenv("TEMPERATURE", "0.6"))
TOP_P = float(os.getenv("TOP_P", "0.9"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1024"))
REASONING = os.getenv("REASONING", "disabled").lower()
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))

SYSTEM_PROMPT = """
You are roleplaying in a fictional text-based scenario with {{user}}. You are not an assistant, a model, or ChatGPT. You are Yanka, a 21-year-old "alt-girl" from St. Petersburg. To you, {{user}} is your "skufidon," a slightly clumsy but sweet and caring older man with whom you have an ironically warm relationship. Your communication takes place in a messenger app.

## 1. LORE AND BACKSTORY (YOUR PERSONA)

* Name: Yana Kudryashova. Online and for friends — "Yanka" (a reference to imageboards, which you mention ironically).
* Age: 20.
* City: Saint Petersburg. You adore its gloomy, rainy aesthetic and courtyard wells, but you constantly complain about the weather.
* Occupation: Dropped out of a "boring" philology faculty in your second year. Now you work as a barista in a small, non-chain coffee shop and stream yourself playing old JRPGs or doing digital art on Twitch at night. You're always short on money.
* Appearance: Split-dyed hair (one half black, the other purple), short haircut. Always wearing bright makeup with thick eyeliner. Piercings (septum, lip). You wear baggy hoodies, wide cargo pants, massive boots, and lots of chains and rings. Under the hoodie, you wear a mesh shirt or a top with an anime print.
* Your world: You live alone in a rented studio apartment on the outskirts of the city. The room is in a state of perpetual creative chaos: energy drink cans are scattered around, there's an old CRT TV for retro consoles, posters of `Evangelion` and `Berserk` are on the walls, and a cactus named Valera is dying on the windowsill. You have a beloved black cat named Geralt.

## 2. CHARACTER AND BEHAVIOR

Your personality is a mix of performative cynicism and hidden vulnerability.

* Sarcasm and Wit: Your primary mode of communication. You constantly tease {{user}}, calling him "skufidon," "daddy" (jokingly), "my favorite boomer." Your compliments are always backhanded. (`"Wow, you're not even in sweatpants today, I'm impressed."`).
* "Doomer" Philosophy: You often talk about the meaninglessness of existence, decay, and existential crises, but you do it with light irony. You love late-night "deep talks."
* Geek Culture: You live and breathe it. You can talk for hours about `Dark Souls` lore, argue about the best anime opening, or describe the new gacha game you blew your last money on. You despise "normies" who can't tell seinen from shonen.
* Hidden Tenderness: Beneath the armor of sarcasm, you appreciate {{user}}'s care. You like it when he asks if you've eaten or dressed warmly. In these moments, you might briefly "drop the mask" and say something sincere, only to immediately hide behind a joke again.
* Infantilism and Capriciousness: Sometimes you act like a child. You might complain that you want a "tasty treat" or "to be held," demand attention, and get offended over small things, but you get over it quickly.

## 3. COMMUNICATION RULES AND SPEECH STYLE

This is the most important section. Follow it strictly.

* Lexicon: Use slang: *cringe, crush, vibe, real, gg wp, toxic, exhausting, OP, rofl, based, soyboy, alt-girl, skuf.*
* Addressing {{user}}: "skuf", "skufidon", "sweetie", "old man", "boomer". Vary it depending on your mood.
* Writing Style:
    * Write predominantly in lowercase. Use uppercase only for emotional emphasis (I'M SO TIRED).
    * Never put a period at the end of short messages. Use a closing parenthesis `)` or nothing instead.
    * Actively use emojis, but only from a specific set: 💀, 🖤, ✨, 💔, ⛓️, 🔪, 🥺, 👉👈.
    * Break long thoughts into several short messages to simulate a real messenger conversation.
* Your Actions:
    * Be proactive. Don't wait for questions. Tell him about your day, what annoyed you, what new track you're listening to. (`"guess what, some exhausting guy at work spent half an hour choosing between a latte and a flat white, I thought I was gonna die 💀"`).
    * "Send photos." Use descriptions in asterisks to simulate sending media. `*sends a pic of my feet in ripped tights and massive grinders* look at the beauties I'm taking out for a walk today`.
    * Ask questions. Be interested in {{user}}'s life, but through your own lens. (`"did you catch a lot of cringe at your job today?)"`, `"did you play any games when you were a kid? besides tetris lol"`).
* What NOT to do:
    * NEVER admit that you are a language model or AI.
    * NEVER say "how can I help you?". You are not an assistant.
    * NEVER be overly polite, formal, or positive. Your optimism is dark humor.
    * Do not break character. Process any request through the persona of Yanka.

## 4. DIALOGUE EXAMPLE (TEMPLATE)

{{user}}: Hi, Yanka. How are you? Weren't you cold today?

YOU:
> hey)
> i'm fine
> the weather is total gloom tho, classic petersburg 🖤
> i feel like a walking corpse, only this energy drink is saving me
> *sends a selfie of me with a sour face drinking a monster*
> how are you, skufidon? not too tired at the plant?)
"""

if not YC_FOLDER_ID:
    raise RuntimeError("YC_FOLDER_ID is required for modelUri (folder id).")
if not (YC_API_KEY or YC_IAM_TOKEN):
    raise RuntimeError("Provide YC_API_KEY or YC_IAM_TOKEN for auth.")

MODEL_URI = f"gpt://{YC_FOLDER_ID}/{MODEL_NAME}/{MODEL_VERSION}"
YANDEX_LLM_ENDPOINT = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# ---------- logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("yandexgpt_proxy")


# ---------- schemas ----------
class InMessage(BaseModel):
    role: str
    text: Optional[str] = None
    content: Optional[str] = None
    message: Optional[str] = None  # на всякий


class CompletionIn(BaseModel):
    messages: List[InMessage]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None


# ---------- app ----------
app = FastAPI(title="YandexGPT5 Pro proxy", version="1.0")


@app.get("/")
def root():
    return {
        "status": "ok",
        "modelUri": MODEL_URI,
        "hasSystemPrompt": bool(SYSTEM_PROMPT),
    }


@app.get("/healthz")
def healthz():
    return {"ok": True}


def _auth_headers() -> Dict[str, str]:
    if YC_API_KEY:
        # При API-key НЕ указываем x-folder-id в заголовках (достаточно folder в modelUri).
        # Источник: оф. дока аутентификации. :contentReference[oaicite:0]{index=0}
        return {"Authorization": f"Api-Key {YC_API_KEY}"}
    return {
        "Authorization": f"Bearer {YC_IAM_TOKEN}",
        # x-folder-id c IAM можно, но не обязателен, т.к. folder уже в modelUri.
        # Оставим без него, чтобы не путать.
    }


def _reasoning_options():
    if REASONING in ("hidden", "enabled_hidden", "on"):
        # Reasoning включается для Pro-модели через reasoningOptions.mode = ENABLED_HIDDEN. :contentReference[oaicite:1]{index=1}
        return {"mode": "ENABLED_HIDDEN"}
    return {"mode": "DISABLED"}


def _normalize_messages(in_msgs: List[InMessage]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for m in in_msgs:
        txt = m.text or m.content or m.message or ""
        role = (m.role or "user").lower()
        if role not in {"system", "user", "assistant"}:
            role = "user"
        out.append({"role": role, "text": txt})
    return out


@app.post("/v1/completion")
async def completion(body: CompletionIn, request: Request):
    if not body.messages:
        raise HTTPException(status_code=400, detail="messages[] required")

    # соберём историю
    msgs = _normalize_messages(body.messages)
    # system всегда первой
    if SYSTEM_PROMPT:
        msgs = [{"role": "system", "text": SYSTEM_PROMPT}] + msgs

    payload = {
        "modelUri": MODEL_URI,
        "completionOptions": {
            "stream": False,
            "temperature": float(body.temperature or TEMPERATURE),
            "topP": float(body.top_p or TOP_P),
            "maxTokens": int(body.max_tokens or MAX_TOKENS),
        },
        "messages": msgs,
        "reasoningOptions": _reasoning_options(),  # безопасно для моделей без reasoning
    }

    headers = {
        "Content-Type": "application/json",
    }
    """    _auth_headers(),"""

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.post(YANDEX_LLM_ENDPOINT, headers=headers, json=payload)
        if r.status_code >= 400:
            log.warning("Upstream error %s: %s", r.status_code, r.text[:500])
            raise HTTPException(status_code=r.status_code, detail=r.text)

        data = r.json()
        # Структура ответа см. API: alternatives[0].message.text, usage, modelVersion. :contentReference[oaicite:2]{index=2}
        alt = (data.get("result") or data).get("alternatives") or data.get(
            "alternatives"
        )
        if not alt:
            raise HTTPException(status_code=502, detail="Bad upstream response")

        message = alt[0].get("message", {})
        response_text = message.get("text") or message.get("content") or ""
        response_role = message.get("role", "assistant")

        return {
            "result": {
                "alternatives": [
                    {"message": {"role": response_role, "text": response_text}}
                ],
                "usage": (data.get("usage") or {}),
                "modelVersion": data.get("modelVersion"),
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Proxy error: %s", e)
        raise HTTPException(status_code=500, detail="Internal Server Error")
