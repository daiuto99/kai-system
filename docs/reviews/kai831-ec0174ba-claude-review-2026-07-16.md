# Rule-#9 review — KAI-831 / ec0174ba (specialist delegation through the Memory Service) — PASS

**Reviewer:** Claude (LSE session, 2026-07-16) · **Builder:** Codex · **Commit under review:** 7f82818 (tree-identical restore of reverted 64a9957 — this is the first review of the wiring itself)

## Verdict: PASS — build correct; gate item (a) closed per Leo decision (see below)

## Independently verified

1. **Wiring.** Local specialist context assembly fully retired from `_consult_specialist` (persona file read, business_profile concat, ad-hoc Qdrant search — all removed); Memory Service `POST /context/assemble` is the only context path. Fails closed on assembly error. Consult result carries an `assembly` block (package_id, project_scope, tier3_hits, tier4_fact_ids).
2. **§7.2 compliance (decision 38fa2a35).** `active_project` flows only from the message boundary: `council_message(req.project)` → `_run_agentic_loop(active_project=...)` → injected as `_active_project` into consult_specialist tool input, unconditionally overwriting any model-supplied value. No project → no project param sent (globals + specialist memory). No mid-delegation prompting anywhere.
3. **Unit harness reproduced** in deployed container: `PASS test_cross_domain_consult_without_project_uses_memory_package_and_trail`, `PASS test_specialist_handler_uses_server_owned_message_project`.
4. **Deployed parity:** running kai-council-api container code contains the Memory Service consult path.
5. **Fresh live gate run (reviewer-fired, not builder evidence).** Cross-domain request to KAI (channel `kai`, no project) → KAI consulted architect and synthesized its reply. Trail in `assembly_log`: KAI package `1ff024c8` (key `["kai","claude:rule9-review"]`) → consult package `8de88fda` (key `["architect","consult:architect"]`), t4 facts (7 ids incl. leo-synergy-rig-001), t5 blocks incl. `persona`, threat-scanned. Builder-claimed pair (ec39a824 → 6bce28c1) also present in the trail.
6. **Scoped-fact routing proven live:** `m0smoke` assemble returns its two advisor-scoped facts; architect assemble excludes m0smoke- and kai-scoped facts. `registry.facts_for` scoping is correct in production.

## Material caveat — gate (a) substance, and Leo's ruling

No specialist has real seeded knowledge today: architect Qdrant collection = 0 points; all 26 production registry facts are `advisor: None` (global); the only scoped facts belong to `kai` and the `m0smoke` fixture. The live consult therefore carried **global** facts — the builder handoff presented these as "seeded facts," which is misleading as gate evidence; the specialist received nothing it would not have received as any advisor. The delegation/scoping **mechanism** is fully proven (items 5–6); the missing piece is content, which Leo has explicitly deferred to M-C.

**Leo decision (2026-07-16, blocking question):** accept the mechanism proof; mark KAI-831 Done; real specialist facts arrive with M-C seeding and require no further code. Recorded here per no-theater — the gate is closed on mechanism + fixture proof, not on real specialist knowledge.

## Scope check

Build touched only the consult path + router propagation + its test. Tier 3 note: architect recall returned no hits (empty collection) — expected; `wordpress` allowlist gap already filed (e9ad1421). Mac tracked mirror (execute_tool.py, router.py) verified byte-identical to 7f82818 and committed by reviewer (builder sandbox blocked the mirror commit).
