"""时间表达式 → 日期区间（《技术方案》9.2「时间表达式单独处理」）。

"上周""三月份"这类表达先解析成日期区间，再作为过滤条件下推到 SQL，
而不是交给向量检索去猜——后者会把"上周"当成一个普通词，既召回不准也没法解释。

优先级是**先宽泛后具体**：「上周 / 这周 / 下周」按整周解析，
单独的「周三」才解析成具体某天。整周是具体某天的超集，宁可多召回几条，
也不要因为猜错一天而把真正的那场会过滤掉。

解析失败**返回 None 而不是猜**：拿不准就不加过滤条件，让检索层照常工作，
而不是悄悄把范围缩小到错误的时间段。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

WEEK_CHARS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

_LAST_WEEK = re.compile(r"(上|前)(个)?(周|星期|礼拜)")
_THIS_WEEK = re.compile(r"(这|本)(个)?(周|星期|礼拜)")
_NEXT_WEEK = re.compile(r"(下)(个)?(周|星期|礼拜)")
_WEEKDAY = re.compile(r"(?:这|本)?(?:周|星期|礼拜)([一二三四五六日天])")
_MONTH = re.compile(r"(\d{1,2})\s*月(份)?")
_MONTH_CN = re.compile(r"([一二三四五六七八九十]{1,3})\s*月(份)?")
_RECENT_DAYS = re.compile(r"(最近|近)(\d{1,3})\s*天")
_TODAY = re.compile(r"今天|今日")
_YESTERDAY = re.compile(r"昨天|昨日")

CN_NUMBERS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}


def week_bounds(today: date) -> tuple[date, date]:
    """本周一到本周日。"""
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def parse_time_range(text: str, *, today: date | None = None) -> tuple[date, date] | None:
    """把时间表达解析成闭区间 `(start, end)`；解析不到返回 None。"""
    if not text:
        return None
    anchor = today or date.today()
    monday, sunday = week_bounds(anchor)

    if _TODAY.search(text):
        return anchor, anchor
    if _YESTERDAY.search(text):
        day = anchor - timedelta(days=1)
        return day, day
    if _LAST_WEEK.search(text):
        return monday - timedelta(days=7), sunday - timedelta(days=7)
    if _NEXT_WEEK.search(text):
        return monday + timedelta(days=7), sunday + timedelta(days=7)
    if _THIS_WEEK.search(text):
        return monday, sunday

    weekday = _WEEKDAY.search(text)
    if weekday:
        day = monday + timedelta(days=WEEK_CHARS[weekday.group(1)])
        return day, day

    days = _RECENT_DAYS.search(text)
    if days:
        span = int(days.group(2))
        if span <= 0:
            return None
        return anchor - timedelta(days=span - 1), anchor

    month = _MONTH.search(text)
    chinese_month = _MONTH_CN.search(text)
    if month or chinese_month:
        value = int(month.group(1)) if month else CN_NUMBERS.get(chinese_month.group(1), 0)
        if not 1 <= value <= 12:
            return None
        start = date(anchor.year, value, 1)
        end = date(anchor.year + (value == 12), (value % 12) + 1, 1) - timedelta(days=1)
        return start, end

    return None
