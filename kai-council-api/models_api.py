import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
import httpx
from fastapi import APIRouter, HTTPException
from council_config import VAULT_PATH
from complexity import _load_model_config
from providers import get_anthropic_client

logger = logging.getLogger(__name__)
models_router = APIRouter()

MODEL_CONFIG_FILE = VAULT_PATH / "00_System" / "model_config.json"


@models_router.get("/models/config")
def get_model_config():
    return _load_model_config()


@models_router.patch("/models/config/advisor/{advisor_id}")
def update_advisor_model(advisor_id: str, body: dict):
    config = _load_model_config()
    if "advisors" not in config:
        config["advisors"] = {}
    if advisor_id not in config["advisors"]:
        config["advisors"][advisor_id] = {}
    config["advisors"][advisor_id].update(body)
    MODEL_CONFIG_FILE.write_text(json.dumps(config, indent=2))
    return {"ok": True, "advisor": advisor_id, "config": config["advisors"][advisor_id]}


@models_router.get("/models/status")
def get_model_status():
    status = {}
    secret_path = Path("/run/secrets/anthropic_api_key")
    has_anthropic = secret_path.exists() or bool(os.environ.get("ANTHROPIC_API_KEY"))
    status["anthropic"] = {
        "available": has_anthropic, "label": "Anthropic Claude",
        "tier": "cloud", "privacy": "cloud",
        "models_available": ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"],
    }
    oai_path = Path("/run/secrets/openai_api_key")
    has_openai = oai_path.exists() or bool(os.environ.get("OPENAI_API_KEY"))
    status["openai"] = {
        "available": has_openai, "label": "OpenAI GPT",
        "tier": "cloud", "privacy": "cloud",
        "models_available": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-mini"],
    }
    try:
        with httpx.Client(timeout=3) as hc:
            r = hc.get("http://kai-ollama:11434/api/tags")
            if r.status_code == 200:
                raw_models = r.json().get("models", [])
                model_details = []
                for m in raw_models:
                    size_gb = round(m.get("size", 0) / 1e9, 1)
                    modified = m.get("modified_at", "")[:10]
                    model_details.append({
                        "name": m["name"], "size_gb": size_gb, "modified": modified,
                        "digest": m.get("digest", "")[:12],
                        "family": m.get("details", {}).get("family", ""),
                        "params": m.get("details", {}).get("parameter_size", ""),
                    })
                status["ollama"] = {
                    "available": True, "label": "Ollama (Local)",
                    "tier": "local", "privacy": "local",
                    "models": [m["name"] for m in raw_models],
                    "model_details": model_details,
                }
            else:
                status["ollama"] = {"available": False, "label": "Ollama (Local)", "tier": "local", "error": f"HTTP {r.status_code}"}
    except Exception as e:
        logger.exception("get_model_status ollama: %s", e)
        status["ollama"] = {"available": False, "label": "Ollama (Local)", "tier": "local", "error": str(e)}

    bench_path = Path("/vault/00_System/model_benchmarks.json")
    benchmarks = {}
    if bench_path.exists():
        try:
            benchmarks = json.loads(bench_path.read_text()).get("benchmarks", {})
        except Exception as e:
            logger.exception("get_model_status benchmarks: %s", e)

    return {"providers": status, "benchmarks": benchmarks}


@models_router.get("/models/benchmarks")
def get_benchmarks():
    bench_path = Path("/vault/00_System/model_benchmarks.json")
    if not bench_path.exists():
        return {"benchmarks": {}}
    try:
        return json.loads(bench_path.read_text())
    except Exception as e:
        logger.exception("get_benchmarks: %s", e)
        return {"benchmarks": {}}


@models_router.post("/models/benchmarks/run")
def run_benchmark(body: dict):
    import time
    model = body.get("model")
    if not model:
        raise HTTPException(status_code=400, detail="model required")
    prompt = "In exactly one sentence, describe your purpose."
    start = time.time()
    try:
        with httpx.Client(timeout=120) as hc:
            r = hc.post("http://kai-ollama:11434/api/chat", json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": 60, "temperature": 0.1},
            })
        elapsed_ms = int((time.time() - start) * 1000)
        data = r.json()
        reply = data.get("message", {}).get("content", "")
        eval_count = data.get("eval_count", 0)
        prompt_count = data.get("prompt_eval_count", 0)
        tps = round(eval_count / (elapsed_ms / 1000), 1) if elapsed_ms > 0 else 0
        result = {
            "avg_ms": elapsed_ms, "tokens_per_sec": tps,
            "eval_tokens": eval_count, "prompt_tokens": prompt_count,
            "last_run": datetime.now().isoformat()[:19],
            "status": "ok", "sample": reply[:80],
        }
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        result = {
            "avg_ms": elapsed_ms, "tokens_per_sec": 0,
            "last_run": datetime.now().isoformat()[:19],
            "status": "error", "error": str(e),
        }

    bench_path = Path("/vault/00_System/model_benchmarks.json")
    try:
        existing = json.loads(bench_path.read_text()) if bench_path.exists() else {"benchmarks": {}}
    except Exception as e:
        logger.exception("run_benchmark read: %s", e)
        existing = {"benchmarks": {}}
    existing["benchmarks"][model] = result
    bench_path.write_text(json.dumps(existing, indent=2))
    return {"model": model, "result": result}


