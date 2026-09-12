"""感知后端工厂（计划文档 v2 第 4.1 节）。

`MA_PERCEPTION_BACKEND` 三个取值：

* `offline`（默认）—— 固定数据后端，无依赖、无 GPU、无网络（约束 C3）；
* `local` —— 真实模型（faster-whisper + ECAPA）。依赖或权重缺失时**回落 offline**，
  并把原因写进任务事件；
* `auto` —— 语义上表示「能真就真，不能真就假」，行为同 local。

回落这件事需要防一手：如果部署时配了 `local` 却一直在跑固定数据，
从界面看不出来。因此提供 `MA_PERCEPTION_STRICT=1`：此时不可用直接报错，
让问题在启动阶段暴露，而不是在评审现场暴露。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import DEFAULT_PERCEPTION_BACKEND, Settings
from app.perception.base import BackendUnavailable, PerceptionBackend, PerceptionError
from app.perception.offline import OfflinePerception

VALID_BACKENDS = ("offline", "local", "auto")


@dataclass(frozen=True)
class BackendSelection:
    backend: PerceptionBackend
    requested: str
    fallback_reason: str | None = None

    @property
    def is_fallback(self) -> bool:
        return self.fallback_reason is not None


def select_backend(settings: Settings, conn: object | None = None) -> BackendSelection:
    """按配置选择后端，返回选择结果与回落原因。

    `conn` 是数据库连接：本地后端需要它加载声纹库（没有声纹库时所有片段
    都会判成未识别，所以调用方一定要把连接传进来）。
    """
    requested = (settings.perception_backend or DEFAULT_PERCEPTION_BACKEND).strip().lower()
    if requested not in VALID_BACKENDS:
        raise PerceptionError(
            "MA_PERCEPTION_BACKEND 取值非法：%s（可选：%s）"
            % (requested, " / ".join(VALID_BACKENDS))
        )

    offline = OfflinePerception()
    if requested == "offline":
        return BackendSelection(backend=offline, requested=requested)

    try:
        # 延迟导入：没有安装感知依赖的机器上，导入工厂本身不能失败
        from app.perception.local import LocalPerception

        local = LocalPerception.from_settings(settings, conn)
    except (BackendUnavailable, PerceptionError, ImportError, OSError) as error:
        reason = "local 后端不可用，已回落到离线后端：%s: %s" % (type(error).__name__, error)
        if settings.strict_perception:
            raise PerceptionError(reason) from error
        return BackendSelection(backend=offline, requested=requested, fallback_reason=reason)

    return BackendSelection(backend=local, requested=requested)


def get_backend(settings: Settings, conn: object | None = None) -> PerceptionBackend:
    return select_backend(settings, conn).backend


def get_stages(settings: Settings, conn: object | None = None) -> list[str]:
    """任务进度用的阶段名（与后端一致）。"""
    return list(get_backend(settings, conn).stages())