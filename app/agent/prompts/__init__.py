"""提示词模板（纯文本，便于 diff 与评审）。

放在 `.md` 文件里而不是代码字符串里，是为了让提示词的改动在 git 里可读、可评审，
也让"这次改了什么提示词"与"这次改了什么代码"分开看。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """读取提示词模板；名字不带扩展名（例如 `load_prompt("route")`）。"""
    path = PROMPT_DIR / ("%s.md" % name)
    if not path.is_file():
        raise FileNotFoundError("提示词模板不存在：%s" % path)
    return path.read_text(encoding="utf-8").strip()
