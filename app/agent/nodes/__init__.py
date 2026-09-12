"""编排节点（每个节点都是不依赖 LangGraph 的纯函数）。

分层纪律：`app/agent/graph.py` 是唯一 import langgraph 的地方，
这里的节点只吃 `(state, context)` 并返回"要合并进状态的字段"。
因此节点级测试不需要装框架，替换或移除框架也不会波及这一层。
"""

from app.agent.nodes.answer_nodes import finalize_node, synthesize_node, verify_evidence_node
from app.agent.nodes.context import NodeContext, append_trace, scope_meetings
from app.agent.nodes.retrieval_nodes import grade_node, refuse_node, retrieve_node
from app.agent.nodes.routing_nodes import (
    clarify_node,
    create_task_node,
    resolve_scope_node,
    route_node,
)

__all__ = [
    "NodeContext",
    "append_trace",
    "scope_meetings",
    "route_node",
    "resolve_scope_node",
    "clarify_node",
    "create_task_node",
    "retrieve_node",
    "grade_node",
    "refuse_node",
    "synthesize_node",
    "verify_evidence_node",
    "finalize_node",
]
