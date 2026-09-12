"""测试夹具。

两条硬约束（《技术方案》14.1）：
1. 所有测试都在临时目录里跑，不写真实 data/，也不依赖网络与模型；
2. `MA_DATA_DIR` 在导入 app.main 之前就被改到临时目录，
   避免「导入即建库」污染工作区。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在导入 app.main 之前设置：模块级 app 会按环境变量建库
os.environ.setdefault("MA_DATA_DIR", str(Path(tempfile.mkdtemp()) / "data"))
# 离线后端是瞬时完成的，测试里不需要演示用的阶段间隔
os.environ.setdefault("MA_STAGE_DELAY", "0")

# M3：测试**永远不调用真实模型、不加载向量权重**。
# 这里用硬赋值而不是 setdefault——本机的 .env 里 MA_LLM_MODE=live，
# 一旦被读进来，测试就会去访问外部 API（约束 C3 的红线）。
# 必须在导入 app.config 之前生效：.env 是在那个模块导入时载入的。
os.environ["MA_LLM_MODE"] = "off"
os.environ["MA_TEXT_ENCODER"] = "hash"

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.store.bootstrap import bootstrap  # noqa: E402


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(tmp_path / "data", web_dir=ROOT / "web")


@pytest.fixture()
def conn(settings: Settings):
    connection = bootstrap(settings)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def client(settings: Settings):
    from fastapi.testclient import TestClient

    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client
