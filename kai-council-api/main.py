import base64
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import router as council_router
import history
import insights
from models_api import models_router
import routes_orchestrate
import routes_bug_workflow
import routes_council_gate
import routes_function_map
from routes_bug_workflow import start_bug_poller

_NO_AUTH = frozenset({"/health"})
_AUTH_FILES = (
    Path("/run/secrets/kai_worker_auth"),
    Path("/run/wp_secrets/kai_worker_auth.txt"),
    Path("/home/leo/kai-system/secrets/kai_worker_auth.txt"),
)


def _load_credential() -> tuple[str, str] | None:
    for auth_file in _AUTH_FILES:
        try:
            user, pw = auth_file.read_text().strip().split(":", 1)
            if user and pw:
                return user, pw
        except Exception:
            continue
    return None


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Fail closed for every council route except the Docker healthcheck."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _NO_AUTH:
            return await call_next(request)
        credential = _load_credential()
        if credential is None:
            return Response(status_code=503, content="council auth boundary misconfigured")
        expected_user, expected_pw = credential
        header = request.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                user, pw = base64.b64decode(header[6:]).decode().split(":", 1)
                if hmac.compare_digest(user, expected_user) and hmac.compare_digest(pw, expected_pw):
                    return await call_next(request)
            except Exception:
                pass
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="KAI Council API"'})


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_bug_poller()
    yield


app = FastAPI(title="kai-council-api", version="0.5.0", lifespan=lifespan)

app.add_middleware(BasicAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kai.sonicink.space"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(council_router.router)
app.include_router(history.router)
app.include_router(insights.router)
app.include_router(models_router)
app.include_router(routes_orchestrate.router)
app.include_router(routes_bug_workflow.router)
app.include_router(routes_council_gate.router)
app.include_router(routes_function_map.router)


@app.get("/health")
def health():
    from pathlib import Path
    council_ok = Path("/vault/60_Council").exists()
    return {
        "status": "ok",
        "service": "kai-council-api",
        "version": "0.5.0",
        "council_path_mounted": council_ok,
        "bug_workflow": "active",
        "council_gate": "active",
        "function_map": "active",
    }
