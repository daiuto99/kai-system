from dataclasses import dataclass, field  # noqa: F401
from typing import Optional, Any  # noqa: F401

@dataclass
class Job:
    id: str
    type: str
    inputs: dict
    status: str
    current_step: Optional[str] = None
    approval_policy: str = "auto"
    artifacts: Optional[dict] = None
    error_summary: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

@dataclass
class Step:
    id: str
    job_id: str
    name: str
    capability: Optional[str] = None
    input: Optional[dict] = None
    status: str = "pending"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[dict] = None
    verification: Optional[dict] = None
    retry_count: int = 0
    error: Optional[str] = None

@dataclass
class Event:
    id: str
    type: str
    job_id: Optional[str] = None
    step_id: Optional[str] = None
    payload: Optional[dict] = None
    created_at: str = ""

@dataclass
class CapabilityResult:
    ok: bool
    status: str = "succeeded"
    data: Optional[dict] = None
    verification: Optional[dict] = None
    transport_used: Optional[str] = None
    error: Optional[dict] = None

@dataclass
class StepDef:
    """Workflow step definition (not a DB row)."""
    name: str
    capability: Optional[str] = None
    step_type: str = "auto"   # auto | decision | creative_output | approval_gate
    verify_fn: Optional[str] = None
    finalize: bool = False
    max_retries: int = 3
