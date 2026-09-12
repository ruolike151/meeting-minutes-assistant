"""每轮回答的 trace 落库（M3）。

它是三件事的共同数据源：

* 调试：这次回答走了哪些节点、各花了多久；
* 成本：这一轮用了多少 input/output token、折多少钱；
* 评测：意图路由与拒答率的分母（M5 的看板直接读这张表）。

Langfuse 是 M5 的事（决策 ⑨）：M3 先把数据落到自己库里，不引入外部服务。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Sequence

from app.domain.models import TraceStep
from app.store.repos import iso, new_id


@dataclass(frozen=True)
class ChatTrace:
    id: str
    conversation_id: str | None
    intent: str
    answer_mode: str
    route_source: str
    nodes: list[dict] = field(default_factory=list)
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    # M5：单轮延迟（节点耗时之和）与所属会议，用于 P95 与单场成本
    latency_ms: int = 0
    meeting_id: str | None = None
    cached: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class TraceRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(
        self,
        *,
        conversation_id: str | None,
        intent: str,
        answer_mode: str,
        route_source: str = "",
        nodes: Sequence[TraceStep] = (),
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: float = 0.0,
        latency_ms: int = 0,
        meeting_id: str | None = None,
        cached: bool = False,
    ) -> ChatTrace:
        trace = ChatTrace(
            id=new_id("t"),
            conversation_id=conversation_id,
            intent=intent,
            answer_mode=answer_mode,
            route_source=route_source,
            nodes=[step.model_dump(mode="json") for step in nodes],
            model=model,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            cost=float(cost),
            latency_ms=int(latency_ms),
            meeting_id=meeting_id,
            cached=bool(cached),
        )
        self.conn.execute(
            "INSERT INTO chat_traces(id, conversation_id, intent, answer_mode, route_source,"
            " nodes, model, input_tokens, output_tokens, cost, latency_ms, meeting_id,"
            " cached, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trace.id,
                trace.conversation_id,
                trace.intent,
                trace.answer_mode,
                trace.route_source,
                json.dumps(trace.nodes, ensure_ascii=False),
                trace.model,
                trace.input_tokens,
                trace.output_tokens,
                trace.cost,
                trace.latency_ms,
                trace.meeting_id,
                1 if trace.cached else 0,
                iso(),
            ),
        )
        self.conn.commit()
        return trace

    def get(self, trace_id: str) -> ChatTrace | None:
        row = self.conn.execute("SELECT * FROM chat_traces WHERE id = ?", (trace_id,)).fetchone()
        return _to_trace(row) if row else None

    def list_recent(self, *, limit: int = 20) -> list[ChatTrace]:
        rows = self.conn.execute(
            "SELECT * FROM chat_traces ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [_to_trace(row) for row in rows]

    def total_cost(self) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(cost), 0) AS total FROM chat_traces").fetchone()
        return float(row["total"]) if row else 0.0

    def list_by_meeting(self, meeting_id: str, *, limit: int = 200) -> list[ChatTrace]:
        """某场会议相关的问答 trace（单场会议成本的分子）。"""
        rows = self.conn.execute(
            "SELECT * FROM chat_traces WHERE meeting_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (meeting_id, int(limit)),
        ).fetchall()
        return [_to_trace(row) for row in rows]


def _to_trace(row: sqlite3.Row) -> ChatTrace:
    keys = row.keys()
    return ChatTrace(
        id=row["id"],
        conversation_id=row["conversation_id"],
        intent=row["intent"],
        answer_mode=row["answer_mode"],
        route_source=row["route_source"] or "",
        nodes=json.loads(row["nodes"] or "[]"),
        model=row["model"],
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        cost=float(row["cost"]),
        latency_ms=int(row["latency_ms"] or 0) if "latency_ms" in keys else 0,
        meeting_id=row["meeting_id"] if "meeting_id" in keys else None,
        cached=bool(row["cached"]) if "cached" in keys else False,
    )
