from typing import TypedDict, Optional  # noqa: F401


class BugState(TypedDict):
    # Bug metadata
    issue_id:        str
    issue_name:      str
    issue_description: str
    project_name:    str
    priority:        str

    # Support Engineer investigation
    diagnosis:       str
    proposed_fix:    str
    confidence:      str   # High / Medium / Low
    iteration:       int   # retry counter (max 2)
    prior_feedback:  str   # LSE/Architect feedback on prior iteration

    # Peer review
    lse_review:      str
    lse_approved:    bool
    architect_review: str
    architect_approved: bool

    # KAI validation
    kai_assessment:  str
    kai_approved:    bool
    kai_return_notes: str

    # Routing
    bug_routing:     str   # dev / devops / creative / kai
    risk_level:      str   # low / high

    # Status
    status:          str   # diagnosing / peer_review / kai_validation / awaiting_leo / done
    slack_thread_ts: str   # root Slack message ts for threading
    audit_log:       list
