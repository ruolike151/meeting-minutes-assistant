"""节点级测试：不装 LangGraph 也能测每个节点（《M3 计划》T4）。

这一层守着两条纪律：

* 节点是纯函数，输入 state 与上下文，输出要合并进状态的字段；
* **拒答与澄清路径一次模型都不调**——"会不会编造"不取决于模型的自觉。
"""

from __future__ import annotations

from dataclasses import replace

from app.agent.nodes import (
    NodeContext,
    clarify_node,
    create_task_node,
    grade_node,
    refuse_node,
    resolve_scope_node,
    retrieve_node,
    route_node,
    synthesize_node,
    verify_evidence_node,
)
from app.agent.pipeline import build_context
from app.config import Settings
from app.llm.client import ChatMessage
from app.llm.fake import FakeLLMClient
from app.retrieval.encoder import HashingEmbedder
from app.retrieval.index import ensure_meeting_index
from app.store.repos import SegmentRepo


def make_context(conn, *, llm=None, encoder=None, **settings_changes) -> NodeContext:
    settings = replace(Settings.from_env(), **settings_changes)
    context = NodeContext.from_settings(conn, settings, llm=llm, encoder=encoder)
    return context


def test_route_node_uses_rules_without_model(conn) -> None:
    context = make_context(conn, llm=FakeLLMClient(available=False))
    update = route_node({"user_input": "这三场会有什么风险？"}, context)
    assert update["intent"] == "query_content"
    assert update["answer_mode"] == "risks"
    assert update["route_source"] == "rules"
    assert update["scope"]["meeting_ids"], "规则路由要给出范围"


def test_route_node_prefers_model_then_falls_back(conn) -> None:
    """模型给了合法结论就用它；schema 不合法就回落规则（约束 C1 不回退）。"""
    good = FakeLLMClient('{"intent": "query_speaker", "answer_mode": "speaker_tasks",'
                         ' "speaker_id": "p_wang", "reason": "问的是王强"}')
    context = make_context(conn, llm=good, route_mode="auto")
    update = route_node({"user_input": "王强答应了什么？"}, context)
    assert update["route_source"] == "llm"
    assert update["answer_mode"] == "speaker_tasks"
    assert update["speaker_id"] == "p_wang"

    broken = FakeLLMClient("这不是 JSON")
    context = make_context(conn, llm=broken, route_mode="auto")
    update = route_node({"user_input": "王强答应了什么？"}, context)
    assert update["route_source"] == "rules"
    assert update["answer_mode"] == "speaker_tasks"


def test_route_node_filters_fabricated_meeting_ids(conn) -> None:
    """模型不能凭空指定会议范围：不存在的 ID 会被丢掉并记账。"""
    llm = FakeLLMClient(
        '{"intent": "query_content", "answer_mode": "decisions",'
        ' "meeting_ids": ["m_不存在", "m_20260911"], "reason": "点名了周会"}'
    )
    context = make_context(conn, llm=llm)
    update = route_node({"user_input": "官网改版周会定了什么？"}, context)
    assert update["scope"]["meeting_ids"] == ["m_20260911"]
    assert any("不存在的会议 ID" in note for note in update["route_rejected"])


def test_route_node_rejects_unknown_from_model(conn) -> None:
    llm = FakeLLMClient('{"intent": "unknown", "answer_mode": "refuse"}')
    context = make_context(conn, llm=llm)
    update = route_node({"user_input": "这三场会有什么风险？"}, context)
    assert update["route_source"] == "rules", "unknown 要回落规则，不能直接拒答"


def test_route_mode_rules_ignores_model(conn) -> None:
    llm = FakeLLMClient('{"intent": "query_speaker", "answer_mode": "speaker_tasks"}')
    context = make_context(conn, llm=llm, route_mode="rules")
    route_node({"user_input": "这三场会有什么风险？"}, context)
    assert llm.call_count == 0, "rules 模式不该调用模型"


def test_resolve_scope_inherits_from_history(conn) -> None:
    context = make_context(conn)
    context.history = [
        ChatMessage(role="assistant", content="结论如下……[scope=m_20260911]"),
        ChatMessage(role="user", content="那还有什么风险？"),
    ]
    update = resolve_scope_node(
        {"scope": {"meeting_ids": ["m_20260911", "m_20260909", "m_20260904"], "narrowed": False,
                   "ambiguous": False, "source": "library"}},
        context,
    )
    assert update["scope"]["meeting_ids"] == ["m_20260911"]
    assert update["scope"]["source"] == "history"


def test_resolve_scope_keeps_explicit_scope(conn) -> None:
    context = make_context(conn)
    context.history = [ChatMessage(role="assistant", content="[scope=m_20260909]")]
    update = resolve_scope_node(
        {"scope": {"meeting_ids": ["m_20260911"], "narrowed": True, "source": "explicit"}},
        context,
    )
    assert update["scope"]["meeting_ids"] == ["m_20260911"]


def test_retrieve_and_grade_nodes(conn) -> None:
    encoder = HashingEmbedder(dim=64)
    ensure_meeting_index(conn, encoder, "m_20260911")
    context = make_context(conn, encoder=encoder)
    scope = {"meeting_ids": ["m_20260911"], "narrowed": True, "source": "explicit"}

    retrieved = retrieve_node({"user_input": "谁提到了活动弹窗的问题？", "scope": scope}, context)
    assert retrieved["retrieval"].refused is False
    assert retrieved["evidence"]
    assert retrieved["encoder"].startswith("hash-bigram-v1")

    graded = grade_node({"retrieval": retrieved["retrieval"]}, context)
    assert graded["enough_evidence"] is True
    assert graded["grade_reason"] == ""


