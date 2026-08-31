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
from fastapi.responses import JSONResponse, Response

sys.path.insert(0, "/shared")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("kai-voice-stt")

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "base")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE", "int8")
COUNCIL_URL = os.environ.get("COUNCIL_API_URL", "http://kai-council-api:8002")
TTS_URL = os.environ.get("TTS_API_URL", "http://kai-voice-tts:8006")

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


async def _save_upload(audio: UploadFile) -> str:
    suffix = os.path.splitext(audio.filename or "")[1] or ".wav"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(await audio.read())
    return tmp_path


def _transcribe_file(path: str) -> dict:
    """Core STT: file path -> {text, language, duration_s, transcribe_s}."""
    t0 = time.time()
    segments, info = _get_model().transcribe(path, beam_size=1)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return {
        "text": text,
        "language": info.language,
        "duration_s": round(info.duration, 2),
        "transcribe_s": round(time.time() - t0, 2),
    }


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
        tmp_path = await _save_upload(audio)
        result = {"ok": True, **_transcribe_file(tmp_path)}
        if forward_to_council and result["text"]:
            result["council"] = _forward_to_council(result["text"], channel)
        return result
    except Exception as e:  # noqa: BLE001
        log.exception("transcribe error: %s", e)
        _set_presence("error", str(e)[:80])
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        _set_presence("idle")


@app.post("/converse")
async def converse(
    audio: UploadFile = File(...),
    channel: str = Form("kai"),
    voice: str = Form(""),
):
    """The full two-way loop: spoken audio -> transcript -> council -> spoken reply.

    Returns the reply as a WAV stream (audio/wav); the transcript and reply text ride
    along in X-Transcript / X-Reply-Text headers. Drives presence end to end:
    thinking (STT + council) -> speaking (TTS) -> idle. This service owns the presence
    lifecycle, so it calls TTS with manage_presence=false to avoid a double-writer."""
    _set_presence("thinking", "transcribing")
    tmp_path = None
    try:
        tmp_path = await _save_upload(audio)
        stt = _transcribe_file(tmp_path)
        transcript = stt["text"]
        if not transcript:
            return JSONResponse(status_code=422, content={"ok": False, "error": "no speech detected"})

        council = _forward_to_council(transcript, channel)
        if not council.get("ok"):
            return JSONResponse(status_code=502,
                                content={"ok": False, "error": "council forward failed",
                                         "transcript": transcript, "council": council})
        reply = _reply_text(council)
        if not reply:
            return JSONResponse(status_code=502,
                                content={"ok": False, "error": "empty council reply",
                                         "transcript": transcript})

        _set_presence("speaking", reply[:60])
        audio_bytes = _synthesize(reply, voice)
        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={
                "X-Transcript": _hdr(transcript),
                "X-Reply-Text": _hdr(reply),
            },
        )
    except Exception as e:  # noqa: BLE001
        log.exception("converse error: %s", e)
        _set_presence("error", str(e)[:80])
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        _set_presence("idle")


def _reply_text(council: dict) -> str:
    resp = council.get("response") or {}
    return str(resp.get("reply") or resp.get("message") or "").strip()


def _hdr(s: str) -> str:
    """HTTP header values must be latin-1 and single-line."""
    return s.replace("\n", " ").encode("latin-1", "replace").decode("latin-1")[:800]


def _synthesize(text: str, voice: str) -> bytes:
    """Call the TTS service; presence stays owned by /converse (manage_presence=false)."""
    body = {"text": text, "manage_presence": False}
    if voice:
        body["voice"] = voice
    r = httpx.post(f"{TTS_URL}/speak", json=body, timeout=180)
    r.raise_for_status()
    return r.content


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
