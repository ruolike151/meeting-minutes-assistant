"""对话接口：POST /api/chat（JSON 与 SSE 两种返回）。

同一个编排生成器驱动两条路径：
* JSON：跑完取终态，回答里带完整引用；
* SSE：把 stage / citation / delta / done 逐个推给前端。
前端默认用 SSE 显示阶段进度，失败时回退 JSON。
"""

from __future__ import annotations

from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.agent.pipeline import ChatResult, respond, stream
from app.api.deps import get_context, get_settings
from app.api.export import content_disposition, conversation_markdown
from app.api.schemas import ChatRequest, ChatResponse
from app.api.sse import SSE_HEADERS, sse_event
from app.config import Settings
from app.domain.models import Conversation
from app.store.conversations_repo import ConversationRepo
from app.store.db import connect
from app.tools.registry import ToolContext

router = APIRouter(prefix="/api", tags=["chat"])


def wants_stream(request: Request) -> bool:
    flag = (request.query_params.get("stream") or "").lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    return "text/event-stream" in (request.headers.get("accept") or "").lower()


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    request: Request,
    ctx: ToolContext = Depends(get_context),
):
    if wants_stream(request) and ctx.settings is not None:
        return _streaming_response(ctx.settings, payload)
    result = respond(
        ctx.conn,
        payload.message,
        conversation_id=payload.conversation_id,
        active_meeting_id=payload.meeting_id,
    )
    return ChatResponse(
        conversation_id=result.conversation_id,
        message=result.assistant_message,
        answer=result.answer,
    )


def _streaming_response(settings: Settings, payload: ChatRequest) -> StreamingResponse:
    def generator() -> Iterator[str]:
        conn = connect(settings.db_path)
        try:
            for event, data in stream(
                conn,
                payload.message,
                conversation_id=payload.conversation_id,
                active_meeting_id=payload.meeting_id,
            ):
                if event == "done":
                    result: ChatResult = data  # type: ignore[assignment]
                    yield sse_event(
                        "done",
                        {
                            "conversation_id": result.conversation_id,
                            "message": result.assistant_message.model_dump(mode="json"),
                            "answer": result.answer.model_dump(mode="json"),
                        },
                    )
                    continue
                yield sse_event(event, data)
        except Exception as error:  # noqa: BLE001 - 编排异常转成 error 事件
            yield sse_event("error", {"message": "%s: %s" % (type(error).__name__, error)})
        finally:
            conn.close()

    return StreamingResponse(
        generator(), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.get("/conversations/{conversation_id}", response_model=Conversation)
def get_conversation(
    conversation_id: str, ctx: ToolContext = Depends(get_context)
) -> Conversation:
    conversation = ConversationRepo(ctx.conn).get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


@router.get("/conversations/{conversation_id}/export")
def export_conversation(
    conversation_id: str,
    format: str = Query(default="md", pattern="^md$"),
    ctx: ToolContext = Depends(get_context),
) -> Response:
    """对话记录导出：保留问答与引用来源（产品文档 5.5）。"""
    conversation = ConversationRepo(ctx.conn).get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    body = conversation_markdown(conversation)
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": content_disposition("对话记录.md")},
    )