def test_retrieve_node_parses_time_range(conn) -> None:
    context = make_context(conn)
    update = retrieve_node(
        {
            "user_input": "上周活动弹窗的问题",
            "scope": {"meeting_ids": ["m_20260911", "m_20260909"], "narrowed": True},
        },
        context,
    )
    # 2026-09-12 的上周是 08-31 ~ 09-06，种子会议都在 09 月，因此检索不到
    assert update["retrieval"].refused is True


def test_refuse_and_clarify_never_call_model(conn) -> None:
    llm = FakeLLMClient('{"intent": "query_content", "answer_mode": "content"}')
    context = make_context(conn, llm=llm)
    refuse_node(
        {"user_input": "公司食堂今天中午吃什么？", "scope": {"meeting_ids": ["m_20260911"]}},
        context,
    )
    clarify_node({"scope": {"meeting_ids": ["m_20260911"]}}, context)
    assert llm.call_count == 0, "拒答与澄清都不该调用模型"


def test_create_task_node_without_attachment_gives_guidance(conn) -> None:
    context = make_context(conn)
    update = create_task_node({"user_input": "上传", "attachments": []}, context)
    assert update["answer_mode"] == "process_result"
    assert "拖进输入框" in update["answer_draft"].lead


def test_synthesize_uses_template_when_model_unavailable(conn) -> None:
    llm = FakeLLMClient(available=False)
    context = make_context(conn, llm=llm)
    retrieved = retrieve_node(
        {
            "user_input": "谁提到了活动弹窗的问题？",
            "scope": {"meeting_ids": ["m_20260911"], "narrowed": True},
        },
        context,
    )
    update = synthesize_node(
        {
            "user_input": "谁提到了活动弹窗的问题？",
            "answer_mode": "attribution",
            "scope": {"meeting_ids": ["m_20260911"], "narrowed": True},
            **retrieved,
        },
        context,
    )
    assert update["synthesis_source"] == "template"
    assert update["answer_draft"].blocks, "模板兜底也要给出带引用的回答"
    assert llm.call_count == 0


def test_synthesize_uses_llm_and_filters_foreign_citations(conn) -> None:
    """模型只能引用本次检索命中的片段：越界引用被丢弃并记账。"""
    target = SegmentRepo(conn).list_for_meeting("m_20260911")[1]
    good = '{"lead": "李明在周会上提到了这个风险。", "items": [{"title": "出处", "text": "…",' \
           ' "evidence": ["m_20260911:%s", "m_20260911:s999"]}]}' % target.id
    llm = FakeLLMClient(good)
    context = make_context(conn, llm=llm)
    retrieved = retrieve_node(
        {
            "user_input": "谁提到了活动弹窗的问题？",
            "scope": {"meeting_ids": ["m_20260911"], "narrowed": True},
        },
        context,
    )
    update = synthesize_node(
        {
            "user_input": "谁提到了活动弹窗的问题？",
            "answer_mode": "attribution",
            "scope": {"meeting_ids": ["m_20260911"], "narrowed": True},
            **retrieved,
        },
        context,
    )
    assert update["synthesis_source"] == "llm"
    assert update["dropped_citations"] == 1
    item = update["answer_draft"].blocks[0].items[0]
    assert [citation.segment_id for citation in item.citations] == [target.id]


def test_synthesize_marks_items_without_evidence(conn) -> None:
    llm = FakeLLMClient('{"lead": "结论", "items": [{"text": "无出处的结论", "evidence": []}]}')
    context = make_context(conn, llm=llm)
    retrieved = retrieve_node(
        {
            "user_input": "谁提到了活动弹窗的问题？",
            "scope": {"meeting_ids": ["m_20260911"], "narrowed": True},
        },
        context,
    )
    update = synthesize_node(
        {
            "user_input": "谁提到了活动弹窗的问题？",
            "answer_mode": "attribution",
            "scope": {"meeting_ids": ["m_20260911"], "narrowed": True},
            **retrieved,
        },
        context,
    )
    assert update["answer_draft"].blocks[0].items[0].warn is True


def test_structured_question_skips_model(conn) -> None:
    """结构化问题读纪要即可，不该让模型参与（省一次调用，也少一分不确定）。"""
    llm = FakeLLMClient("不该被调用")
    context = make_context(conn, llm=llm)
    update = synthesize_node(
        {
            "user_input": "这三场会有什么风险？",
            "answer_mode": "risks",
            "scope": {"meeting_ids": ["m_20260911", "m_20260909", "m_20260904"]},
        },
        context,
    )
    assert update["synthesis_source"] == "template"
    assert update["answer_draft"].blocks
    assert llm.call_count == 0


def test_verify_node_drops_unknown_citation(conn) -> None:
    from app.agent.answers import Draft, quote_block
    from app.domain.models import AnswerItem, Citation

    context = make_context(conn)
    fabricated = Citation(
        meeting_id="m_20260911",
        meeting_title="官网改版周会",
        segment_id="s999",
        speaker="李明",
        start=1,
        label="李明 00:01",
    )
    update = verify_evidence_node(
        {
            "scope": {"meeting_ids": ["m_20260911"]},
            "answer_draft": Draft(
                lead="测试", blocks=[quote_block("块", [AnswerItem(text="结论", citations=[fabricated])])]
            ),
        },
        context,
    )
    assert update["dropped_citations"] == 1
    assert update["unsupported_items"] == 1
    assert update["answer_draft"].blocks[0].items[0].warn is True