@models_router.get("/models/catalog")
def get_model_catalog():
    ANTHROPIC_MODELS = [
        {"name": "claude-sonnet-4-6", "label": "Sonnet 4.6", "tier": "cloud", "speed_label": "~2s", "speed_ms": 2000},
        {"name": "claude-opus-4-6",   "label": "Opus 4.6",   "tier": "premium", "speed_label": "~5s", "speed_ms": 5000},
        {"name": "claude-haiku-4-5-20251001", "label": "Haiku 4.5", "tier": "cloud", "speed_label": "~0.5s", "speed_ms": 500},
    ]
    OPENAI_MODELS = [
        {"name": "gpt-4o",      "label": "GPT-4o",      "tier": "cloud",   "speed_label": "~3s", "speed_ms": 3000},
        {"name": "gpt-4o-mini", "label": "GPT-4o mini", "tier": "cloud",   "speed_label": "~1s", "speed_ms": 1000},
        {"name": "o1-mini",     "label": "o1-mini",     "tier": "premium", "speed_label": "~10s", "speed_ms": 10000},
    ]
    ant_ok = Path("/run/secrets/anthropic_api_key").exists() or bool(os.environ.get("ANTHROPIC_API_KEY"))
    oai_ok = Path("/run/secrets/openai_api_key").exists() or bool(os.environ.get("OPENAI_API_KEY"))
    ollama_ok = False
    ollama_installed = []
    try:
        with httpx.Client(timeout=3) as hc:
            r = hc.get("http://kai-ollama:11434/api/tags")
            if r.status_code == 200:
                ollama_ok = True
                for m in r.json().get("models", []):
                    ollama_installed.append({
                        "name": m["name"], "label": m["name"], "tier": "local",
                        "size_gb": round(m.get("size", 0) / 1e9, 1),
                        "params": m.get("details", {}).get("parameter_size", ""),
                        "family": m.get("details", {}).get("family", ""),
                        "modified": m.get("modified_at", "")[:10],
                    })
    except Exception as e:
        logger.exception("get_model_catalog ollama: %s", e)
    bench_path = Path("/vault/00_System/model_benchmarks.json")
    benchmarks = {}
    if bench_path.exists():
        try:
            benchmarks = json.loads(bench_path.read_text()).get("benchmarks", {})
        except Exception as e:
            logger.exception("get_model_catalog benchmarks: %s", e)
    for m in ollama_installed:
        b = benchmarks.get(m["name"]) or benchmarks.get(m["name"].split(":")[0])
        if b and b.get("avg_ms"):
            m["speed_ms"] = b["avg_ms"]; m["tokens_per_sec"] = b.get("tokens_per_sec")
            m["speed_label"] = f"{b['avg_ms']//1000}s"; m["last_benchmarked"] = b.get("last_run", "")[:16]
        else:
            m["speed_ms"] = None; m["speed_label"] = "Not tested"
    cfg = _load_model_config()
    advisor_configs = cfg.get("advisors", {})
    FUNCTION_MAP = [
        {"function": "KAI Tools",            "description": "create_task, send_slack, create_event, write_vault", "provider": "anthropic", "model": advisor_configs.get("chief", {}).get("model", "claude-sonnet-4-6")},
        {"function": "Specialist Consult",   "description": "consult_specialist — 10 domain experts",             "provider": "anthropic", "model": advisor_configs.get("chief", {}).get("model", "claude-sonnet-4-6")},
        {"function": "Session Summaries",    "description": "Auto-summarize after 20+ exchanges",                  "provider": "anthropic", "model": advisor_configs.get("chief", {}).get("model", "claude-sonnet-4-6")},
        {"function": "Decision Logging",     "description": "log_decision — vault/60_Council/decisions/",          "provider": "anthropic", "model": advisor_configs.get("chief", {}).get("model", "claude-sonnet-4-6")},
        {"function": "Gmail Read / Draft",   "description": "read_email, draft_email via n8n OAuth",               "provider": "anthropic", "model": advisor_configs.get("chief", {}).get("model", "claude-sonnet-4-6")},
        {"function": "n8n Workflow Trigger", "description": "trigger_n8n_workflow — calendar, automations",        "provider": "anthropic", "model": advisor_configs.get("chief", {}).get("model", "claude-sonnet-4-6")},
    ]
    for adv_id, adv_cfg in advisor_configs.items():
        FUNCTION_MAP.append({
            "function": f"{adv_id.title()} Chat",
            "description": (adv_cfg.get("notes") or "")[:70],
            "provider": adv_cfg.get("provider", "anthropic"),
            "model": adv_cfg.get("model", "claude-sonnet-4-6"),
            "is_advisor": True,
        })
    return {
        "providers": {
            "anthropic": {"available": ant_ok, "label": "Anthropic Claude", "tier": "cloud", "color": "#6366f1", "models": ANTHROPIC_MODELS},
            "openai":    {"available": oai_ok, "label": "OpenAI GPT",        "tier": "cloud", "color": "#10a37f", "models": OPENAI_MODELS},
            "ollama":    {"available": ollama_ok, "label": "Ollama — Local",  "tier": "local", "color": "#f59e0b", "models": ollama_installed},
        },
        "function_map": FUNCTION_MAP,
        "benchmarks": benchmarks,
    }
