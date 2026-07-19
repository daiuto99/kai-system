import logging
import docker as docker_sdk
from fastapi import APIRouter, HTTPException, Header, Body
from routes._destructive_audit import DestructiveRequest, audit_before
from pathlib import Path

logger = logging.getLogger(__name__)
router = APIRouter()

_TOKEN_FILE = Path("/run/secrets/anthropic_api_key").parent.parent / "secrets" / "redeploy_token.txt"

COMPOSE_PROJECT = "kai-system"
ALLOWED_SERVICES = {
    "kai-worker-api", "kai-council-api", "kai-web",
    "kai-slack-bot", "kai-scheduler", "kai-n8n",
}


def _check_auth(token: str | None):
    token_file = next((p for p in (Path("/run/secrets/redeploy_token"),
                                   Path("/home/leo/kai-system/secrets/redeploy_token.txt"))
                       if p.exists()), None)
    if token_file is None:
        raise HTTPException(401, "redeploy_token not configured")
    expected = token_file.read_text().strip()
    if token != f"Bearer {expected}":
        raise HTTPException(401, "Invalid token")


@router.post("/admin/redeploy/{service}")
def redeploy_service(service: str, authorization: str | None = Header(default=None), body: DestructiveRequest = Body(...)):
    """Trigger docker pull + restart for a named compose service."""
    _check_auth(authorization)
    if service not in ALLOWED_SERVICES:
        raise HTTPException(400, f"Unknown service '{service}'. Allowed: {sorted(ALLOWED_SERVICES)}")
    audit_before("/admin/redeploy/{service}", {"service": service}, body.operator, body.reason)
    try:
        client = docker_sdk.DockerClient.from_env()
        container = client.containers.get(service)
        container.restart()
        logger.info("redeploy: restarted %s", service)
        return {"ok": True, "service": service, "action": "restarted"}
    except docker_sdk.errors.NotFound:
        raise HTTPException(404, f"Container {service} not found")
    except Exception as e:
        logger.exception("redeploy %s: %s", service, e)
        raise HTTPException(500, str(e))


@router.get("/admin/services")
def list_services(authorization: str | None = Header(default=None)):
    """List running KAI service containers and their status."""
    _check_auth(authorization)
    try:
        client = docker_sdk.DockerClient.from_env()
        result = {}
        for name in ALLOWED_SERVICES:
            try:
                c = client.containers.get(name)
                result[name] = {"status": c.status, "id": c.short_id}
            except docker_sdk.errors.NotFound:
                result[name] = {"status": "not_found"}
        return result
    except Exception as e:
        raise HTTPException(500, str(e))
