"""状态图测试：拓扑完整性、路径可达性、事件契约与可恢复性（《M3 计划》T5）。

拓扑测试的价值在于**它不依赖任何一次具体回答**：节点漏接一条边、
条件边少一个分支、出现孤儿节点，都会在这里直接失败，
而不是等到某次查询"莫名没有回答"才被发现。
"""

from __future__ import annotations

from dataclasses import replace

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import NODE_LABELS, build_graph
from app.agent.pipeline import build_context, respond, stream
from app.config import Settings
from app.llm.fake import FakeLLMClient
from app.retrieval.encoder import HashingEmbedder
from app.store.conversations_repo import ConversationRepo
from app.store.traces_repo import TraceRepo

EXPECTED_NODES = {
    "route",
    "resolve_scope",
    "clarify",
    "create_task",
    "retrieve",
    "grade",
    "refuse",
    "synthesize",
    "verify_evidence",
    "finalize",
}


def make_context(conn, **changes):
    return build_context(conn, replace(Settings.from_env(), **changes))


def test_topology_has_all_nodes_and_no_orphans(conn) -> None:
    graph = build_graph(make_context(conn)).get_graph()
    nodes = set(graph.nodes) - {"__start__", "__end__"}
    assert nodes == EXPECTED_NODES

    touched = set()
    for edge in graph.edges:
        touched.add(edge.source)
        touched.add(edge.target)
    orphans = {node for node in nodes if node not in touched}
    assert not orphans, "孤儿节点会让流程永远走不到：%s" % sorted(orphans)


def test_topology_edges_are_wired_as_documented(conn) -> None:
    graph = build_graph(make_context(conn)).get_graph()
    plain = {(edge.source, edge.target) for edge in graph.edges if not edge.conditional}
    conditional = {(edge.source, edge.target) for edge in graph.edges if edge.conditional}
    assert ("__start__", "route") in plain
    assert ("retrieve", "grade") in plain
    assert ("synthesize", "verify_evidence") in plain
    assert ("verify_evidence", "finalize") in plain
    assert ("finalize", "__end__") in plain

    # 条件边必须覆盖每一条分支：漏一条就等于某类问题没有出口
    assert ("route", "create_task") in conditional
    assert ("route", "clarify") in conditional
    assert ("route", "synthesize") in conditional
    assert ("route", "resolve_scope") in conditional
    assert ("resolve_scope", "clarify") in conditional
    assert ("resolve_scope", "retrieve") in conditional
    assert ("grade", "refuse") in conditional
    assert ("grade", "synthesize") in conditional

    # 澄清 / 拒答 / 建任务也要过证据校验与落库，而不是各自提前结束
    assert ("clarify", "verify_evidence") in plain
    assert ("refuse", "verify_evidence") in plain
    assert ("create_task", "verify_evidence") in plain


def test_every_node_has_a_stage_label() -> None:
    assert EXPECTED_NODES <= set(NODE_LABELS), "节点必须都能翻译成给用户看的阶段名"


def test_graph_runs_content_path_end_to_end(conn) -> None:
    result = respond(
        conn, "谁提到了活动弹窗的问题？", settings=replace(Settings.from_env(), text_encoder="hash")
    )
    names = [step.name for step in result.answer.trace]
    assert names == ["route", "resolve_scope", "retrieve", "grade", "synthesize",
                     "verify_evidence", "persist"]
    assert result.answer.answer_mode == "attribution"
    assert result.answer.citations


def test_graph_refuse_path_skips_synthesize(conn) -> None:
    """拒答链路上，除了路由，一次模型都不该调（这里把路由也固定成规则）。"""
    llm = FakeLLMClient("不该被调用")
    result = respond(
        conn,
        "公司食堂今天中午吃什么？",
        settings=replace(Settings.from_env(), route_mode="rules"),
        llm=llm,
        encoder=HashingEmbedder(dim=32),
    )
    names = [step.name for step in result.answer.trace]
    assert names == ["route", "resolve_scope", "retrieve", "grade", "refuse",
                     "verify_evidence", "persist"]
    assert "synthesize" not in names
    assert result.answer.answer_mode == "refuse"
    assert llm.call_count == 0, "拒答路径一次模型都不该调"


def test_graph_structured_path_skips_retrieval(conn) -> None:
    result = respond(conn, "这三场会有什么风险？", settings=Settings.from_env())
    names = [step.name for step in result.answer.trace]
    assert names == ["route", "synthesize", "verify_evidence", "persist"]


def test_stream_event_contract_matches_m1(conn) -> None:
    """事件名与载荷形状必须与 M1 一致，前端才不会白屏。"""
    events = list(
        stream(conn, "谁提到了活动弹窗的问题？", settings=replace(Settings.from_env(), text_encoder="hash"))
    )
    names = [name for name, _ in events]
    assert names[:3] == ["stage", "stage", "stage"]
    assert names[-1] == "done"
    assert "citation" in names and "delta" in names

    for name, payload in events:
        if name == "stage":
            assert set(payload) == {"node", "label"}
        if name == "citation":
            assert set(payload) >= {"meeting_id", "segment_id", "label", "start"}
    done = dict(events)["done"]
    assert done.conversation_id.startswith("c_")
    assert done.answer.citations


def test_run_writes_chat_trace_with_usage(conn) -> None:
    settings = replace(Settings.from_env(), text_encoder="hash")
    respond(conn, "谁提到了活动弹窗的问题？", settings=settings)
    traces = TraceRepo(conn).list_recent(limit=5)
    assert traces, "每轮回答都要落一条 trace（成本与评测的数据源）"
    latest = traces[0]
    assert latest.answer_mode == "attribution"
    assert latest.route_source == "rules"
    assert [node["name"] for node in latest.nodes][-1] == "persist"


def test_graph_accepts_checkpointer(conn) -> None:
    """M4 会接持久化 checkpointer；M3 先用 InMemorySaver 证明接口留得下来。"""
    saver = InMemorySaver()
    graph = build_graph(make_context(conn), checkpointer=saver)
    config = {"configurable": {"thread_id": "c_test_thread"}}
    graph.invoke(
        {
            "user_input": "谁提到了活动弹窗的问题？",
            "conversation_id": "c_test_thread",
            "attachments": [],
            "trace": [],
        },
        config=config,
    )
    snapshot = graph.get_state(config)
    assert snapshot.values.get("answer") is not None, "带 checkpointer 时状态要能读回来"


def test_answer_is_persisted_with_scope_marker(conn) -> None:
    settings = replace(Settings.from_env(), text_encoder="hash")
    first = respond(conn, "官网改版周会定了什么？", settings=settings, active_meeting_id="m_20260911")
    conversation = ConversationRepo(conn).get(first.conversation_id)
    assert conversation is not None
    assert "[scope=m_20260911]" in conversation.messages[1].content
