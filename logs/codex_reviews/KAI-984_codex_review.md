# Codex Review Log — KAI-984 (authorized provisioning path — tailnet-only)

Protocol: Claude builds -> Codex verifies directly (`codex exec`, cross-provider). Logged per Leo's
standing caveat (2026-07-25). Ticket: KAI-984 (b70f4b3d). Session: 2026-07-27.

---

## Round 1 — increment 1 (tailnet_guard, build 0650df7) -> FAIL
KAI-984-INC1 VERIFICATION — FAIL
FAIL-CLOSED: Broken — multiple malformed, ambiguous, offline, and unenrolled inputs return allowed=True; one exception escapes the boundary.
ALLOW-PATH HOLES: Peer Online="false" or 1; Self missing Online or Online=False; arbitrary non-node-ID strings; duplicate status node IDs; duplicate allowlist aliases sharing one ID; TailscaleIPs as a dict, integer, malformed-item-plus-valid-IP list, or multiple CGNAT IPs; pending-unconfirmed enrollment; duplicate JSON keys.
CONCERNS:
 1. Matching is exclusively by ID, not hostname; hostname spoofing was correctly denied.
 2. Canonical IPv4 membership is correct: 100.64.0.0–100.127.255.255 allowed; 100.32.x, 100.63.x, 100.128.x, 101.x, 10.x, IPv6-only, and IPv4-mapped IPv6 denied.
 3. evaluate_target can raise when invalid name stringification fails; malformed IP elements are skipped instead of causing whole-decision denial.
 4. load_allowlist ignores enrollment_status, so the supplied seeded_pending_leo_confirmation file is immediately usable.
MISSING TESTS: strict boolean Online; Self online enforcement; duplicate IDs in status/allowlist; stable-ID validation; strict list[str] IP schema; malformed-IP whole-decision denial; multiple-address ambiguity; enrollment_status; duplicate JSON keys; hostile name coercion; CGNAT boundaries and IPv4-mapped IPv6.
RECOMMENDATION: do NOT — fix first

## Round 2 — hardened (strict Online/node-id/dup-id/strict-IP/enrollment gate) -> FAIL
```
KAI-984-INC1 VERIFICATION — FAIL
FAIL-CLOSED: No—malformed and ambiguous inputs can allow, and one exception path can still raise beyond the boundary; 29 tests passed but miss these cases.
ALLOW-PATH HOLES: Self with enrolled ID and CGNAT IP allows when Online is omitted or null—even with BackendState="Stopped"; duplicate target-name keys in confirmed allowlist JSON select the last, attacker-controlled ID and allow it; malformed Self or non-dict Peer entries are silently ignored when another matching entry exists, allowing an unexpectedly shaped status.
CONCERNS:
 1. Online is not strictly boolean True for Self; missing and explicit null are treated as online.
 2. Hostname spoofing was denied and arbitrary hostnames with the correct stable ID allowed, proving matching is ID-only; off-range IPv4, IPv6-only, IPv4-mapped IPv6, malformed IPs, and multiple CGNAT IPs denied correctly.
 3. _safe_str can still raise when both __str__ and the metaclass __repr__ raise, violating the never-raise boundary.
 4. The current allowlist correctly loads as deny-all because enrollment is pending, but its 0644 leo-owned permissions do not demonstrate the claimed tamper protection.
MISSING TESTS: Self Online omitted/null with stopped backend; duplicate JSON keys; malformed sibling Self/Peer entries poisoning the decision; hostile __str__ plus metaclass __repr__; peer Online omitted/null; missing target ID/IP fields; duplicate ID across Self and Peer.
RECOMMENDATION: do NOT — fix first
```

## Round 3 — hardened (BackendState gate, dup-JSON-keys, safe_str) -> FAIL
```
KAI-984-INC1 VERIFICATION — FAIL
FAIL-CLOSED: No — 34 tests pass, but malformed/ambiguous ordinary-dict inputs can still ALLOW, and hostile inputs can raise past the boundary.
ALLOW-PATH HOLES: missing or empty `Self` with a valid target peer; missing `Peer` with a valid target self; a valid target peer plus malformed sibling `"garbage"` or `{}`; direct allowlist `{"alias1":ID,"alias2":ID}`; direct allowlist `{"target":ID,"poison":42}` — all returned `allowed=True`.
CONCERNS:
 1. `_iter_entries` silently skips malformed peer entries, while container presence and complete shape are not required; this violates the stated “any missing field/unexpected shape denies” rule.
 2. `_safe_str` is not literally non-raising: `__str__` raising `KeyboardInterrupt`/`SystemExit` escapes because only `Exception` is caught. `load_allowlist` has the same `BaseException` escape class.
 3. Stable-ID matching is hostname-independent: spoofed hostname with the wrong ID denied; the correct ID with an arbitrary hostname allowed; duplicate live ID entries denied.
 4. The CGNAT membership check correctly denied off-range-only, IPv6-only, and IPv4-mapped-IPv6 addresses and accepted exact `/10` boundaries. A valid CGNAT address plus an extra off-range IPv4 is accepted, though the off-range address is not selected.
MISSING TESTS: missing/empty `Self`; missing `Peer`; malformed individual peer values; malformed or duplicate-ID allowlists passed directly to `evaluate_target`; `BaseException`-raising `__str__`/path conversion; valid CGNAT plus off-range IPv4.
RECOMMENDATION: do NOT — fix first
```

