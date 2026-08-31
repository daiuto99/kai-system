"""kai-voice-stt — CPU-only local speech-to-text (KAI-1283 P-3 voice layer).

faster-whisper (CTranslate2, int8) on CPU: no GPU, no cloud, $0 per minute. Wired to
the voice signal bus so the dashboard/kiosk *feel* KAI listening/thinking, and to the
council path so a spoken turn becomes a real council message.

Turn lifecycle written to the bus:
  receive audio → 'thinking' (transcribing) → [forward to council] → 'idle'
(the 'speaking' state is owned by the TTS service, landing in the next P-3 session).

Fail-soft: bus writes and the council forward never break transcription — the caller
always gets its text back even if presence or council is down.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

sys.path.insert(0, "/shared")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("kai-voice-stt")

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "base")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE", "int8")
COUNCIL_URL = os.environ.get("COUNCIL_API_URL", "http://kai-council-api:8002")

# The council API fails closed behind HTTP Basic (kai_worker_auth). Same secret,
# same standard paths every internal caller reads.
_WORKER_AUTH_FILES = (
    "/run/secrets/kai_worker_auth",
    "/home/leo/kai-system/secrets/kai_worker_auth.txt",
)


def _worker_auth():
    """Return (user, pw) for the council, or None if the secret isn't mounted."""
    for path in _WORKER_AUTH_FILES:
        try:
            user, pw = open(path).read().strip().split(":", 1)
            if user and pw:
                return (user, pw)
        except Exception:
            continue
    log.warning("kai_worker_auth not found — council forward will 401")
    return None


app = FastAPI(title="kai-voice-stt")

_model = None  # lazy — first /transcribe pays the load, /health can report readiness


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
            sb.set_state(state, detail, "stt")
        except Exception as e:  # noqa: BLE001
            log.warning("presence write failed (non-fatal): %s", e)


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel  # heavy import, deferred to first use
        log.info("loading faster-whisper model=%s compute=%s (CPU)", MODEL_SIZE, COMPUTE_TYPE)
        t0 = time.time()
        _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type=COMPUTE_TYPE)
        log.info("model loaded in %.1fs", time.time() - t0)
    return _model


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_SIZE, "compute": COMPUTE_TYPE, "loaded": _model is not None}


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    forward_to_council: bool = Form(False),
    channel: str = Form("kai"),
):
    """Transcribe an uploaded audio file (wav/mp3/m4a/ogg/flac — anything ffmpeg reads).

    Returns {text, language, duration_s, transcribe_s}. If forward_to_council is set,
    also posts the transcript to the council and returns its reply under `council`."""
    _set_presence("thinking", "transcribing")
    tmp_path = None
    try:
        suffix = os.path.splitext(audio.filename or "")[1] or ".wav"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(await audio.read())

        t0 = time.time()
        segments, info = _get_model().transcribe(tmp_path, beam_size=1)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        transcribe_s = round(time.time() - t0, 2)

        result = {
            "ok": True,
            "text": text,
            "language": info.language,
            "duration_s": round(info.duration, 2),
            "transcribe_s": transcribe_s,
        }

        if forward_to_council and text:
            result["council"] = _forward_to_council(text, channel)

        return result
    except Exception as e:  # noqa: BLE001
        log.exception("transcribe error: %s", e)
        _set_presence("error", str(e)[:80])
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        _set_presence("idle")


def _forward_to_council(text: str, channel: str) -> dict:
    """POST the transcript to the council path. Non-fatal: a council failure is
    reported in the response, not raised — the transcript already succeeded."""
    try:
        r = httpx.post(
            f"{COUNCIL_URL}/message",
            json={"channel": channel, "message": text, "trigger_source": "voice:stt"},
            auth=_worker_auth(),
            timeout=120,
        )
        r.raise_for_status()
        return {"ok": True, "response": r.json()}
    except Exception as e:  # noqa: BLE001
        log.warning("council forward failed (non-fatal): %s", e)
        return {"ok": False, "error": str(e)}
