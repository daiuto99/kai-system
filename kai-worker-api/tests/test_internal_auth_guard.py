"""Regression guard for the internal-auth class fix (Bug 48f85706 / aec2d486,
Recovery-Plan Step 1). Rewritten 2026-07-11 after independent review
(docs/reviews/internal-auth-codex-review.md, finding F3) found the original
guard evadable in five distinct ways. Every fix below cites the finding it
closes.

kai-worker-api authenticates every route (bar /health + webhooks). Every
internal service-to-service call to the worker MUST carry the worker
Basic-auth credential. This bounded static AST guard fails the build when a
supported Python caller shape regresses to sending no credential. It does not
claim that arbitrary program analysis is un-evadable. It complements the
runtime invariant `inv_internal_worker_auth`
(kai-scheduler/invariants.py), which proves enforcement end-to-end at runtime.

What this guard can and cannot see (stated explicitly, not silently):
  * Scans every directory under kai-system root containing *.py files
    (discovered dynamically — F3 bullet 1: no more fixed 6-dir allowlist that
    misses future service directories).
  * Cannot parse nginx.conf, JavaScript, or the n8n workflow database. The
    production nginx proxy is verified separately; the Vite worker proxy is
    forbidden separately; n8n is an explicit, recorded accepted-risk
    (see docs/reviews/internal-auth-rework-2026-07-11.md F2/n8n disposition)
    intersecting the S7-9 retirement track — not hacked around here.

Runnable two ways:
  * pytest:   pytest kai-worker-api/tests/test_internal_auth_guard.py
  * directly: python3 kai-worker-api/tests/test_internal_auth_guard.py
              (prints the full verified call-site inventory; exit 1 on any
               violation)
"""
import ast
import re
from pathlib import Path

# kai-system root (…/kai-system/kai-worker-api/tests/this_file)
ROOT = Path(__file__).resolve().parents[2]

# Directories that are never service code, even though some contain stray
# *.py files (e.g. a one-off script left in a data dir).
_DIR_DENYLIST = {
    ".git", ".claude", ".pytest_cache", ".ruff_cache", "__pycache__",
    "secrets", "logs", "n8n-data", "n8n-workflows", "docs", "docker-socket-proxy",
    "litellm", "kai-wordpress-plugin",
}

# Literal substrings that identify the worker's own network address. Matched
# against reconstructed string content, not identifier names — this is what
# catches literal URLs regardless of what variable (if any) they're assigned
# to (F3 bullet 2).
_WORKER_HOST_MARKERS = ("kai-worker-api", "localhost:8001", "127.0.0.1:8001")

# Routes the worker exempts from auth (kai-worker-api/main.py::_NO_AUTH).
# Matched by EXACT reconstructed path, never substring (F3 bullet 3 — the
# committed guard's `p in path` check wrongly exempted /system/health because
# it contains "/health").
NO_AUTH_PATHS = {
    "/health",
    "/github/webhook",
    "/slack/events",
    "/telegram/webhook",
    "/mode_lock/slack_callback",
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "request", "stream", "urlopen"}


def _discover_service_dirs(root: Path) -> list[Path]:
    """Every top-level directory under root that contains at least one .py
    file, minus the denylist. Replaces the old fixed SERVICE_DIRS list so a
    newly added service directory is covered automatically."""
    dirs = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in _DIR_DENYLIST or child.name.startswith("."):
            continue
        if any(child.rglob("*.py")):
            dirs.append(child)
    return dirs


# --------------------------------------------------------------------------
# Worker-base-name alias resolution (F3 bullet 2: "_WORKER_BASE and
# _WORKER_API_URL evade it, as do... aliases"). Instead of a hardcoded
# {"WORKER_URL", "WORKER_API"} identifier set, discover every name in the
# file whose value is (transitively) a literal worker URL: a direct string
# literal containing a worker host marker, an os.environ.get(...) / getenv(
# ...) call whose default argument is such a literal, string concatenation of
# such parts, or a bare reference to another already-known worker name. Fixed
# point over the whole file so arbitrary alias chains (A = "...", B = A,
# C = B) all resolve.
# --------------------------------------------------------------------------

