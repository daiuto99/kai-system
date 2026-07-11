"""registry.get, registry.check capabilities (CONTEXT_SPEC.md §5 Tier 4 / MEG §8.23).

Thin capability wrapper over kai-orchestrator/registry.py's file-backed read path.
S7-1 (Plane 532c0d4a, Backlog) builds the full registry (ingest workflow, verify
queue, semantic check()); this wrapper exposes the minimal live read path as a
named capability now, per MEG line 517 ('Exposed as registry.get(domain, key) and
registry.check(claim) capabilities. Verifiers ... check output against the
registry'). Not yet wired into a chat-facing tool schema (KAI_TOOLS) -- that's
verifier-integration scope, tracked under S7-1, not this increment.
"""
import registry as _registry
from models import CapabilityResult
from . import capability


@capability("registry.get")
def get(domain: str, key: str, advisor: str = None, project: str = None, **_) -> CapabilityResult:
    fact = _registry.get(domain, key, advisor=advisor, project=project)
    if fact is None:
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "not_found", "domain": domain, "key": key})
    return CapabilityResult(ok=True, status="succeeded", data={"fact": fact},
                            verification={"verified": True, "method": "registry_lookup"})


@capability("registry.check")
def check(claim: str, advisor: str = None, **_) -> CapabilityResult:
    matches = _registry.check(claim, advisor=advisor)
    return CapabilityResult(ok=True, status="succeeded",
                            data={"claim": claim, "matches": matches, "count": len(matches)},
                            verification={"verified": True, "method": "registry_check"})
