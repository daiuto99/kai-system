import logging
import os
from pathlib import Path
import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def get_anthropic_client():
    import anthropic
    secret_path = Path("/run/secrets/anthropic_api_key")
    api_key = secret_path.read_text().strip() if secret_path.exists() else os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")
    return anthropic.Anthropic(api_key=api_key)


def _call_ollama(model: str, system: str, messages: list, max_tokens: int = 1024) -> tuple:
    """Call local Ollama. Returns (reply, input_tokens, output_tokens)."""
    if "</background_context>" in system:
        system = system.split("</background_context>", 1)[1].strip()
    if len(system) > 1200:
        system = system[:1200] + "\n[Respond in character per the above.]"

    ollama_msgs = [{"role": "system", "content": system}]
    for m in messages[-4:]:
        c = m["content"] if isinstance(m["content"], str) else str(m["content"])
        ollama_msgs.append({"role": m["role"], "content": c})

    with httpx.Client(timeout=30) as hc:
        r = hc.post("http://kai-ollama:11434/api/chat", json={
            "model": model,
            "messages": ollama_msgs,
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_predict": max_tokens, "temperature": 0.7},
        })
    if r.status_code != 200:
        raise RuntimeError(f"Ollama {r.status_code}: {r.text[:300]}")
    data = r.json()
    reply = data["message"]["content"]
    return reply, data.get("prompt_eval_count", 0), data.get("eval_count", 0)


def _call_openai(model: str, system: str, messages: list, max_tokens: int = 2048) -> tuple:
    """Call OpenAI API. Returns (reply, input_tokens, output_tokens)."""
    secret_path = Path("/run/secrets/openai_api_key")
    api_key = secret_path.read_text().strip() if secret_path.exists() else os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OpenAI API key not configured — add OPENAI_API_KEY secret")

    oai_msgs = []
    if system:
        oai_msgs.append({"role": "system", "content": system})
    for m in messages:
        if isinstance(m["content"], str):
            oai_msgs.append({"role": m["role"], "content": m["content"]})

    with httpx.Client(timeout=60) as hc:
        r = hc.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": oai_msgs, "max_tokens": max_tokens},
        )
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:300]}")
    data = r.json()
    reply = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return reply, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def _warmup_ollama(model: str = "llama3.2"):
    try:
        with httpx.Client(timeout=5) as hc:
            hc.post("http://kai-ollama:11434/api/generate", json={
                "model": model, "prompt": "", "keep_alive": "30m"
            })
    except Exception as e:
        logger.exception("warmup_ollama: %s", e)
