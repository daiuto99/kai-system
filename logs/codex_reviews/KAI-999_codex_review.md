# Codex Review Log — KAI-999 (mode-lock unlock approval: Slack → Telegram)

Protocol: Claude builds -> Codex verifies (rule #9, `codex exec` cross-provider, codex-cli 0.145.0).
Logged per Leo standing caveat (2026-07-25). Ticket: KAI-999 (950284e0). Session: 2026-07-30.
Scope: the two landed worker-side files (commits e828040, e3d2ab2) — routes/mode_lock.py + kai-scheduler/scheduler.py.
Verify contract: the 5 in-model properties in docs/staged/KAI-999_mode_lock_telegram.md
(secret-leak / auth boundary / allowlist / state machine / callback bounds).
Residual policy: feedback_codex_residual_acceptance — stop once in-model properties hold;
accept out-of-threat-model + caller-misuse + deploy-level residuals.

---

## Round 1 — landed files as-is -> FAIL (1 finding)
KAI-999 VERIFICATION — FAIL
PROP 1 (secret-leak):    HOLDS — type(e).__name__ on network errors; redact(body, token) on rejects (mode_lock.py:407-416,429-441).
PROP 2 (auth):           HOLDS — endpoint not in _NO_AUTH; scheduler forwards with worker_auth() (scheduler.py:188-192).
PROP 3 (allowlist):      HOLDS — chat_id not in allowed => deny; empty allowlist = deny-all (scheduler.py:179-184,132-137).
PROP 4 (state machine):  BROKEN — _decision_render (mode_lock.py:596-608) only keys approved_once/denied/approved_session;
                         _decision_render("consumed"|"pending"|"already_decided", ...) raises KeyError. Mutation/idempotency paths (630-656) hold.
PROP 5 (callback bounds):HOLDS — request_id = 12 ASCII hex; max callback_data 29 bytes (mode_lock.py:475,394-398).
RECOMMENDATION: do NOT — fix first

### Claude reachability analysis (before fixing)
Codex PROP-4 KeyError is UNREACHABLE in the current code, so the in-model property (idempotent, no
double-consume, no state moving backward) actually HOLDS:
 - _apply_decision emits status only from _ACTION_MAP on the fresh path => always one of {approved_once, denied, approved_session} (mode_lock.py:659,620).
 - Both call sites early-return on not-ok / already_decided BEFORE _decision_render:
   telegram_action_internal (748-750) and the dormant Slack callback (685-686).
 => _decision_render never receives an unhandled status in the live flow.
Classified as a LATENT robustness fragility, not a live defect. Fixed anyway (own the risk + mitigate):
a total render function must not be one future refactor away from a 500.

### Fix applied (Claude, non-lock-asset file)
_decision_render made total: header/summary dict lookups changed from `[new_status]` to
`.get(new_status, <generic fallback>)`. Any unexpected status now degrades to a generic
"KAI Mode Lock — Updated" line instead of raising. Direct proof: all 6 statuses
(incl. consumed/pending/already_decided) render without KeyError.
CI gate (scripts/ci.sh) green; kai-worker-api rebuilt + healthy.

## Round 2 — re-verify after fix -> PASS
KAI-999 VERIFICATION — PASS
PROP 1 (secret-leak):    HOLDS
PROP 2 (auth):           HOLDS
PROP 3 (allowlist):      HOLDS
PROP 4 (state machine):  HOLDS — total .get() fallbacks for every status; only pending mutates; terminal re-tap => already_decided; consume only approved_once->consumed.
PROP 5 (callback bounds):HOLDS — 26/26/29 bytes for once/deny/session.
ACCEPTED RESIDUALS:
 - Direct invocation by an already-trusted holder of worker Basic-auth credentials (out-of-threat-model).
 - Caller misuse supplying a non-generated request_id directly to internal helpers.
 - Deploy-level secret config / file permissions / storage durability.
RECOMMENDATION: SHIP (in-model properties hold)

---

## Verdict
PASS at Round 2. Worker-side Telegram mode-lock approval is Codex-verified. One robustness
fix landed on top of the reviewed change (_decision_render totalization). Remaining KAI-999
work is unchanged and unaffected: the gate-hook async install (tamper-locked asset, Leo hand only).
