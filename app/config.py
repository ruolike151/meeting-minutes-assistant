"""运行配置与检索参数。

M1 只有两类配置：运行目录（数据 / 音频 / 导出 / 静态资源）与检索层旋钮。
全部可用环境变量覆盖，测试据此把数据写进临时目录，不必污染项目目录。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 环境变量统一用 MA_ 前缀（meeting assistant），避免与系统变量冲突
ENV_DATA_DIR = "MA_DATA_DIR"

DB_FILENAME = "meeting_assistant.db"

# -- 检索层参数（《技术方案》9.1）---------------------------------------
# M1 只落地「过滤 -> 关键词召回 -> 阈值拒答 -> 相邻片段组装」这半段；
# 向量召回、RRF 融合、重排属于 M3，这里先把其余旋钮的默认值固定下来。
RECALL_K1 = 50              # 关键词召回条数上限
TOP_K = 8                   # 进入上下文的片段条数
NEIGHBOR_WINDOW = 1         # 相邻片段窗口正负 1
TOKEN_BUDGET_CHARS = 2400   # 上下文预算（按字符近似，M3 换 token 计数）
MIN_COVERAGE = 0.34         # 查询词覆盖率低于该值视为证据不足，直接拒答

# 索引与查询共用的分词参数
MIN_TOKEN_LEN = 2           # 单字不成词，过滤掉「吃」「说」这类噪声


@dataclass(frozen=True)
class Settings:
    """一次进程运行需要的全部路径。"""

    data_dir: Path
    db_path: Path
    audio_dir: Path
    export_dir: Path
    web_dir: Path
    seed_on_start: bool = True

    @classmethod
    def from_env(
        cls,
        data_dir: str | os.PathLike[str] | None = None,
        *,
        seed_on_start: bool = True,
        web_dir: str | os.PathLike[str] | None = None,
    ) -> "Settings":
        raw = data_dir or os.environ.get(ENV_DATA_DIR) or (ROOT / "data")
        base = Path(raw).expanduser().absolute()
        return cls(
            data_dir=base,
            db_path=base / DB_FILENAME,
            audio_dir=base / "audio",
            export_dir=base / "export",
            web_dir=Path(web_dir).absolute() if web_dir else ROOT / "web",
            seed_on_start=seed_on_start,
        )

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.audio_dir, self.export_dir):
            path.mkdir(parents=True, exist_ok=True)
