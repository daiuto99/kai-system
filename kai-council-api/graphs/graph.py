import sqlite3
import logging
from pathlib import Path

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from graphs.state import KAIState
from graphs.nodes import channel_router, advisor_node

logger = logging.getLogger(__name__)

CHECKPOINT_DB = Path("/vault/00_System/orchestration_state.db")


def build_graph():
    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    workflow = StateGraph(KAIState)
    workflow.add_node("channel_router", channel_router)
    workflow.add_node("advisor_node", advisor_node)

    workflow.set_entry_point("channel_router")
    workflow.add_edge("channel_router", "advisor_node")
    workflow.add_edge("advisor_node", END)

    return workflow.compile(checkpointer=checkpointer)


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
        logger.info("LangGraph compiled — checkpoint: %s", CHECKPOINT_DB)
    return _graph
