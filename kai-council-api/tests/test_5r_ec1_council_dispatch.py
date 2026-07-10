"""
Exit Criterion #1 — run_capability round-trip THROUGH council dispatch.

Path under test:
  execute_tool("run_capability", {...}, advisor)
    → _h_workflows(client, "run_capability", ti, advisor)
      → httpx.POST http://kai-orchestrator:8003/capability/vault.read
        → orchestrator capability
          → vault file read
            → result back to caller

This is the council→orchestrator path, NOT the orchestrator endpoint directly.
The original S5R-1 bug (tool_input/ti NameError) lived in _h_workflows.
"""
import sys
import json
import os

# Run inside council container so imports resolve
sys.path.insert(0, "/app")
os.chdir("/app")

from execute_tool import execute_tool

print("=" * 60)
print("EC#1 — run_capability round-trip via council dispatch")
print("=" * 60)

# Use vault.read on JARVIS_DEFINITION.md — should return content
result = execute_tool(
    "run_capability",
    {
        "capability": "vault.read",
        "inputs": {"path": "00_System/JARVIS_DEFINITION.md"},
    },
    "kai",
)

print(f"\nResult type: {type(result)}")
print(f"Result: {json.dumps(result, indent=2, default=str)[:500]}")

# Verify round-trip
ok = isinstance(result, dict) and (
    result.get("ok") is True
    or "content" in result
    or result.get("data", {})
)

if not ok:
    print("\n[FAIL] round-trip returned no content or ok=False")
    sys.exit(1)

print("\n[PASS] run_capability round-trip via council dispatch completed")
print(f"       Orchestrator returned data with keys: {list(result.keys())}")
