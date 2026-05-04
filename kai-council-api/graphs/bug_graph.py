import sqlite3
import logging
from pathlib import Path

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from graphs.bug_state import BugState
from graphs.bug_nodes import (
    support_diagnosis, lse_review, architect_review,
    kai_validation, leo_notify,
    peer_review_decision, kai_decision,
)

logger = logging.getLogger(__name__)

BUG_CHECKPOINT_DB = Path("/vault/00_System/bug_workflow_state.db")


def build_bug_graph():
    BUG_CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(BUG_CHECKPOINT_DB), check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    wf = StateGraph(BugState)

    wf.add_node("run_support_diagnosis",  support_diagnosis)
    wf.add_node("run_lse_review",         lse_review)
    wf.add_node("run_architect_review",   architect_review)
    wf.add_node("run_kai_validation",     kai_validation)
    wf.add_node("run_leo_notify",         leo_notify)

    wf.set_entry_point("run_support_diagnosis")
    wf.add_edge("run_support_diagnosis", "run_lse_review")
    wf.add_edge("run_lse_review", "run_architect_review")
    wf.add_conditional_edges("run_architect_review", peer_review_decision, {
        "run_kai_validation":    "run_kai_validation",
        "run_support_diagnosis": "run_support_diagnosis",
    })
    wf.add_conditional_edges("run_kai_validation", kai_decision, {
        "run_leo_notify":        "run_leo_notify",
        "run_support_diagnosis": "run_support_diagnosis",
    })
    wf.add_edge("run_leo_notify", END)

    return wf.compile(checkpointer=checkpointer)


_bug_graph = None


def get_bug_graph():
    global _bug_graph
    if _bug_graph is None:
        _bug_graph = build_bug_graph()
        logger.info("Bug investigation graph compiled")
    return _bug_graph