def _string_const(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_is_worker_host(s: str) -> bool:
    return any(marker in s for marker in _WORKER_HOST_MARKERS)


def _constant_string(node: ast.AST) -> str | None:
    """Fold literal-only string concatenation, including split host tokens."""
    s = _string_const(node)
    if s is not None:
        return s
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string(node.left)
        right = _constant_string(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            parts.append(value.value)
        return "".join(parts)
    return None


def _expr_is_worker_base(node: ast.AST, known: set) -> bool:
    s = _constant_string(node)
    if s is not None:
        return _literal_is_worker_host(s)
    if isinstance(node, ast.Name):
        return node.id in known
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _expr_is_worker_base(node.left, known) or _expr_is_worker_base(node.right, known)
    if isinstance(node, ast.JoinedStr):
        return any(
            (isinstance(v, ast.FormattedValue) and _expr_is_worker_base(v.value, known))
            or (isinstance(v, ast.Constant) and isinstance(v.value, str) and _literal_is_worker_host(v.value))
            for v in node.values
        )
    if isinstance(node, ast.Call):
        func = node.func
        is_getenv = (
            (isinstance(func, ast.Attribute) and func.attr in ("get", "getenv"))
            or (isinstance(func, ast.Name) and func.id == "getenv")
        )
        if is_getenv and len(node.args) >= 2:
            return _expr_is_worker_base(node.args[1], known)
    return False


def _assign_pairs_in(nodes) -> list:
    return [
        (t.id, n.value)
        for n in nodes
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    ]


def _resolve_worker_base_names(assign_pairs: list, seed: set = frozenset()) -> set:
    """Fixed-point pass over a given set of (name, value) assign pairs: every
    NAME bound to a worker-base expression, given names already known (seed).
    Scope-restricted by the caller (module-level assigns for the global set,
    a single function's own assigns layered on top for a local set) — NOT
    file-global. A file-global version of this specific resolution caused a
    real false positive during development: an unrelated local variable named
    `url` in one function was treated as worker-referencing because a
    different function elsewhere in the same file also had a local `url`
    that genuinely did reference the worker. Same class of bug as F3 bullet 5
    (auth-tracking scope leakage) — fixed the same way, by scope isolation."""
    known = set(seed)
    changed = True
    while changed:
        changed = False
        for name, value in assign_pairs:
            if name not in known and _expr_is_worker_base(value, known):
                known.add(name)
                changed = True
    return known


def _url_references_worker(node: ast.AST, known: set) -> bool:
    """True if a call's URL argument — f-string, literal, concatenation, or a
    bare Name — resolves to the worker (F3 bullet 2: literal URLs and
    concatenation, not just the exact-identifier f-string case)."""
    return _expr_is_worker_base(node, known)


def _static_path(node: ast.AST) -> str:
    """Reconstruct the literal portion of a URL/path expression. Interpolated
    (non-constant) segments are rendered as a `<expr>` placeholder rather than
    silently dropped, so a dynamic path can never spuriously equal a static
    NO_AUTH_PATHS entry (F3 bullet 3)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                parts.append("<expr>")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _static_path(node.left) + _static_path(node.right)
    return "<expr>"


def _is_no_auth_exact(full_path_with_base_stripped: str) -> bool:
    """Exact match only — the old substring check (`p in path`) is exactly
    the bug that let /system/health pass as exempt (F3 bullet 3)."""
    return full_path_with_base_stripped in NO_AUTH_PATHS


def _path_after_worker_base(node: ast.AST, known: set) -> str:
    """Strip the worker-base prefix off a reconstructed path so NO_AUTH
    matching compares just the route, e.g. f"{WORKER_URL}/health" -> "/health"."""
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                if isinstance(v.value, ast.Name) and v.value.id in known:
                    continue  # the worker-base part itself — drop it
                parts.append("<expr>")
        return "".join(parts)
    s = _string_const(node)
    if s is not None:
        for marker_host in ("http://kai-worker-api:8001", "http://localhost:8001", "http://127.0.0.1:8001"):
            if s.startswith(marker_host):
                return s[len(marker_host):]
        return s
    return _static_path(node)


def _is_falsy_auth_value(value_node: ast.AST) -> bool:
    """F3 bullet 4: `auth=False`, an empty string, or an empty tuple must be
    treated as UNAUTHENTICATED, not accepted as any-non-None-passes."""
    if isinstance(value_node, ast.Constant):
        return value_node.value in (None, False, "")
    if isinstance(value_node, (ast.Tuple, ast.List)) and not value_node.elts:
        return True
    return False


def _has_auth_kwarg(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "auth":
            return not _is_falsy_auth_value(kw.value)
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


def _client_base_url_worker(node: ast.AST, known: set) -> bool:
    """True if an httpx.Client(base_url=...) points at the worker — covers
    the `client = httpx.Client(base_url=WORKER_URL); client.get("/tasks")`
    wrapper pattern the committed guard couldn't see at all (F3 bullet 2)."""
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
    for kw in node.keywords:
        if kw.arg == "base_url":
            return _expr_is_worker_base(kw.value, known)
    return False


def _walk_scope(root: ast.AST):
    """DFS over root's descendants that does NOT descend into nested
    function/async-function bodies. Each function is scanned as its own
    independent scope elsewhere — this is what fixes F3 bullet 5: the
    committed guard collected authenticated-client variable names with a
    single file-global `ast.walk(tree)`, so an authenticated `client` in one
    handler silently "authenticated" a same-named bare `client` in another."""
    out = []

    def rec(node):
        for child in ast.iter_child_nodes(node):
            out.append(child)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue  # nested/sibling scope — its body is walked separately
            rec(child)

    rec(root)
    return out


def _iter_scopes(tree: ast.Module):
    """Module top-level (function bodies excluded — they're their own scope
    below) plus every function/async function in the file, each isolated."""
    yield tree
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n


def _collect_scope_client_vars(scope: ast.AST, known: set) -> tuple[set, dict]:
    """Returns (authed_var_names, worker_base_var_names -> authed_bool),
    both scoped to just this function/module — not the whole file."""
    authed = set()
    worker_base = {}
    for n in _walk_scope(scope):
        creator = None
        target_names = []
        if isinstance(n, ast.Assign):
            creator = n.value
            target_names = [t.id for t in n.targets if isinstance(t, ast.Name)]
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if isinstance(item.optional_vars, ast.Name):
                    _record(item.context_expr, item.optional_vars.id, known, authed, worker_base)
            continue
        if creator is not None:
            for name in target_names:
                _record(creator, name, known, authed, worker_base)
    return authed, worker_base


def _bound_names(scope: ast.AST) -> set[str]:
    """Names local to a scope; inherited module clients with these names shadow."""
    names = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        names.update(arg.arg for arg in (*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs))
        if scope.args.vararg:
            names.add(scope.args.vararg.arg)
        if scope.args.kwarg:
            names.add(scope.args.kwarg.arg)
    for node in _walk_scope(scope):
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            names.update(
                item.optional_vars.id
                for item in node.items
                if isinstance(item.optional_vars, ast.Name)
            )
    return names


def _record(creator: ast.AST, name: str, known: set, authed: set, worker_base: dict):
    if _client_created_with_auth(creator):
        authed.add(name)
    if _client_base_url_worker(creator, known):
        worker_base[name] = name in authed


def _call_url_node(call: ast.Call) -> ast.AST | None:
    """URL supplied positionally or as url= (requests/httpx/urllib)."""
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg == "url":
            return keyword.value
    return None


def _iter_worker_calls_in_scope(scope: ast.AST, known: set, worker_base_vars: dict):
    """Yield (call_node, receiver_name_or_None, path_node_or_None) for every
    HTTP-verb call in this scope whose URL references the worker, either
    directly (URL argument) or via a base_url= client (F3 bullet 2)."""
    for n in _walk_scope(scope):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        if not isinstance(func, ast.Attribute) or func.attr not in HTTP_METHODS:
            continue
        recv = func.value.id if isinstance(func.value, ast.Name) else None
        url_node = _call_url_node(n)
        if url_node is not None and _url_references_worker(url_node, known):
            yield n, recv, url_node
        elif recv is not None and recv in worker_base_vars:
            yield n, recv, url_node


def _discover_url_wrappers(tree: ast.Module) -> dict[str, list[tuple[int, str, bool]]]:
    """Find one-hop local wrappers whose parameter becomes an HTTP URL.

    This covers the demonstrated wrapper-parameter evasion. It is deliberately
    bounded one-hop analysis, not a claim to solve arbitrary data flow.
    """
    wrappers = {}
    for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        params = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
        positions = {arg.arg: index for index, arg in enumerate(params)}
        found = []
        for call in (n for n in _walk_scope(fn) if isinstance(n, ast.Call)):
            func = call.func
            if not isinstance(func, ast.Attribute) or func.attr not in HTTP_METHODS:
                continue
            url_node = _call_url_node(call)
            if isinstance(url_node, ast.Name) and url_node.id in positions:
                found.append((positions[url_node.id], url_node.id, _has_auth_kwarg(call)))
        if found:
            wrappers[fn.name] = found
    return wrappers


def _iter_worker_wrapper_calls(scope: ast.AST, known: set, wrappers: dict):
    for call in (n for n in _walk_scope(scope) if isinstance(n, ast.Call)):
        if not isinstance(call.func, ast.Name) or call.func.id not in wrappers:
            continue
        for position, param_name, inner_has_auth in wrappers[call.func.id]:
            actual = call.args[position] if len(call.args) > position else None
            if actual is None:
                actual = next((kw.value for kw in call.keywords if kw.arg == param_name), None)
            if actual is not None and _url_references_worker(actual, known):
                yield call, call.func.id, actual, inner_has_auth


_INTENTIONAL_PROBE_MARKER = "# GUARD: intentional-unauthenticated-probe"


def _is_intentional_probe(src_lines: list, lineno: int) -> bool:
    """A call may deliberately send NO credential to prove the worker rejects
    it (e.g. inv_internal_worker_auth's own boundary self-test) — that is the
    invariant working as designed, not a caller regression. Recognized only
    via an explicit, visible marker comment on the call's own line or the
    line immediately above; never inferred, so it can't silently swallow a
    real violation the way the old guard's blanket exemptions did."""
    for ln in (lineno, lineno - 1):
        if 1 <= ln <= len(src_lines) and _INTENTIONAL_PROBE_MARKER in src_lines[ln - 1]:
            return True
    return False


def _scan_python_source(src: str, rel: str) -> tuple[list[str], list[str]]:
    """Analyze one parsed Python source with bounded cross-scope data flow."""
    tree = ast.parse(src)
    src_lines = src.splitlines()
    verified, violations = [], []
    global_known = _resolve_worker_base_names(_assign_pairs_in(_walk_scope(tree)))
    global_authed, global_worker_base = _collect_scope_client_vars(tree, global_known)
    wrappers = _discover_url_wrappers(tree)

    for scope in _iter_scopes(tree):
        if scope is tree:
            known = global_known
            authed_vars = set(global_authed)
            worker_base_vars = dict(global_worker_base)
        else:
            known = _resolve_worker_base_names(
                _assign_pairs_in(_walk_scope(scope)), seed=global_known
            )
            local_bound = _bound_names(scope)
            local_authed, local_worker_base = _collect_scope_client_vars(scope, known)
            authed_vars = {name for name in global_authed if name not in local_bound} | local_authed
            worker_base_vars = {
                name: value
                for name, value in global_worker_base.items()
                if name not in local_bound
            }
            worker_base_vars.update(local_worker_base)

        for call, recv, path_node in _iter_worker_calls_in_scope(scope, known, worker_base_vars):
            loc = f"{rel}:{call.lineno}"
            path = _path_after_worker_base(path_node, known) if path_node is not None else "<relative>"
            if _is_no_auth_exact(path):
                verified.append(f"{loc}  {path}  [NO_AUTH exempt]")
            elif _has_auth_kwarg(call):
                verified.append(f"{loc}  {path}  [per-call auth]")
            elif recv is not None and recv in authed_vars:
                verified.append(f"{loc}  {path}  [authed client '{recv}']")
            elif recv is not None and worker_base_vars.get(recv):
                verified.append(f"{loc}  {path}  [authed base_url client '{recv}']")
            elif _is_intentional_probe(src_lines, call.lineno):
                verified.append(f"{loc}  {path}  [intentional no-auth probe]")
            else:
                violations.append(
                    f"{loc}  {path}  via '{recv or '<expr>'}' — NO auth (bare internal worker call)"
                )

        for call, wrapper, path_node, inner_has_auth in _iter_worker_wrapper_calls(scope, known, wrappers):
            loc = f"{rel}:{call.lineno}"
            path = _path_after_worker_base(path_node, known)
            if inner_has_auth:
                verified.append(f"{loc}  {path}  [authed one-hop wrapper '{wrapper}']")
            else:
                violations.append(
                    f"{loc}  {path}  via wrapper '{wrapper}' — NO auth (bare internal worker call)"
                )
    return verified, violations


def _scan():
    """Return (verified, violations, skipped) across every discovered service dir."""
    verified, violations, skipped = [], [], []
    for base in _discover_service_dirs(ROOT):
        for py in sorted(base.rglob("*.py")):
            rel = py.relative_to(ROOT)
            if "/tests/" in f"/{rel}/" or py.name.startswith("test_") or "__pycache__" in py.parts:
                continue
            try:
                file_verified, file_violations = _scan_python_source(py.read_text(), str(rel))
            except Exception as e:
                # Parse/analysis failures are surfaced, never silently dropped.
                skipped.append(f"{rel}: {e}")
                continue
            verified.extend(file_verified)
            violations.extend(file_violations)
    return verified, violations, skipped


def test_no_bare_internal_worker_calls():
    """No internal call to kai-worker-api may omit the auth credential."""
    verified, violations, skipped = _scan()
    assert verified, "guard found zero worker call sites — scan is broken (wrong ROOT?)"
    assert not violations, (
        "Bare (unauthenticated) internal worker call(s) — attach auth=_worker_auth():\n  "
        + "\n  ".join(violations)
    )
    assert not skipped, (
        "File(s) in a scanned service directory failed to parse — guard cannot "
        "vouch for coverage until these are fixed or explicitly excluded:\n  "
        + "\n  ".join(skipped)
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


def test_no_bare_nginx_worker_proxy():
    """kai-web/nginx.conf's /api/ location must attach the worker credential
    (F2 finding: this proxy previously forwarded nothing, so every protected
    worker route 401'd through the dashboard). Not an AST check — nginx.conf
    isn't Python — but the guard must still fail loudly if this regresses."""
    conf = (ROOT / "kai-web" / "nginx.conf").read_text()
    api_block = re.search(r"location\s+/api/\s*\{([^}]*)\}", conf, re.DOTALL)
    assert api_block, "kai-web/nginx.conf: no location /api/ block found — proxy config missing or renamed"
    assert "proxy_set_header Authorization" in api_block.group(1), (
        "kai-web/nginx.conf location /api/ does not attach a worker Authorization "
        "header — dashboard calls to protected worker routes will 401/503"
    )


def test_vite_worker_proxy_is_non_executable():
    """F2: Vite must not expose a second bare /api→worker execution path.

    Vite has no runtime Docker-secret wiring. The safe disposition is to omit
    its worker proxy and use the authenticated production nginx endpoint for
    UI integration tests.
    """
    vite = (ROOT / "kai-web" / "vite.config.js").read_text()
    assert "kai-worker-api:8001" not in vite, (
        "kai-web/vite.config.js must not target kai-worker-api directly; "
        "the Vite dev server cannot attach the Docker worker credential"
    )
    assert not re.search(r"['\"]\/api['\"]\s*:\s*\{", vite), (
        "kai-web/vite.config.js must not define an executable /api proxy"
    )


# --------------------------------------------------------------------------
# Adversarial self-test — proves the four specific evasions Codex's review
# demonstrated against the OLD guard no longer work (docs/reviews/
# internal-auth-codex-review.md F3: literal_url_detected=False,
# alias_detected=False, system_health_exempt=True, auth_false_accepted=True).
# --------------------------------------------------------------------------

def test_adversarial_probes_all_flip():
    probes = {}

    literal_url_src = (
        "import httpx\n"
        "def f():\n"
        "    httpx.post('http://kai-worker-api:8001/tasks', json={})\n"
    )
    tree = ast.parse(literal_url_src)
    known = _resolve_worker_base_names(_assign_pairs_in(_walk_scope(tree)))
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(getattr(n.func, "value", None), ast.Name) and n.func.value.id == "httpx" and n.func.attr == "post")
    probes["literal_url_detected"] = _url_references_worker(call.args[0], known)

    alias_src = (
        "import os\n"
        "_WORKER_BASE = os.environ.get('WORKER_API_URL', 'http://kai-worker-api:8001')\n"
        "_ALIAS = _WORKER_BASE\n"
        "import httpx\n"
        "def f():\n"
        "    httpx.get(f'{_ALIAS}/plane/issues')\n"
    )
    tree = ast.parse(alias_src)
    known = _resolve_worker_base_names(_assign_pairs_in(_walk_scope(tree)))
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(getattr(n.func, "value", None), ast.Name) and n.func.value.id == "httpx" and n.func.attr == "get")
    probes["alias_detected"] = _url_references_worker(call.args[0], known)

    health_src = (
        "WORKER_URL = 'http://kai-worker-api:8001'\n"
        "import httpx\n"
        "def f():\n"
        "    httpx.get(f'{WORKER_URL}/system/health')\n"
    )
    tree = ast.parse(health_src)
    known = _resolve_worker_base_names(_assign_pairs_in(_walk_scope(tree)))
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(getattr(n.func, "value", None), ast.Name) and n.func.value.id == "httpx" and n.func.attr == "get")
    path = _path_after_worker_base(call.args[0], known)
    probes["system_health_exempt"] = _is_no_auth_exact(path)

    auth_false_src = (
        "import httpx\n"
        "def f():\n"
        "    httpx.get('http://kai-worker-api:8001/tasks', auth=False)\n"
    )
    tree = ast.parse(auth_false_src)
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(getattr(n.func, "value", None), ast.Name) and n.func.value.id == "httpx" and n.func.attr == "get")
    probes["auth_false_accepted"] = _has_auth_kwarg(call)

    assert probes == {
        "literal_url_detected": True,
        "alias_detected": True,
        "system_health_exempt": False,
        "auth_false_accepted": False,
    }, probes


