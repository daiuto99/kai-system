"""kai-voice-tts — CPU-only local text-to-speech (KAI-1283 P-3 voice layer).

Kokoro (kokoro-onnx, ONNX Runtime) on CPU: local, $0 per minute, no cloud TTS. Gives
KAI a voice so a turn *ends* in speech, not text — the second half of the two-way loop
whose first half (STT + council) already ships. Writes 'speaking' to the voice signal
bus while synthesizing so the dashboard/kiosk dot lights up.

Model files (onnx + voice pack) download once into the mounted /models cache on first
use — same pattern as the STT model. Fail-soft: a bus write never breaks synthesis.
"""
from __future__ import annotations

import io
import logging
import os
import sys
import time
import urllib.request

import soundfile as sf
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, "/shared")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("kai-voice-tts")

MODELS_DIR = os.environ.get("KOKORO_MODELS_DIR", "/models")
MODEL_PATH = os.path.join(MODELS_DIR, "kokoro-v1.0.onnx")
VOICES_PATH = os.path.join(MODELS_DIR, "voices-v1.0.bin")
DEFAULT_VOICE = os.environ.get("KOKORO_VOICE", "af_sarah")

_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

app = FastAPI(title="kai-voice-tts")

_kokoro = None  # lazy — first /speak pays the model load


def _signal_bus():
    try:
        import signal_bus
        return signal_bus
    except Exception as e:  # noqa: BLE001
        log.warning("signal_bus unavailable: %s", e)
        return None


def _set_presence(state: str, detail: str | None = None):
    sb = _signal_bus()
    if sb is not None:
        try:
            sb.set_state(state, detail, "tts")
        except Exception as e:  # noqa: BLE001
            log.warning("presence write failed (non-fatal): %s", e)


def _ensure_models():
    os.makedirs(MODELS_DIR, exist_ok=True)
    for path, url in ((MODEL_PATH, _MODEL_URL), (VOICES_PATH, _VOICES_URL)):
        if not os.path.exists(path):
            log.info("downloading %s -> %s", url, path)
            t0 = time.time()
            urllib.request.urlretrieve(url, path)
            log.info("downloaded %s (%.1fMB) in %.1fs", os.path.basename(path),
                     os.path.getsize(path) / 1e6, time.time() - t0)


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        _ensure_models()
        from kokoro_onnx import Kokoro  # heavy import deferred to first use
        log.info("loading Kokoro (CPU)")
        t0 = time.time()
        _kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
        log.info("Kokoro loaded in %.1fs", time.time() - t0)
    return _kokoro


class SpeakRequest(BaseModel):
    text: str
    voice: str | None = None
    speed: float = 1.0
    manage_presence: bool = True  # /converse sets False and owns presence itself


@app.get("/health")
def health():
    return {"ok": True, "voice": DEFAULT_VOICE, "loaded": _kokoro is not None,
            "models_present": os.path.exists(MODEL_PATH) and os.path.exists(VOICES_PATH)}


@app.post("/speak")
def speak(req: SpeakRequest):
    """Synthesize speech from text. Returns a WAV stream (audio/wav)."""
    text = (req.text or "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"ok": False, "error": "text is required"})
    if req.manage_presence:
        _set_presence("speaking", text[:60])
    try:
        t0 = time.time()
        samples, sr = _get_kokoro().create(
            text, voice=req.voice or DEFAULT_VOICE, speed=req.speed, lang="en-us")
        buf = io.BytesIO()
        sf.write(buf, samples, sr, format="WAV")
        buf.seek(0)
        log.info("synthesized %d chars in %.2fs (sr=%d)", len(text), time.time() - t0, sr)
        return StreamingResponse(buf, media_type="audio/wav",
                                 headers={"X-Synthesis-Seconds": f"{time.time() - t0:.2f}"})
    except Exception as e:  # noqa: BLE001
        log.exception("speak error: %s", e)
        _set_presence("error", str(e)[:80])
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    finally:
        if req.manage_presence:
            _set_presence("idle")
