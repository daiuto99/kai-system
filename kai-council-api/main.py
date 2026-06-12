from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import router as council_router
import history
import insights
from models_api import models_router
import routes_orchestrate
import routes_bug_workflow
import routes_council_gate
import routes_function_map
from routes_bug_workflow import start_bug_poller


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_bug_poller()
    yield


app = FastAPI(title="kai-council-api", version="0.5.0", lifespan=lifespan)

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
