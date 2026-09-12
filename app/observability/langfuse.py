"""Langfuse 适配（M5 T8，关闭 M3 决策 ⑨）。

三条纪律：

1. **默认不启用**：`MA_LANGFUSE_ENABLED=false` 时返回空实现，且**不 import langfuse**
   （用例断言这一点：观测依赖不能拖进主路径）；
2. **上报失败只记日志**：Langfuse 挂了、网络断了、Key 写错了，都不该让用户的问题答不出来；
3. **字段与 `chat_traces` 同源**：同一轮对话在本地库与 Langfuse 上的 token / 费用一致，
   否则看板与评测报告会对不上（用例断言两者取自同一个 usage 字典）。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Protocol, Sequence

from app.config import Settings

logger = logging.getLogger("ma.observability")


class TraceSink(Protocol):
    name: str

    def record(
        self,
        *,
        name: str,
        thread_id: str | None,
        nodes: Sequence,
        usage: dict,
        cost: float,
        meeting_id: str | None = None,
    ) -> None: ...


class NullTraceSink:
    """默认实现：什么都不做（离线、CI、未配置时的路径）。"""

    name = "off"
    reason = ""

    def record(self, **kwargs) -> None:  # noqa: D102 - 故意为空
        return None

    def flush(self) -> None:  # noqa: D102 - 故意为空
        return None


class LangfuseTraceSink:
    """真实上报：把一轮图运行写成一个 span。"""

    name = "langfuse"

    def __init__(self, host: str, public_key: str, secret_key: str, *, client=None) -> None:
        self.host = host
        self.public_key = public_key
        self.secret_key = secret_key
        self._client = client
        self.reason = ""
        self.sent = 0

    def _connect(self):
        if self._client is not None:
            return self._client
        try:
            from langfuse import Langfuse  # 延迟导入：只有启用时才会加载

            self._client = Langfuse(
                public_key=self.public_key,
                secret_key=self.secret_key,
                host=self.host,
            )
        except Exception as error:  # noqa: BLE001 - 观测依赖不可用不该影响主流程
            self.reason = "Langfuse 初始化失败（%s: %s）" % (type(error).__name__, error)
            logger.warning(self.reason)
            return None
        return self._client

    def record(
        self,
        *,
        name: str,
        thread_id: str | None,
        nodes: Sequence,
        usage: dict,
        cost: float,
        meeting_id: str | None = None,
    ) -> None:
        client = self._connect()
        if client is None:
            return
        payload = {
            "name": name,
            "trace_id": thread_id,
            "metadata": {
                "meeting_id": meeting_id,
                "nodes": [getattr(node, "name", str(node)) for node in nodes],
                "latency_ms": int(sum(getattr(node, "ms", 0) or 0 for node in nodes)),
            },
            "usage": {
                "input": int(usage.get("input_tokens") or 0),
                "output": int(usage.get("output_tokens") or 0),
            },
            "cost": float(cost),
        }
        try:
            self._send(client, payload)
            self.sent += 1
        except Exception as error:  # noqa: BLE001 - 上报失败只记日志
            self.reason = "Langfuse 上报失败（%s: %s）" % (type(error).__name__, error)
            logger.warning(self.reason)

    def _send(self, client, payload: dict) -> None:
        """兼容不同版本的 SDK（v4 用 OTEL 风格的 start_observation）。

        三条分支按"新 → 旧"尝试，都不匹配才抛错（由调用方降级）。
        """
        trace_id = to_trace_id(payload["trace_id"]) or client.create_trace_id()
        metadata = {**payload["metadata"], "cost": payload["cost"]}
        create = getattr(client, "create_trace", None)
        if callable(create):
            create(name=payload["name"], id=trace_id, metadata=metadata)
            return
        observation = getattr(client, "start_observation", None)
        if callable(observation):
            span = observation(
                name=payload["name"],
                trace_context={"trace_id": trace_id},
                metadata=metadata,
                usage_details={
                    "input": payload["usage"]["input"],
                    "output": payload["usage"]["output"],
                },
                cost_details={"total": payload["cost"]},
            )
            end = getattr(span, "end", None)
            if callable(end):
                end()
            return
        legacy = getattr(client, "trace", None)
        if callable(legacy):
            legacy(
                name=payload["name"],
                id=trace_id,
                metadata=metadata,
                usage=payload["usage"],
            )
            return
        raise AttributeError("Langfuse 客户端没有可用的上报接口")

    def flush(self) -> None:
        """把缓冲区里的 span 立刻推出去（脚本 / 短进程收尾时用）。"""
        client = self._client
        flush = getattr(client, "flush", None)
        if client is not None and callable(flush):
            try:
                flush()
            except Exception as error:  # noqa: BLE001 - 上报是增强，不阻塞主流程
                self.reason = "Langfuse flush 失败（%s: %s）" % (type(error).__name__, error)


def to_trace_id(value: str | None) -> str | None:
    """把会话 ID 映射成 Langfuse 认的 trace id（32 位小写 hex）。

    Langfuse 只接受 32 位 hex；我们的 `conversation_id`（`c_xxx`）直接传会被忽略，
    结果是**同一会话的每一轮各成一条 trace**。这里做一次确定性哈希：
    同一个会话永远映射到同一个 trace（可复现），不同会话不会撞车。
    """
    if not value:
        return None
    if re.fullmatch(r"[0-9a-f]{32}", value):
        return value
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def build_sink(settings: Settings | None) -> TraceSink:
    """按配置返回上报器；未启用 / 缺 Key 时返回空实现（并把原因留在 `reason`）。"""
    if settings is None or not settings.langfuse_enabled:
        return NullTraceSink()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        sink = NullTraceSink()
        sink.reason = "缺少 MA_LANGFUSE_PUBLIC_KEY / MA_LANGFUSE_SECRET_KEY，已跳过上报"
        return sink
    return LangfuseTraceSink(
        settings.langfuse_host, settings.langfuse_public_key, settings.langfuse_secret_key
    )


__all__ = ["LangfuseTraceSink", "NullTraceSink", "TraceSink", "build_sink", "to_trace_id"]
