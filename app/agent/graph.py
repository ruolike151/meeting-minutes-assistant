"""LangGraph 状态图装配（《技术方案》7.2）。

**全项目唯一 import langgraph 的地方**（与 `perception/transcribe.py` 是唯一
import faster-whisper 的地方同一条纪律）。节点本身是 `nodes/` 里的纯函数，
这里只负责"什么时候调用谁"，所以换掉框架不会波及节点与测试。

图结构：

```text
START → route
  ├─ process_audio → create_task → END
  ├─ unknown → clarify → END
  ├─ 结构化问题（决议/待办/风险/未决/发言人）→ synthesize → verify_evidence → finalize → END
  └─ 内容与归属问题 → resolve_scope
        ├─(范围不唯一)→ clarify → END
        └─(已确定)→ retrieve → grade
              ├─(证据不足)→ refuse → END
              └─(证据达标)→ synthesize → verify_evidence → finalize → END
```

`refuse` 与 `clarify` 都是终止节点且**不调用模型**：拒答先于生成，
"会不会编造"不取决于模型的自觉（《技术方案》7.4）。

checkpointer 在 M4 接（人在回路的挂起与恢复要用到）；M3 的 `build_graph`
已经把它做成可选参数，测试里用 `InMemorySaver` 就能断言图的可恢复性。
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    NodeContext,
    clarify_node,
    create_task_node,
    finalize_node,
    grade_node,
    refuse_node,
    resolve_scope_node,
    retrieve_node,
    route_node,
    synthesize_node,
    verify_evidence_node,
)
from app.agent.state import AgentState

# 阶段名 → 前端显示文案（SSE 的 stage 事件用它）
NODE_LABELS: dict[str, str] = {
    "route": "理解意图与查询范围",
    "resolve_scope": "确定会议范围",
    "clarify": "确认你指的是哪场会议",
    "create_task": "创建音频处理任务",
    "retrieve": "检索相关片段",
    "grade": "判断证据是否充分",
    "synthesize": "组织回答",
    "verify_evidence": "校验每条结论的证据",
    "refuse": "确认没有可支撑的记录",
    "finalize": "写入会话记录",
}

# 结构化问题：直接读纪要，不需要检索与模型
STRUCTURED_MODES = (
    "decisions",
    "actions",
    "risks",
    "open_questions",
    "speaker_tasks",
    "speaker_speech",
)


def build_graph(context: NodeContext, *, checkpointer: Any | None = None):
    """按上下文装配并编译状态图。

    节点是 `(state, context)` 形式的纯函数，这里用闭包把上下文绑进去——
    图因此不需要把数据库连接塞进状态里（状态只放业务数据，方便落盘与断点恢复）。
    """
    builder = StateGraph(AgentState)

    def bind(node: Callable[[AgentState, NodeContext], dict]) -> Callable[[AgentState], dict]:
        def run(state: AgentState) -> dict:
            return node(state, context)

        run.__name__ = node.__name__
        return run

    builder.add_node("route", bind(route_node))
    builder.add_node("resolve_scope", bind(resolve_scope_node))
    builder.add_node("clarify", bind(clarify_node))
    builder.add_node("create_task", bind(create_task_node))
    builder.add_node("retrieve", bind(retrieve_node))
    builder.add_node("grade", bind(grade_node))
    builder.add_node("refuse", bind(refuse_node))
    builder.add_node("synthesize", bind(synthesize_node))
    builder.add_node("verify_evidence", bind(verify_evidence_node))
    builder.add_node("finalize", bind(finalize_node))

    builder.add_edge(START, "route")
    builder.add_conditional_edges("route", _after_route, ROUTE_ROUTES)
    builder.add_edge("retrieve", "grade")
    builder.add_conditional_edges(
        "resolve_scope",
        _after_resolve_scope,
        {"clarify": "clarify", "retrieve": "retrieve"},
    )
    builder.add_conditional_edges(
        "grade", _after_grade, {"refuse": "refuse", "synthesize": "synthesize"}
    )
    builder.add_edge("synthesize", "verify_evidence")
    builder.add_edge("verify_evidence", "finalize")
    builder.add_edge("finalize", END)
    # 澄清 / 拒答 / 建任务同样要过一遍证据校验与落库：
    # 这样"每条结论都经过校验"是图结构保证的，而不是各分支各自记得做
    builder.add_edge("clarify", "verify_evidence")
    builder.add_edge("refuse", "verify_evidence")
    builder.add_edge("create_task", "verify_evidence")

    return builder.compile(checkpointer=checkpointer)


ROUTE_ROUTES = {
    "create_task": "create_task",
    "clarify": "clarify",
    "structured": "synthesize",
    "retrieve": "resolve_scope",
}


def _after_route(state: AgentState) -> str:
    if state.get("intent") == "process_audio":
        return "create_task"
    mode = state.get("answer_mode") or "content"
    if mode == "clarify":
        return "clarify"
    if mode == "refuse":
        return "refuse"
    if mode in STRUCTURED_MODES:
        return "structured"
    return "retrieve"


def _after_resolve_scope(state: AgentState) -> str:
    scope = state.get("scope") or {}
    if scope.get("ambiguous"):
        return "clarify"
    return "retrieve"


def _after_grade(state: AgentState) -> str:
    return "synthesize" if state.get("enough_evidence") else "refuse"


__all__ = ["NODE_LABELS", "ROUTE_ROUTES", "STRUCTURED_MODES", "build_graph"]
