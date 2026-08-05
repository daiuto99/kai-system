import base64
import hmac
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware

from routes import vault, focus, parking_lot, inbox, checkin, settings, projects, tasks, habits, calendar, knowledge, t2, telegram, contacts, slack, advisors, wiki, workflows, oura, system, location, git_activity, admin, plane, session, intake, wordpress, sprint_a, assets, orchestrator, mode_lock, anthropic_billing, system_activity
from harmony import router as harmony_router

_NO_AUTH = frozenset({
    "/health", "/github/webhook", "/slack/events", "/telegram/webhook", "/mode_lock/slack_callback",
})

_AUTH_FILES = (Path("/run/secrets/kai_worker_auth"), Path("/home/leo/kai-system/secrets/kai_worker_auth.txt"))


def _load_credential() -> tuple[str, str] | None:
    for auth_file in _AUTH_FILES:
        try:
            raw_credential = auth_file.read_text().strip()
        except OSError:
            continue
        try:
            user, pw = raw_credential.split(":", 1)
        except ValueError:
            # A readable credential source is authoritative. Falling through to a
            # later fallback after a malformed Docker secret would silently turn a
            # configuration error into authentication with a different credential.
            return None
        if not user or not pw:
            return None
        return user, pw
    return None


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _NO_AUTH:
            return await call_next(request)
        cred = _load_credential()
        if cred is None:
            return Response(status_code=503, content="worker auth boundary misconfigured: no valid credential loaded")
        expected_user, expected_pw = cred
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode()
                user, pw = decoded.split(":", 1)
                if hmac.compare_digest(user, expected_user) and hmac.compare_digest(pw, expected_pw):
                    return await call_next(request)
            except Exception:
                pass
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="KAI Worker API"'})


app = FastAPI(title="kai-worker-api", version="0.3.0")
app.add_middleware(BasicAuthMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["https://kai.sonicink.space"], allow_methods=["*"], allow_headers=["*"])

for router_module in [vault, focus, parking_lot, inbox, checkin, settings, projects, tasks, habits, calendar, knowledge, t2, telegram, contacts, slack, advisors, wiki, workflows, oura, system, location, git_activity, admin, plane, session, intake, wordpress, sprint_a, assets, orchestrator, mode_lock, anthropic_billing, system_activity]:
    app.include_router(router_module.router)

app.include_router(harmony_router)


@app.get("/health")
def health():
    vault_ok = Path("/vault").exists()
    return {"status": "ok", "service": "kai-worker-api", "vault_mounted": vault_ok, "vault_path": "/vault"}
