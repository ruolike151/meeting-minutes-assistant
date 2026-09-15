"""FastAPI 入口：挂载 API 路由与静态前端（《技术方案》3 节总体架构）。

前端是无构建的原生页面，由同一个进程托管，因此本地跑一条
`uvicorn app.main:app` 就能拿到完整应用，不需要额外的开发服务器。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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

    @application.middleware("http")
    async def revalidate_static(request: Request, call_next):
        """静态资源必须每次回源校验（`Cache-Control: no-cache`）。

        本地是"改完前端刷新就要看到"的工作方式，而 StaticFiles 默认不带任何缓存头，
        浏览器会按启发式规则**直接用缓存、不回源**——表现就是"代码明明改了，
        页面还是旧的"（用户遇到过：会话栏的新前端没生效）。带上 no-cache 后，
        浏览器仍会存副本，但每次先发一个条件请求，没变就 304，变了立刻拿新的。
        """
        response = await call_next(request)
        if not request.url.path.startswith("/api"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

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
