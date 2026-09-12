"""时间表达式解析测试（《技术方案》9.2）。"""

from __future__ import annotations

from datetime import date

import pytest

from app.retrieval.timefilter import parse_time_range

# 2026-09-12 是周六：本周 09-07 ~ 09-13，上周 08-31 ~ 09-06，下周 09-14 ~ 09-20
ANCHOR = date(2026, 9, 12)


def parse(text: str):
    return parse_time_range(text, today=ANCHOR)


def test_anchor_is_saturday() -> None:
    assert ANCHOR.weekday() == 5


def test_this_week_last_week_next_week() -> None:
    assert parse("这周开了哪些会") == (date(2026, 9, 7), date(2026, 9, 13))
    assert parse("上周的评审怎么样") == (date(2026, 8, 31), date(2026, 9, 6))
    assert parse("下周要注意什么") == (date(2026, 9, 14), date(2026, 9, 20))


def test_week_expression_wins_over_weekday() -> None:
    """先宽泛后具体：「这周三」按整周解析（超集），宁可多召回不漏掉。"""
    assert parse("这周三有什么安排") == (date(2026, 9, 7), date(2026, 9, 13))
    assert parse("周三的评审") == (date(2026, 9, 9), date(2026, 9, 9))


def test_today_and_yesterday() -> None:
    assert parse("今天中午吃什么") == (ANCHOR, ANCHOR)
    assert parse("昨天定了什么") == (date(2026, 9, 11), date(2026, 9, 11))


def test_recent_days() -> None:
    assert parse("最近3天有什么结论") == (date(2026, 9, 10), ANCHOR)


def test_month_in_digits_and_chinese() -> None:
    assert parse("三月份定了什么") == (date(2026, 3, 1), date(2026, 3, 31))
    assert parse("12月的事") == (date(2026, 12, 1), date(2026, 12, 31))


def test_unparsable_returns_none() -> None:
    """解析不到就不加过滤条件，绝不猜一个时间段出来。"""
    assert parse("公司食堂今天中午吃什么") is not None  # 「今天」是真实的过滤条件
    assert parse("这笔预算怎么算的") is None
    assert parse("13月份") is None


@pytest.mark.parametrize("text", ["", "随便聊聊"])
def test_empty_or_plain_text(text: str) -> None:
    assert parse(text) is None