def _reviewer_evasion_results() -> dict[str, int]:
    """Run the five concrete evasions from the 2026-07-11 Codex rejection."""
    probes = {
        "split_concat": (
            "import httpx\n"
            "httpx.get('http://' + 'kai-worker-' + 'api:8001/tasks')\n"
        ),
        "requests_url_keyword": (
            "import requests\n"
            "requests.get(url='http://kai-worker-api:8001/tasks')\n"
        ),
        "urllib_urlopen": (
            "import urllib.request\n"
            "urllib.request.urlopen('http://kai-worker-api:8001/tasks')\n"
        ),
        "module_base_url_cross_scope": (
            "import httpx\n"
            "WORKER = 'http://kai-worker-api:8001'\n"
            "client = httpx.Client(base_url=WORKER)\n"
            "def run():\n"
            "    client.get('/tasks')\n"
        ),
        "wrapper_parameter": (
            "import requests\n"
            "WORKER = 'http://kai-worker-api:8001'\n"
            "def fetch(target):\n"
            "    return requests.get(target)\n"
            "fetch(WORKER + '/tasks')\n"
        ),
    }
    return {
        name: len(_scan_python_source(source, f"probe/{name}.py")[1])
        for name, source in probes.items()
    }


def test_reviewer_evasions_are_flagged():
    results = _reviewer_evasion_results()
    assert results == {
        "split_concat": 1,
        "requests_url_keyword": 1,
        "urllib_urlopen": 1,
        "module_base_url_cross_scope": 1,
        "wrapper_parameter": 1,
    }, results


if __name__ == "__main__":
    import sys

    verified, violations, skipped = _scan()
    print(f"ROOT: {ROOT}")
    print(f"\nVERIFIED internal worker call sites ({len(verified)}):")
    for v in verified:
        print(f"  ✓ {v}")
    if skipped:
        print(f"\nSKIPPED (parse failures — coverage gap, not silently dropped) ({len(skipped)}):")
        for s in skipped:
            print(f"  ? {s}")
    if violations:
        print(f"\nVIOLATIONS ({len(violations)}):")
        for v in violations:
            print(f"  ✗ {v}")
        sys.exit(1)
    if skipped:
        sys.exit(1)
    test_adversarial_probes_all_flip()
    test_reviewer_evasions_are_flagged()
    test_council_shared_client_is_authenticated()
    test_no_bare_nginx_worker_proxy()
    test_vite_worker_proxy_is_non_executable()
    print(f"\nREVIEWER EVASIONS FLAGGED: {_reviewer_evasion_results()}")
    print("\nOK — every internal worker call carries auth.")
    sys.exit(0)