## Round 4 — hardened (non-dict peer values, allowlist defense-in-depth) -> FAIL
```
KAI-984-INC1 VERIFICATION — FAIL
FAIL-CLOSED: Explicit JSON null in Self.Online is treated as an omitted field and incorrectly reaches allowed=True.
ALLOW-PATH HOLES: {"BackendState":"Running","Self":{"ID":"nzkpgsJk1M11CNTRL","Online":null,"TailscaleIPs":["100.78.94.80"]},"Peer":{}} with allowlist {"kai-worker":"nzkpgsJk1M11CNTRL"} returns allowed=True despite lacking a valid online state.
CONCERNS:
 1. _is_online() uses entry.get("Online", None), collapsing explicit null with the deliberately permitted missing-Self-Online case; this violates the stated rule that falsey/non-True explicit values deny.
MISSING TESTS: Self with explicit Online:null must deny; the existing online=None helper omits the field and therefore does not cover this case.
RECOMMENDATION: do NOT — fix first
```

## Round 5 — fixed explicit-null Online -> FAIL
```
KAI-984-INC1 VERIFICATION — FAIL
FAIL-CLOSED: Broken: `_NODE_ID_RE` uses `$`, which accepts a terminal newline, allowing a non-genuine stable node ID.
ALLOW-PATH HOLES: `name="attacker"`, `allowlist={"attacker":"nABCDEF\n"}`, `status={"BackendState":"Running","Peer":{"x":{"ID":"nABCDEF\n","Online":True,"TailscaleIPs":["100.64.0.1"]}}}` returns `allowed=True`.
CONCERNS:
 1. The same regex flaw lets `load_allowlist` accept a confirmed JSON node ID containing a terminal newline; hostname spoofing, strict Online handling, and CGNAT boundaries otherwise held.
MISSING TESTS: terminal-newline node IDs through both `evaluate_target` and `load_allowlist`; missing `ID`; missing/empty `TailscaleIPs`; Self+Peer duplicate-ID ambiguity
RECOMMENDATION: do NOT — fix first
```

## Round 6 — fixed regex terminal-newline (fullmatch) -> PASS
```
KAI-984-INC1 VERIFICATION — PASS
FAIL-CLOSED: 43/43 tests and 114 independent adversarial probes passed; invalid, missing, ambiguous, offline, spoofed-hostname, and off-CGNAT inputs deny.
ALLOW-PATH HOLES: none found
CONCERNS:
 1. None affecting the stated security property.
MISSING TESTS: direct load_allowlist terminal-newline regression; wrong-type matched ID; additional Online types; empty/string/null TailscaleIPs; IPv4-mapped, CIDR, and newline-suffixed IPs; mapping-method exception paths
RECOMMENDATION: trust this core for the next increment
```

## Outcome
PASS at round 6: 43 tests + 114 independent Codex adversarial probes, no allow-path holes.
The loop caught real defects (Online string-truthiness, `$`-regex newline bypass, duplicate-JSON-key
trust-root injection, missing BackendState gate, explicit-null Online). Settled non-holes: valid-target-
with-missing-sibling (correct to allow); BaseException propagation (still fail-closed). Known tracked gap:
allowlist file tamper-protection = increment 4 (Leo-run). Suggested future test-hardening noted by Codex.

## Increment 2 — provision_policy (secret-name x node authorization)

### Round 1 -> FAIL
```
KAI-984-INC2 VERIFICATION — FAIL
FAIL-CLOSED: Guard denials and exceptions deny correctly, but secret-name authorization is bypassable.
ALLOW-PATH HOLES: `provisionable={"random_secret"}` allows `"random_secret"`; a hostile `str` subclass containing `"../etc/passwd"` can spoof iteration/hash/equality and is allowed.
CONCERNS:
 1. `authorize_provision` trusts a caller-supplied allowlist instead of enforcing `PROVISIONABLE_SECRETS`.
 2. `isinstance(secret_name, str)` permits hostile subclasses; the safer `_valid_secret_name()` is unused.
MISSING TESTS: caller cannot widen the allowlist; hostile `str` subclasses deny; guard exceptions deny; exact allow/deny `tailnet_ip` and `node_id` propagation
RECOMMENDATION: do NOT — fix first
```

### Round 2 -> PASS
```
KAI-984-INC2 VERIFICATION — PASS
FAIL-CLOSED: Confirmed; 16 tests and 212 adversarial probes passed, including guard denial, guard exception, and verdict propagation.
ALLOW-PATH HOLES: none found
CONCERNS:
 1. No implementation-level concerns found.
MISSING TESTS: explicit guard-exception denial; denied-guard node_id preservation; uppercase, Unicode, backslash, tab, CR, and NUL secret-name cases
RECOMMENDATION: trust for next increment
```

Outcome: PASS at round 2 (16 tests + 212 probes). Fixed: caller could widen the provisionable
allowlist via a parameter (now the module constant is the sole policy); isinstance(str) admitted
hostile str subclasses (now exact type(name) is str). Bounds BOTH which-secret and which-node.
