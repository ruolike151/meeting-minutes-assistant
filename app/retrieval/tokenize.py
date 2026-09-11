"""中文分词（《技术方案》6.2、9.2）。

为什么必须有这一层：FTS5 自带的 unicode61 分词器不切分中日韩文字，
直接索引中文原文会让整句话成为一个 token，全文检索基本失效。
做法是入库时用 jieba 预分词，把空格分隔的结果写进 `segments.text_tokens`。

两条约束：
* 人名与专有名词要进用户词典，否则「王强」会被切成「王」「强」；
* 没有装 jieba 的机器也要能跑（约束 C3），因此保留一个纯 Python 的二元切分兜底，
  两种实现共用同一套停用词与过滤规则，切换只影响召回质量，不影响接口。
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.config import MIN_TOKEN_LEN

# 停用词只用于「查询侧」的覆盖率计算与召回，不参与索引写入。
# 这样「今天/中午/为什么」这类口语与疑问词不会把覆盖率抬到误判的程度，
# 拒答判定因此更稳定（《技术方案》9.1 第 8 步）。
STOPWORDS = frozenset(
    {
        # 口语时间词
        "今天", "明天", "昨天", "中午", "上午", "下午", "之前", "之后", "时候",
        "现在", "本周", "上周", "下周", "这场", "那场", "这批", "几场",
        # 疑问与指代
        "什么", "怎么", "怎样", "哪些", "哪个", "为什么", "如何", "是否", "多少",
        "几个", "多久", "这么", "那么", "这个", "那个", "这些", "那些", "一下",
        "帮我", "请问", "到底", "一共",
        # 无区分度的动词与连接词
        "我们", "他们", "会议", "说了", "了吗", "有没", "有没有", "没有", "是不",
        "不是", "以及", "关于", "可以", "然后", "就是", "还是", "的话",
        # 英文低频词
        "the", "and", "for", "with", "that", "this", "what", "which",
    }
)

_SEPARATOR = re.compile(r"[\s，。？！、,.?!;:；：“”\"'（）()\[\]【】《》<>—\-~·|/\\]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_RUN = re.compile(r"[A-Za-z0-9_]+")

_user_words: set[str] = set()


@lru_cache(maxsize=1)
def _jieba():
    """延迟导入 jieba：没装也能跑，只是退回二元切分。"""
    try:
        import jieba  # type: ignore
    except Exception:  # pragma: no cover - 取决于运行环境
        return None
    return jieba


def register_words(words) -> None:
    """把参会人姓名、专有名词加进用户词典，随会议数据动态更新。"""
    jb = _jieba()
    for word in words:
        word = (word or "").strip()
        if len(word) < MIN_TOKEN_LEN or word in _user_words:
            continue
        _user_words.add(word)
        if jb is not None:
            jb.add_word(word)


def registered_words() -> set[str]:
    return set(_user_words)


def _fallback_tokens(text: str) -> list[str]:
    """无 jieba 时的兜底：中文走二元切分，英文数字整段保留。"""
    tokens: list[str] = []
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    tokens.extend(_ASCII_RUN.findall(text))
    return tokens


def _normalize(tokens) -> list[str]:
    cleaned: list[str] = []
    for token in tokens:
        token = _SEPARATOR.sub("", token).strip()
        if not token:
            continue
        if len(token) < MIN_TOKEN_LEN and not token.isascii():
            continue
        cleaned.append(token)
    return cleaned


def raw_tokens(text: str) -> list[str]:
    """索引与查询共用的分词结果（不过滤停用词）。"""
    if not text:
        return []
    jb = _jieba()
    if jb is None:
        return _normalize(_fallback_tokens(text))
    return _normalize(jb.cut(text))


def content_tokens(text: str) -> list[str]:
    """去掉停用词后的查询词，用于覆盖率与拒答判定。"""
    seen: list[str] = []
    for token in raw_tokens(text):
        lowered = token.lower()
        if token in STOPWORDS or lowered in STOPWORDS:
            continue
        if token not in seen:
            seen.append(token)
    return seen


def tokenize_for_index(text: str) -> str:
    """写进 `segments.text_tokens` 的形式：空格分隔，供 FTS5 建索引。"""
    return " ".join(raw_tokens(text))


def match_expression(tokens) -> str:
    """拼 FTS5 MATCH 表达式：多词 OR 召回，具体排序交给 BM25。"""
    return " OR ".join('"%s"' % token.replace('"', '""') for token in tokens)
