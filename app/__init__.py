"""会议纪要助手 应用包。

分层：api（HTTP）-> agent（编排）-> tools / retrieval / perception（能力层）
-> store / domain（基础设施）。

依赖只向下：能力层不 import 编排框架，模型访问只经 LLMClient 协议。
"""

__version__ = "0.1.0"
