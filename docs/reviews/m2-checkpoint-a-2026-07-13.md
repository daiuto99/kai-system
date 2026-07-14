# M2 acceptance re-run + CHECKPOINT A — 2026-07-13 (late UTC 2026-07-14)

**Plane issue:** KAI-786 `73c92f0b-07be-4c42-b1c7-e85e9076ee71` (M2: seed Roads + Sky)
**Run by:** Claude (rule #9 — Codex ran the production seed `dfb4c95`; Claude independently runs the recall checks)
**Preconditions verified:** registry at 29 facts, SHA `195331d189386bcbc23a44367c755231a1a2dd4881ee2030f85295e73f9af8e3` before AND after this session (reads only — no re-seed); reader at KAI-788 fix `c34baf8` (review-passed, worker HEAD lineage). Scope comment `c1bc7627` posted on KAI-786 with readback before execution. All queries use LITERAL vocabulary per the documented KAI-789 lexical interim bound.

## VERDICT: M2 acceptance PASS (a–e) · CHECKPOINT A **GO** (all three proof parts hold)

---

## PART 1 — M2 acceptance (a)–(e), live `/context/assemble`, reviewer package IDs

**(a) Roads main-guitar query — PASS.** "What is Leo main number one guitar and why does it fit him?" → package `ffb18ab4-d871-4829-906d-41e23ae3905b`: Tier 4 led by `leo-number-one-guitar-001`; Tier 3 recall 4,933 chars sourced exclusively `qdrant:roads`, containing the Silhouette prose — advisor-attributed prose + fact together.

**(b) Sky DAW/interface query — PASS (the prior failure).** "Which DAW does Leo run and what is his 500-series front end preamp chain?" → package `0d68836f-7c7f-417f-9538-d5f2fd5586a8`: Tier 4 facts #1/#2 = `leo-500-series-chain-001`, `leo-primary-daw-001` — the exact pair the pre-KAI-788 reader starved (builder discovery pkg `d9da9358`). Tier 3 sourced exclusively `qdrant:sky`.

**(c) Shared Bassman fact under BOTH advisors — PASS.** "How does Leo layer the Bassman amp?" → roads package `2d006a85-4229-4e05-b8e7-b07a8271eef5` and sky package `e785842c-87ec-4e6c-9923-af501ce243b5` both return `leo-bassman-layering-001` first (advisor:null global fact, M1.5 semantics).

**(d) Perspective isolation — PASS.** Roads queried WITH Sky's vocabulary ("coaching cheerleader mission for the studio") → package `e8682183-bff2-49c8-9019-f4b2017d1111`: Tier 3 EMPTY — zero Sky coach-mission prose reaches Roads. Sky queried WITH Roads' vocabulary ("thin out the gear he does not need") → package `51f49303-1bde-4767-9b33-9d1b2689ecac`: Tier 3 6,022 chars sourced only `qdrant:sky`, containing NO "thin out" prose — Roads' thin-out perspective absent for Sky.

**(e) Namespace isolation, unseeded third advisor — PASS.** devops queried with combined roads+sky vocabulary ("Ernie Ball Silhouette guitar and the 500-series preamp chain") → package `a9ee6479-daa2-48bb-97b5-45d8368413d8`: Tier 3 EMPTY — none of roads/sky prose. (Tier 4 global facts DO surface for devops — correct by design: `advisor:null` facts are global per M1.5; the isolation claim is about prose namespaces, and it holds.)

## PART 2 — CHECKPOINT A: verified fact beats generic guess

**Path used: `POST /council/message` — the real chat path** (kai-council-api :8002, which assembles via the Memory Service and returns the `package_id` trail). No fallback needed.

**1. CONTROL (memory-off).** Same model Roads uses (`claude-sonnet-4-6`, per `model_config.json` + confirmed in the live reply), called directly through the council container's own Anthropic client with a generic advisor system prompt and NO memory/assembly:
> *"I don't actually have any information about Leo's specific guitars or gear setup… Could you tell me a bit about your main guitar?"*

Forced-guess variant ("if you had to guess the single most likely main guitar"):
> *"I'd guess a **Fender Stratocaster** — it's arguably the most widely used electric guitar…"*

Generic knowledge produces no Silhouette — it produces the predicted Strat skew. The answer cannot come from the base model.

**2. LIVE ANSWER (memory on, real chat path).** `POST /council/message`, channel `roads`, user `checkpoint-a-claude`, question: *"What is Leo's number one guitar, and why is that the one that fits him best?"* — no "Silhouette"/"Ernie Ball"/brand vocabulary anywhere in the prompt. Reply (provider `anthropic`, model `claude-sonnet-4-6`, package `0bb48cce-73f4-40a6-8afa-b00efb7261ee`):

> *"The Ernie Ball Silhouette, Sunburst. That's the one.*
> *Short scale, small frets, comfortable neck radius — it's built like it was made for his hands. With the nerve damage and the surgeries on his neck and both shoulders, fit isn't a preference, it's a requirement. Most guitars make him work around his body. The Silhouette just works. He had a second one, blue, traded it away, and has regretted it ever since…"*

Correct answer AND the reasoning that exists only in seeded content (small hands/short scale/nerve damage/neck + both-shoulder surgeries, the regretted blue-Silhouette trade).

**3. TRAIL.** Assembly log for that exact package `0bb48cce-73f4-40a6-8afa-b00efb7261ee` shows the injected Tier 4 facts:
`['leo-number-one-guitar-001', 'leo-cab-mics-001', 'leo-strat-studio-only-001', 'leo-primary-daw-001', 'leo-500-series-chain-001', 'leo-bassman-layering-001', 'leo-drums-plan-001']`
— `leo-number-one-guitar-001` first. The answer is traceable to the injected verified fact, not luck.

**All three parts hold → CHECKPOINT A: GO.**

## Bounds on the GO (truth-in-labeling, inherited from KAI-788 review C4)

- Recall is proven for literal-vocabulary queries; synonym/paraphrase recall ("axe", "tracking") is NOT claimed — that is KAI-789 (fact-vector index, parked).
- The GO covers the memory chain (seed → registry/prose → assemble → chat answer → trail) on the deployed chat path for Roads and Sky at 29-fact scale.

## Hygiene / DISCOVERED

- Registry SHA byte-identical before/after; no writes, no re-seed, no reader changes, no engine close (D13).
- Discovered (design note, not a defect): global `advisor:null` facts surface in Tier 4 for ALL advisors including devops — correct M1.5 semantics; prose namespaces are the isolation boundary.
- KAI-786 → Done via manual API PATCH + readback on this PASS.
