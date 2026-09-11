"""SSE 事件编码（《技术方案》11 节的事件契约）。

统一放在这里，聊天流与音频任务流共用同一套编码，
避免两个接口各写一份导致事件名漂移。
"""

from __future__ import annotations

import json

# 浏览器 EventSource 不需要 CORS 头，但关掉代理缓冲能避免事件被攒着一起发
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_event(event: str, data: object) -> str:
    """把一条事件编码成 SSE 帧：event 行 + data 行 + 空行。"""
    payload = json.dumps(data, ensure_ascii=False)
    return "event: %s\ndata: %s\n\n" % (event, payload)
