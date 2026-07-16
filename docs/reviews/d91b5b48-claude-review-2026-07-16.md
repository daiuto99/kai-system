# Rule-#9 review — d91b5b48 (Memory Service specialist persona resolution) — PASS

**Reviewer:** Claude (LSE session, 2026-07-16) · **Builder:** Codex · **Commits under review:** 722cfce (red), c74c462 (fix)
**Ticket:** d91b5b48 — Tier 5 loads 60_Council/<advisor>/<ADVISOR>.md only; registry specialists at 60_Council/specialists/<id>.md returned 404 — blocked KAI-831 live gate.

## Verdict: PASS

## What was verified (independently, nothing trusted from builder transcript)

1. **Red-before reproduced.** Worktree at 722cfce, test run inside the deployed container image:
   `tests/test_d91_specialist_persona.py` FAILED (AssertionError at the specialist assertion — Persona not found: architect). Worktree removed after.
2. **Green-after reproduced.** Same test in the deployed container at HEAD (c74c462): `1 passed`.
3. **Deployed parity.** Running container code contains the fix (`get_specialist` present in /app/context_service.py and /app/function_map_read.py); green test ran against the deployed image, not a host checkout.
4. **Live behavior.** In-container against localhost:8003:
   - `POST /context/assemble` advisor=architect → 200, package_id 25dc0bbe-a80f-47f1-9dbb-eb056296ad65, persona content present in package.
   - `GET /context/persona?advisor=kai` → 200 (advisor regression intact).
   - `GET /context/persona?advisor=architect` → 200 (specialist now resolves).
   - `GET /context/persona?advisor=nonexistent-xyz` → 404 (still fails closed).
5. **Pattern 5 (class, not instance).** `grep` confirms one persona-resolution site ("Persona not found" single occurrence, context_service.py:594); fix is registry-authoritative (`specialists.json` declared `file` path via `fm.get_specialist`), no hardcoded second directory glob. Registry integrity: 18/18 specialists declare a `file` that exists under /home/leo/vault.
6. **Design notes (acceptable, recorded):** fallback triggers only when the advisor-directory file is absent; unknown ids still fail closed. `declared_path` is joined to VAULT_PATH without a traversal guard — registry is internal single-writer vault data, acceptable; worth a guard if the registry ever gains additional writers.

## Findings (non-blocking, filed separately)

- **Tier 3 allowlist gap:** registry specialist `wordpress` is absent from `_VALID_COLLECTIONS` (context_service.py) — its Tier 3 recall silently skips. Intentional today (no Qdrant collection until WP seeding, M-E), but forget-shaped: filed as its own Plane ticket so WP Foundation adds the collection + allowlist entry together.

## Scope check

Build stayed inside ticket scope: loader/allowlist resolution only; KAI-831 consult wiring (reverted 0e3cdf2) not touched. KAI-831 may resume its live trail gate.
