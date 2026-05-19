from typing import Any, Optional
from pydantic import BaseModel
import httpx


class SafeResponse(BaseModel):
    ok: bool
    status_code: int = 0
    content_type: str = ""
    data: Optional[Any] = None
    body_preview: Optional[str] = None
    is_cloudflare_challenge: bool = False
    is_auth_failure: bool = False
    error: Optional[str] = None


def safe_request(method: str, url: str, timeout: int = 30, **kwargs) -> SafeResponse:
    try:
        r = httpx.request(method, url, timeout=timeout, **kwargs)
        ct = r.headers.get("content-type", "")
        is_cf = r.status_code in (403, 503) and (
            "Just a moment" in r.text or "cf-ray" in r.headers
        )
        data = None
        if "application/json" in ct:
            try:
                data = r.json()
            except Exception:
                pass
        return SafeResponse(
            ok=r.status_code < 400,
            status_code=r.status_code,
            content_type=ct,
            data=data,
            body_preview=r.text[:100000] if data is None else None,
            is_cloudflare_challenge=is_cf,
            is_auth_failure=r.status_code in (401, 403) and not is_cf,
        )
    except httpx.TimeoutException as e:
        return SafeResponse(ok=False, error=f"timeout: {e}")
    except Exception as e:
        return SafeResponse(ok=False, error=str(e))
