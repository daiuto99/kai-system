from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pathlib import Path
from pydantic import BaseModel
import anthropic
import os

app = FastAPI(title="kai-council-api", version="0.1.0")

VAULT_PATH = Path("/vault")
COUNCIL_PATH = VAULT_PATH / "60_Council"

ADVISOR_CHANNELS = {
    "chief": "chief",
    "beats": "beats",
    "beats-personal": "beats",
    "ember": "ember",
    "doc": "doc",
    "coach": "coach",
    "biz": "biz",
}


def get_anthropic_client():
    secret_path = Path("/run/secrets/anthropic_api_key")
    if secret_path.exists():
        api_key = secret_path.read_text().strip()
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")
    return anthropic.Anthropic(api_key=api_key)


def load_persona(advisor: str, channel: str = None) -> str:
    advisor_dir = COUNCIL_PATH / advisor
    persona_file = advisor_dir / f"{advisor.upper()}.md"
    if not persona_file.exists():
        raise HTTPException(status_code=404, detail=f"Persona not found: {advisor}")
    persona = persona_file.read_text(encoding="utf-8")

    context_file = advisor_dir / "context.md"
    if context_file.exists():
        persona += "\n\n---\n\n" + context_file.read_text(encoding="utf-8")

    # Beats personal channel also loads deep.md
    if channel == "beats-personal" and (advisor_dir / "deep.md").exists():
        persona += "\n\n---\n\n" + (advisor_dir / "deep.md").read_text(encoding="utf-8")

    # Ember also loads insights.md
    if advisor == "ember" and (advisor_dir / "insights.md").exists():
        insights = (advisor_dir / "insights.md").read_text(encoding="utf-8")
        if insights.strip():
            persona += "\n\n---\n\n" + insights

    return persona


class MessageRequest(BaseModel):
    channel: str
    message: str
    user_id: str
    history: list[dict] = []
    thread_ts: str = ""


class ContextUpdateRequest(BaseModel):
    advisor: str
    content: str


@app.get("/health")
def health():
    council_ok = COUNCIL_PATH.exists()
    advisors_present = []
    if council_ok:
        for advisor in ADVISOR_CHANNELS.values():
            if (COUNCIL_PATH / advisor).exists():
                advisors_present.append(advisor)
    return {
        "status": "ok",
        "service": "kai-council-api",
        "council_path_mounted": council_ok,
        "advisors_ready": sorted(set(advisors_present)),
    }


@app.post("/council/message")
def council_message(req: MessageRequest):
    channel = req.channel.lstrip("#")
    advisor = ADVISOR_CHANNELS.get(channel)
    if not advisor:
        raise HTTPException(status_code=400, detail=f"Unknown channel: {channel}")

    system_prompt = load_persona(advisor, channel)
    client = get_anthropic_client()

    messages = req.history[-10:]  # last 10 exchanges max
    messages.append({"role": "user", "content": req.message})

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        system=system_prompt,
        messages=messages,
    )

    reply = response.content[0].text
    return {
        "advisor": advisor,
        "channel": channel,
        "reply": reply,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }


@app.post("/council/context/update")
def update_context(req: ContextUpdateRequest):
    advisor_dir = COUNCIL_PATH / req.advisor
    if not advisor_dir.exists():
        raise HTTPException(status_code=404, detail=f"Advisor not found: {req.advisor}")
    context_file = advisor_dir / "context.md"
    context_file.write_text(req.content, encoding="utf-8")
    return {"status": "updated", "advisor": req.advisor}


@app.get("/council/context/{advisor}")
def get_context(advisor: str):
    advisor_dir = COUNCIL_PATH / advisor
    context_file = advisor_dir / "context.md"
    if not context_file.exists():
        raise HTTPException(status_code=404, detail=f"Context not found for: {advisor}")
    return {"advisor": advisor, "content": context_file.read_text(encoding="utf-8")}
