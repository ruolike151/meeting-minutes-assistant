"""FastAPI 入口：挂载 API 路由与静态前端（《技术方案》3 节总体架构）。

前端是无构建的原生页面，由同一个进程托管，因此本地跑一条
`uvicorn app.main:app` 就能拿到完整应用，不需要额外的开发服务器。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import audio, chat, export, meetings, review
from app.config import Settings
from app.store.bootstrap import bootstrap


def _asset_version(path: Path) -> str:
    """静态资源的版本号 = 文件大小 + 修改时间（内容变了版本就变）。

    为什么要它：`Cache-Control: no-cache` 只让浏览器"先问一句"，
    但用户在重启服务前就已经把旧 JS 缓存在本地了；更糟的是某些浏览器策略会直接吃缓存。
    把版本号写进 URL 后，只要文件变了，浏览器就是在请求一个新地址——
    绕开所有缓存推断，不存在"服务端新、页面旧"的可能。
    """
    try:
        stat = path.stat()
    except OSError:
        return "0"
    return "%d-%d" % (int(stat.st_mtime), stat.st_size)


def render_index(web_dir: Path) -> str:
    """读取 index.html 并给本地 js / css 资源加上版本号查询串。"""
    html = (web_dir / "index.html").read_text(encoding="utf-8")
    for name in ("styles.css", "app.js", "chat.js", "panel.js", "api.js", "store.js", "ui.js"):
        asset = web_dir / name
        if not asset.is_file():
            continue
        version = _asset_version(asset)
        # index.html 里两种写法都要覆盖：src="app.js" 与 src="./app.js"
        for pattern in ('"%s"' % name, '"./%s"' % name):
            html = html.replace(pattern, '"%s?v=%s"' % (name, version))
    # 构建标记：出问题时看一眼就知道页面跑的是哪一版
    stamp = max((_asset_version(web_dir / name) for name in ("index.html", "app.js", "chat.js", "panel.js")), default="0")
    html = html.replace("<head>", '<head>\n    <meta name="ma-build" content="%s">' % stamp, 1)
    return html


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
        """静态资源一律不缓存（`Cache-Control: no-store`）。

        本地是"改完前端刷新就要看到"的工作方式，而 StaticFiles 默认不带任何缓存头，
        浏览器会按启发式规则**直接用缓存、不回源**——表现就是"代码明明改了，
        页面还是旧的"。

        为什么用 no-store 而不是 no-cache：前端是**原生 ES 模块**，
        `app.js` 里 `import './panel.js'` 这种**子导入不带查询串**，
        即使入口文件带了版本号，子模块仍可能命中旧缓存
        （用户遇到的"会话栏没历史对话"就是这个原因）。
        本地页面只有几十 KB，全部不缓存换确定性，值得。
        """
        response = await call_next(request)
        if not request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/", include_in_schema=False, response_class=HTMLResponse)
    def index_page() -> HTMLResponse:
        """首页由后端渲染：给静态资源带上版本号（见 `render_index`）。"""
        if not (resolved.web_dir / "index.html").is_file():
            return HTMLResponse("<h1>前端资源缺失</h1>", status_code=500)
        return HTMLResponse(render_index(resolved.web_dir))

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

__all__ = ["app", "create_app", "render_index"]
