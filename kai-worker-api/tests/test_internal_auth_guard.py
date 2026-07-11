"""Regression guard for the internal-auth class fix (Bug 48f85706 / aec2d486,
Recovery-Plan Step 1).

kai-worker-api authenticates every route (bar /health + webhooks). Every
internal service-to-service call to the worker MUST carry the worker
Basic-auth credential. This static AST guard fails the build if any call site
regresses to sending no credential — "fixed" must mean "can't silently
un-fix." It complements the runtime invariant `inv_internal_worker_auth`
(kai-scheduler/invariants.py), which proves enforcement end-to-end at runtime.

Runnable two ways:
  * pytest:   pytest kai-worker-api/tests/test_internal_auth_guard.py
  * directly: python3 kai-worker-api/tests/test_internal_auth_guard.py
              (prints the full verified call-site inventory; exit 1 on any
               violation)
"""
import ast
from pathlib import Path

# kai-system root (…/kai-system/kai-worker-api/tests/this_file)
ROOT = Path(__file__).resolve().parents[2]

# Services that make internal calls to the worker.
SERVICE_DIRS = [
    "kai-council-api",
    "kai-orchestrator",
    "kai-scheduler",
    "kai-slack-bot",
    "kai-mcp-api",
    "kai-worker-api",
]

# Worker base-URL identifiers used in f-string call URLs.
WORKER_URL_NAMES = {"WORKER_URL", "WORKER_API"}

# Routes the worker exempts from auth (kai-worker-api/main.py::_NO_AUTH).
NO_AUTH_PATHS = {
    "/health",
    "/github/webhook",
    "/slack/events",
    "/telegram/webhook",
    "/mode_lock/slack_callback",
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "request", "stream"}


def _url_references_worker(node: ast.AST) -> bool:
    """True if an f-string URL contains a {WORKER_URL}/{WORKER_API} field."""
    if not isinstance(node, ast.JoinedStr):
        return False
    for v in node.values:
        if isinstance(v, ast.FormattedValue):
            expr = v.value
            if isinstance(expr, ast.Name) and expr.id in WORKER_URL_NAMES:
                return True
    return False


def _static_path(node: ast.AST) -> str:
    """Concatenate the literal parts of an f-string URL (for _NO_AUTH match)."""
    if not isinstance(node, ast.JoinedStr):
        return ""
    return "".join(
        v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
    )


def _is_no_auth(node: ast.AST) -> bool:
    path = _static_path(node)
    return any(p in path for p in NO_AUTH_PATHS)


def _has_auth_kwarg(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "auth":
            # auth=None is not authentication.
            if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                return False
            return True
    return False


def _client_created_with_auth(node: ast.AST) -> bool:
    """True if an httpx.Client(...)/AsyncClient(...) call has a real auth kwarg."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    is_httpx_client = (
        isinstance(func, ast.Attribute)
        and func.attr in ("Client", "AsyncClient")
        and isinstance(func.value, ast.Name)
        and func.value.id == "httpx"
    )
    if not is_httpx_client:
        return False
    return _has_auth_kwarg(node)


def _collect_authed_client_vars(tree: ast.AST) -> set:
    """Names bound to an httpx client that was created WITH auth.

    Covers both `NAME = httpx.Client(auth=...)` and
    `with httpx.Client(auth=...) as NAME:`.
    """
    authed = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and _client_created_with_auth(n.value):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    authed.add(t.id)
        if isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if _client_created_with_auth(item.context_expr) and isinstance(
                    item.optional_vars, ast.Name
                ):
                    authed.add(item.optional_vars.id)
    return authed


def _iter_worker_calls(tree: ast.AST):
    """Yield (call_node, receiver_name) for every httpx-style request whose URL
    references the worker. receiver_name is 'httpx' for direct module calls, or
    the client variable name for `client.get(...)` style calls."""
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        if not isinstance(func, ast.Attribute) or func.attr not in HTTP_METHODS:
            continue
        if not n.args:
            continue
        if not _url_references_worker(n.args[0]):
            continue
        recv = func.value.id if isinstance(func.value, ast.Name) else "<expr>"
        yield n, recv


def _scan():
    """Return (verified, violations) call-site lists across all services."""
    verified, violations = [], []
    for svc in SERVICE_DIRS:
        base = ROOT / svc
        if not base.exists():
            continue
        for py in sorted(base.rglob("*.py")):
            if "/tests/" in str(py) or py.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(py.read_text())
            except Exception:
                continue
            authed_vars = _collect_authed_client_vars(tree)
            for call, recv in _iter_worker_calls(tree):
                loc = f"{py.relative_to(ROOT)}:{call.lineno}"
                path = _static_path(call.args[0]) or "?"
                if _is_no_auth(call.args[0]):
                    verified.append(f"{loc}  {path}  [NO_AUTH exempt]")
                elif _has_auth_kwarg(call):
                    verified.append(f"{loc}  {path}  [per-call auth]")
                elif recv in authed_vars:
                    verified.append(f"{loc}  {path}  [authed client '{recv}']")
                else:
                    violations.append(
                        f"{loc}  {path}  via '{recv}' — NO auth (bare internal worker call)"
                    )
    return verified, violations


def test_no_bare_internal_worker_calls():
    """No internal call to kai-worker-api may omit the auth credential."""
    verified, violations = _scan()
    assert verified, "guard found zero worker call sites — scan is broken (wrong ROOT?)"
    assert not violations, (
        "Bare (unauthenticated) internal worker call(s) — attach auth=_worker_auth():\n  "
        + "\n  ".join(violations)
    )


def test_council_shared_client_is_authenticated():
    """The central council→worker client must be created with auth so every
    tool call inherits it (guards the ~40 handler call sites on `client`)."""
    src = (ROOT / "kai-council-api" / "execute_tool.py").read_text()
    tree = ast.parse(src)
    ok = False
    for n in ast.walk(tree):
        if isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if _client_created_with_auth(item.context_expr):
                    ok = True
    assert ok, "execute_tool.py shared httpx.Client must be created with auth=_worker_auth()"


if __name__ == "__main__":
    import sys

    verified, violations = _scan()
    print(f"ROOT: {ROOT}")
    print(f"\nVERIFIED internal worker call sites ({len(verified)}):")
    for v in verified:
        print(f"  ✓ {v}")
    if violations:
        print(f"\nVIOLATIONS ({len(violations)}):")
        for v in violations:
            print(f"  ✗ {v}")
        sys.exit(1)
    print("\nOK — every internal worker call carries auth.")
    sys.exit(0)
