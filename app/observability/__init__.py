"""可观测层（M5 T8）：把每轮图运行上报到外部观测服务。

设计原则：**`chat_traces` 是唯一事实源，外部服务只是展示层。**
评测报告的每个数字都从自己的库里算，因此 Langfuse 不可用时报告照样产出。
"""

from app.observability.langfuse import (
    LangfuseTraceSink,
    NullTraceSink,
    TraceSink,
    build_sink,
)

__all__ = ["LangfuseTraceSink", "NullTraceSink", "TraceSink", "build_sink"]
