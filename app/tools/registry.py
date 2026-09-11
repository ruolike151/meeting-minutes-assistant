"""工具注册表：把入参模型导出成 JSON Schema 交给模型（《技术方案》8）。

模型看到的只是 Schema，换模型不影响工具实现；工具实现是纯函数，
因此每个工具都能脱离框架与模型单独测试。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.tools.audio import ProcessAudioInput, ProcessAudioOutput, process_audio
from app.tools.meetings import (
    GetMinutesInput,
    GetMinutesOutput,
    ListMeetingsInput,
    ListMeetingsOutput,
    UpdateActionItemInput,
    UpdateActionItemOutput,
    get_minutes,
    list_meetings,
    update_action_item,
)
from app.tools.search import (
    FlagUncertaintyInput,
    FlagUncertaintyOutput,
    QuerySpeakerInput,
    QuerySpeakerOutput,
    SearchSegmentsInput,
    SearchSegmentsOutput,
    flag_uncertainty,
    query_speaker,
    search_segments,
)


@dataclass
class ToolContext:
    """工具执行上下文：数据库连接与运行配置。"""

    conn: sqlite3.Connection
    settings: Settings | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], BaseModel]


def _meetings_handler(ctx: ToolContext, payload: BaseModel) -> BaseModel:
    return list_meetings(ctx.conn, payload)  # type: ignore[arg-type]


def _minutes_handler(ctx: ToolContext, payload: BaseModel) -> BaseModel:
    return get_minutes(ctx.conn, payload)  # type: ignore[arg-type]


def _update_handler(ctx: ToolContext, payload: BaseModel) -> BaseModel:
    return update_action_item(ctx.conn, payload)  # type: ignore[arg-type]


def _search_handler(ctx: ToolContext, payload: BaseModel) -> BaseModel:
    return search_segments(ctx.conn, payload)  # type: ignore[arg-type]


def _speaker_handler(ctx: ToolContext, payload: BaseModel) -> BaseModel:
    return query_speaker(ctx.conn, payload)  # type: ignore[arg-type]


def _flag_handler(ctx: ToolContext, payload: BaseModel) -> BaseModel:
    return flag_uncertainty(ctx.conn, payload)  # type: ignore[arg-type]


def _audio_handler(ctx: ToolContext, payload: BaseModel) -> BaseModel:
    if ctx.settings is None:
        raise RuntimeError("process_audio 需要 ToolContext.settings")
    return process_audio(ctx.settings, payload)  # type: ignore[arg-type]


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_meetings",
        description="列出或筛选已有会议（标题 / 日期区间 / 参会人）",
        input_model=ListMeetingsInput,
        output_model=ListMeetingsOutput,
        handler=_meetings_handler,
    ),
    ToolSpec(
        name="get_minutes",
        description="取单场会议的结构化纪要",
        input_model=GetMinutesInput,
        output_model=GetMinutesOutput,
        handler=_minutes_handler,
    ),
    ToolSpec(
        name="search_segments",
        description="片段级检索，支持按会议与发言人过滤",
        input_model=SearchSegmentsInput,
        output_model=SearchSegmentsOutput,
        handler=_search_handler,
    ),
    ToolSpec(
        name="query_speaker",
        description="按发言人汇总发言、待办与出现场次",
        input_model=QuerySpeakerInput,
        output_model=QuerySpeakerOutput,
        handler=_speaker_handler,
    ),
    ToolSpec(
        name="update_action_item",
        description="修改待办的状态 / 负责人 / 截止时间",
        input_model=UpdateActionItemInput,
        output_model=UpdateActionItemOutput,
        handler=_update_handler,
    ),
    ToolSpec(
        name="flag_uncertainty",
        description="把存疑条目录入待确认区，等待人工确认",
        input_model=FlagUncertaintyInput,
        output_model=FlagUncertaintyOutput,
        handler=_flag_handler,
    ),
    ToolSpec(
        name="process_audio",
        description="为一段会议音频新建处理任务（转写 + 声纹 + 生成纪要）",
        input_model=ProcessAudioInput,
        output_model=ProcessAudioOutput,
        handler=_audio_handler,
    ),
)

_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}


def get_tool(name: str) -> ToolSpec:
    if name not in _BY_NAME:
        raise KeyError("未注册的工具：%s" % name)
    return _BY_NAME[name]


def tool_names() -> list[str]:
    return [spec.name for spec in TOOL_SPECS]


def tool_schemas() -> list[dict]:
    """导出给模型的工具清单（OpenAI 兼容的 function 描述形状）。"""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_model.model_json_schema(),
        }
        for spec in TOOL_SPECS
    ]


def call_tool(ctx: ToolContext, name: str, payload: dict | BaseModel) -> BaseModel:
    """统一调用入口：入参先过 Pydantic 校验，再交给纯函数。"""
    spec = get_tool(name)
    if isinstance(payload, spec.input_model):
        parsed = payload
    else:
        try:
            parsed = spec.input_model.model_validate(payload)
        except ValidationError as error:
            raise ValueError("工具 %s 的入参不合法：%s" % (name, error)) from error
    return spec.handler(ctx, parsed)
