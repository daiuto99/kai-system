import json
import logging
from fastapi import APIRouter, HTTPException
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

CONTACTS_FILE = VAULT_PATH / "00_System" / "contacts.json"


def _contacts_load() -> list:
    if CONTACTS_FILE.exists():
        return json.loads(CONTACTS_FILE.read_text())
    return []


def _contacts_save(contacts: list):
    CONTACTS_FILE.write_text(json.dumps(contacts, indent=2))


@router.get("/contacts")
def list_contacts():
    return {"contacts": _contacts_load()}


@router.post("/contacts")
def add_contact(body: dict):
    contacts = _contacts_load()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name required")
    contact = {
        "id": body.get("id") or name.lower().replace(" ", "-"),
        "name": name,
        "aliases": body.get("aliases", [name.lower().split()[0]]),
        "email": body.get("email", ""),
        "slack_id": body.get("slack_id"),
        "role": body.get("role", ""),
        "notes": body.get("notes", ""),
    }
    contacts = [c for c in contacts if c["id"] != contact["id"]]
    contacts.append(contact)
    _contacts_save(contacts)
    return {"ok": True, "contact": contact}


@router.patch("/contacts/{contact_id}")
def update_contact(contact_id: str, body: dict):
    contacts = _contacts_load()
    for c in contacts:
        if c["id"] == contact_id:
            c.update({k: v for k, v in body.items() if k != "id"})
            _contacts_save(contacts)
            return {"ok": True, "contact": c}
    raise HTTPException(404, f"Contact {contact_id} not found")


@router.get("/contacts/lookup")
def lookup_contact(q: str):
    contacts = _contacts_load()
    q_lower = q.lower().strip()
    for c in contacts:
        if (q_lower == c["id"] or
            q_lower in [a.lower() for a in c.get("aliases", [])] or
            q_lower in c.get("name", "").lower() or
            q_lower == c.get("email", "").lower()):
            return {"found": True, "contact": c}
    return {"found": False, "query": q}
