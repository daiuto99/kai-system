from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from routes import vault, focus, parking_lot, inbox, checkin, settings, projects, tasks, habits, calendar, knowledge, t2, telegram, contacts, slack, advisors, wiki, workflows, oura, system, location, git_activity, admin, plane, session, intake, wordpress
from harmony import router as harmony_router

app = FastAPI(title="kai-worker-api", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kai.sonicink.space"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for router_module in [vault, focus, parking_lot, inbox, checkin, settings, projects, tasks, habits, calendar, knowledge, t2, telegram, contacts, slack, advisors, wiki, workflows, oura, system, location, git_activity, admin, plane, session, intake, wordpress]:
    app.include_router(router_module.router)

app.include_router(harmony_router)


@app.get("/health")
def health():
    vault_ok = Path("/vault").exists()
    return {
        "status": "ok",
        "service": "kai-worker-api",
        "vault_mounted": vault_ok,
        "vault_path": "/vault",
    }
