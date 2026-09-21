import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

# Direct-Gmail read-only path (KAI-1384). Mirrors routes/calendar.py: same OOB
# OAuth flow, same Google OAuth app (reuses google_calendar_client.json), token
# stored separately with the gmail.readonly scope. n8n Gmail path retired.
GMAIL_CREDS_FILE  = VAULT_PATH / "00_System" / "google_gmail_token.json"
GMAIL_CLIENT_FILE = VAULT_PATH / "00_System" / "google_calendar_client.json"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _gmail_service():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        if not GMAIL_CREDS_FILE.exists():
            return None
        creds = Credentials.from_authorized_user_file(str(GMAIL_CREDS_FILE), GMAIL_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GMAIL_CREDS_FILE.write_text(creds.to_json())
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        logger.exception("gmail service error: %s", e)
        return None


@router.get("/gmail/auth-url")
def gmail_auth_url():
    try:
        from google_auth_oauthlib.flow import Flow
        if not GMAIL_CLIENT_FILE.exists():
            raise HTTPException(400, "google_calendar_client.json not found in vault")
        flow = Flow.from_client_secrets_file(
            str(GMAIL_CLIENT_FILE), scopes=GMAIL_SCOPES,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob"
        )
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        return {"auth_url": auth_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("gmail auth url: %s", e)
        raise HTTPException(500, str(e))


class GmailCodeRequest(BaseModel):
    code: str


@router.post("/gmail/auth-code")
def gmail_auth_code(req: GmailCodeRequest):
    try:
        from google_auth_oauthlib.flow import Flow
        if not GMAIL_CLIENT_FILE.exists():
            raise HTTPException(400, "google_calendar_client.json not found in vault")
        flow = Flow.from_client_secrets_file(
            str(GMAIL_CLIENT_FILE), scopes=GMAIL_SCOPES,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob"
        )
        flow.fetch_token(code=req.code)
        creds = flow.credentials
        GMAIL_CREDS_FILE.write_text(creds.to_json())
        return {"ok": True, "message": "Gmail authorized (read-only) and token saved"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("gmail auth code: %s", e)
        raise HTTPException(500, str(e))


def _header(headers: list, name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


@router.get("/gmail/messages")
def gmail_messages(max_results: int = 10, query: str = "is:unread"):
    """Read-only recent messages. Default = unread. Metadata + snippet only —
    never bodies (least-privilege read path). Returns {emails: [...]} and a
    graceful {emails: [], error} when the direct-Google auth is not yet done."""
    svc = _gmail_service()
    if not svc:
        # Honest say-so (KAI-1484): present-but-unusable token = auth expired/revoked.
        if GMAIL_CREDS_FILE.exists():
            return {"emails": [], "feed_status": "auth_failed",
                    "error": "gmail auth expired or revoked — re-consent needed (GET /gmail/auth-url), KAI-1484"}
        return {"emails": [], "feed_status": "not_configured",
                "error": "gmail not configured — no token yet (GET /gmail/auth-url), KAI-1384"}
    try:
        max_results = max(1, min(int(max_results), 25))
        listing = svc.users().messages().list(
            userId="me", maxResults=max_results, q=query or ""
        ).execute()
        emails = []
        for ref in listing.get("messages", []):
            msg = svc.users().messages().get(
                userId="me", id=ref["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = msg.get("payload", {}).get("headers", [])
            emails.append({
                "id": msg.get("id"),
                "thread_id": msg.get("threadId"),
                "from": _header(headers, "From"),
                "subject": _header(headers, "Subject"),
                "date": _header(headers, "Date"),
                "snippet": msg.get("snippet", ""),
                "unread": "UNREAD" in msg.get("labelIds", []),
            })
        return {"emails": emails, "count": len(emails), "query": query}
    except Exception as e:
        logger.exception("gmail messages: %s", e)
        return {"emails": [], "error": str(e)}
