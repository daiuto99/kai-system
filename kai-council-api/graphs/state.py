from typing import TypedDict


class KAIState(TypedDict):
    # ── Input ──────────────────────────────────────────────
    channel: str
    message: str
    user_id: str
    thread_ts: str
    attachments: list
    privacy_mode: bool
    history: list          # prior messages [{"role":…,"content":…}]

    # ── Routing (set by channel_router) ────────────────────
    target_advisor: str
    routing_reason: str

    # ── Execution (set by advisor_node) ────────────────────
    advisor_reply: str
    final_reply: str
    model_used: str
    input_tokens: int
    output_tokens: int

    # ── Audit ──────────────────────────────────────────────
    audit_log: list        # [{ts, node, action, …}]
