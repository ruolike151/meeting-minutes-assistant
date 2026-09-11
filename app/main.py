"""FastAPI 入口：挂载 API 路由与静态前端（《技术方案》3 节总体架构）。

前端是无构建的原生页面，由同一个进程托管，因此本地跑一条
`uvicorn app.main:app` 就能拿到完整应用，不需要额外的开发服务器。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import audio, chat, export, meetings, review
from app.config import Settings
from app.store.bootstrap import bootstrap


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    resolved.ensure_dirs()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # 启动时建表并按需装载固定样例数据；重复启动不会重复写入
        conn = bootstrap(resolved)
        conn.close()
        yield

    application = FastAPI(
        title="会议纪要助手",
        version=__version__,
        description="对话式会议纪要助手：音频进、带引用的答案出。",
        lifespan=lifespan,
    )
    application.state.settings = resolved

    application.include_router(chat.router)
    application.include_router(meetings.router)
    application.include_router(review.router)
    application.include_router(export.router)
    application.include_router(audio.router)

    if resolved.web_dir.is_dir():
        # 放在所有 API 路由之后挂载：/api/* 先匹配，其余走静态资源
        application.mount(
            "/", StaticFiles(directory=str(resolved.web_dir), html=True), name="web"
        )
    return application


app = create_app()
