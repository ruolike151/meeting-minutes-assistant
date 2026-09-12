"""检索效果评测（《技术方案》9.3、《M3 计划》T8）。

评测与单测分工明确：单测保证"不坏"，评测回答"好不好"。
这里提供三件东西：

* `default_queries()` —— 固定标注查询集：14 条域内问题（带金标片段）+ 8 条域外问题；
* `evaluate()` —— Recall@k、MRR、拒答正确率，以及每条查询的逐条明细；
* `sweep()` —— 按网格扫参（K1/K2/RRF k/top-k/阈值），输出按召回排序的配置列表。

参数不拍脑袋：初始值来自《技术方案》9.1，最终值以这份评测的实测为准，
改参数必须附前后对比。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from itertools import product
from typing import Sequence

from app.config import RetrievalSettings
from app.retrieval.encoder import TextEncoder
from app.retrieval.pipeline import retrieve


@dataclass(frozen=True)
class EvalQuery:
    query: str
    gold: tuple[str, ...] = ()      # 形如 "m_20260911:s2"
    note: str = ""

    @property
    def answerable(self) -> bool:
        return bool(self.gold)


@dataclass
class QueryOutcome:
    query: str
    answerable: bool
    refused: bool
    rank: int | None            # 第一个金标命中的名次（1 起），没命中为 None
    recalled: bool
    matched: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    encoder: str
    top_k: int
    config: RetrievalSettings
    outcomes: list[QueryOutcome] = field(default_factory=list)

    @property
    def answerable(self) -> list[QueryOutcome]:
        return [item for item in self.outcomes if item.answerable]

    @property
    def unanswerable(self) -> list[QueryOutcome]:
        return [item for item in self.outcomes if not item.answerable]

    @property
    def recall(self) -> float:
        items = self.answerable
        if not items:
            return 0.0
        return round(sum(1 for item in items if item.recalled) / len(items), 4)

    @property
    def mrr(self) -> float:
        items = self.answerable
        if not items:
            return 0.0
        total = sum(1.0 / item.rank for item in items if item.rank)
        return round(total / len(items), 4)

    @property
    def refuse_accuracy(self) -> float:
        """域外问题的拒答正确率：本项目最核心的质量指标之一（不编造）。"""
        items = self.unanswerable
        if not items:
            return 1.0
        return round(sum(1 for item in items if item.refused) / len(items), 4)

    @property
    def answer_rate(self) -> float:
        """域内问题的作答率：这是召回的另一面（召回不到就只能拒答）。"""
        items = self.answerable
        if not items:
            return 0.0
        return round(sum(1 for item in items if not item.refused) / len(items), 4)

    def to_dict(self) -> dict:
        return {
            "encoder": self.encoder,
            "top_k": self.top_k,
            "config": {
                "recall_k1": self.config.recall_k1,
                "recall_k2": self.config.recall_k2,
                "rrf_k": self.config.rrf_k,
                "top_k": self.config.top_k,
                "min_coverage": self.config.min_coverage,
                "min_cosine": self.config.min_cosine,
            },
            "metrics": {
                "recall_at_k": self.recall,
                "mrr": self.mrr,
                "refuse_accuracy": self.refuse_accuracy,
                "answer_rate": self.answer_rate,
            },
            "outcomes": [
                {
                    "query": item.query,
                    "answerable": item.answerable,
                    "refused": item.refused,
                    "rank": item.rank,
                    "recalled": item.recalled,
                    "matched": item.matched,
                }
                for item in self.outcomes
            ],
        }


def default_queries() -> list[EvalQuery]:
    """固定标注集：金标来自种子会议的逐段正文（人工核对过）。"""
    return [
        EvalQuery("首页入口太深的问题", ("m_20260911:s2",), "用户反馈主题"),
        EvalQuery("活动弹窗的点击转化率是多少", ("m_20260911:s2",)),
        EvalQuery("设计稿什么时候交付", ("m_20260911:s3", "m_20260911:s4")),
        EvalQuery("开发需要几个工作日", ("m_20260911:s5", "m_20260911:s6")),
        EvalQuery("埋点口径要跟谁对齐", ("m_20260911:s7", "m_20260911:s9")),
        EvalQuery("这次改版有什么风险", ("m_20260911:s10",)),
        EvalQuery("退款之后积分怎么处理", ("m_20260909:s2", "m_20260909:s3")),
        EvalQuery("积分被用户花掉了怎么办", ("m_20260909:s4", "m_20260909:s5")),
        EvalQuery("等级规则按什么周期算", ("m_20260909:s6", "m_20260909:s7", "m_20260909:s8")),
        EvalQuery("积分流水表要几天", ("m_20260909:s9",)),
        EvalQuery("冷启动中位数是多少秒", ("m_20260904:s2", "m_20260904:s3")),
        EvalQuery("首屏为什么慢", ("m_20260904:s5",)),
        EvalQuery("优化下来能省多少时间", ("m_20260904:s7",)),
        EvalQuery("老机型兼容测试由谁来做", ("m_20260904:s10",)),
        # -- 域外：必须拒答，不能编造（产品文档 9 节的核心指标）--
        EvalQuery("公司食堂今天中午吃什么？"),
        EvalQuery("明天北京天气怎么样"),
        EvalQuery("帮我订一张去上海的机票"),
        EvalQuery("上个月的差旅费怎么报销"),
        EvalQuery("年假怎么申请"),
        EvalQuery("附近有什么好吃的餐厅"),
        EvalQuery("公司班车几点发车"),
        EvalQuery("帮我写一首关于秋天的诗"),
    ]


def evaluate(
    conn: sqlite3.Connection,
    queries: Sequence[EvalQuery],
    *,
    encoder: TextEncoder | None = None,
    config: RetrievalSettings | None = None,
    top_k: int | None = None,
) -> EvalReport:
    """跑一遍标注集，返回指标与逐条明细。"""
    settings = config or RetrievalSettings()
    k = int(top_k or settings.top_k)
    report = EvalReport(
        encoder=encoder.storage_key if encoder is not None else "keyword-only",
        top_k=k,
        config=settings,
    )
    for item in queries:
        result = retrieve(conn, item.query, encoder=encoder, config=settings)
        ranked = ["%s:%s" % key for key in result.ranked_keys[:k]]
        gold = set(item.gold)
        rank = next((index + 1 for index, key in enumerate(ranked) if key in gold), None)
        report.outcomes.append(
            QueryOutcome(
                query=item.query,
                answerable=item.answerable,
                refused=bool(result.refused),
                rank=rank,
                recalled=bool(gold) and rank is not None,
                matched=ranked,
            )
        )
    return report


def sweep(
    conn: sqlite3.Connection,
    queries: Sequence[EvalQuery] | None = None,
    *,
    encoder: TextEncoder | None = None,
    base: RetrievalSettings | None = None,
    rrf_ks: Sequence[int] = (10, 30, 60),
    min_cosines: Sequence[float] = (0.2, 0.35, 0.5),
    top_ks: Sequence[int] = (4, 8),
    min_coverages: Sequence[float] = (0.2, 0.34, 0.5),
) -> list[EvalReport]:
    """扫参：返回按 (召回, MRR, 拒答正确率) 排序的报告列表。"""
    items = list(queries or default_queries())
    base_config = base or RetrievalSettings()
    reports: list[EvalReport] = []
    for rrf_k, min_cosine, top_k, min_coverage in product(
        rrf_ks, min_cosines, top_ks, min_coverages
    ):
        config = base_config.replaced(
            rrf_k=rrf_k,
            min_cosine=min_cosine,
            top_k=top_k,
            min_coverage=min_coverage,
        )
        reports.append(evaluate(conn, items, encoder=encoder, config=config, top_k=top_k))
    return sorted(
        reports,
        key=lambda report: (-report.recall, -report.mrr, -report.refuse_accuracy, report.top_k),
    )


def summarize(report: EvalReport) -> str:
    """一行摘要，便于脚本里打表对比。"""
    config = report.config
    return (
        "encoder=%-22s top_k=%d rrf_k=%-3d min_cos=%.2f min_cov=%.2f "
        "| recall@k=%.3f mrr=%.3f 拒答=%.3f 作答率=%.3f"
        % (
            report.encoder,
            report.top_k,
            config.rrf_k,
            config.min_cosine,
            config.min_coverage,
            report.recall,
            report.mrr,
            report.refuse_accuracy,
            report.answer_rate,
        )
    )


__all__ = [
    "EvalQuery",
    "EvalReport",
    "QueryOutcome",
    "default_queries",
    "evaluate",
    "summarize",
    "sweep",
]
