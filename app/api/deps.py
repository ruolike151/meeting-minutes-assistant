"""依赖注入：每个请求一条 SQLite 连接。

FastAPI 的同步路由跑在线程池里，连接不能跨线程复用，
因此这里按请求开关连接，而不是全进程共用一个连接对象。
"""

from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, Request

from app.config import Settings
from app.store.db import connect
from app.tools.registry import ToolContext


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(settings: Settings = Depends(get_settings)) -> Iterator[sqlite3.Connection]:
    conn = connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_context(
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> ToolContext:
    return ToolContext(conn=conn, settings=settings)
