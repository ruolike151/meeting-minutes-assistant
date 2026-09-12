"""评测层（M5）：数据集契约、指标函数、运行器与报告。

与 `tests/` 的分工（《M5 计划》Global Constraints 第 2 条）：

* `tests/` 保证"不坏"——离线、断言行为、每次提交都跑；
* `app/eval/` 回答"好不好"——数据集 + 指标 + 消融，产出报告。

这一层不 import 模型与网络：真实模型只通过已有的 `LLMClient` 协议注入。
"""

from app.eval.dataset import EvalDataset, load_dataset
from app.eval.metrics import accuracy, mrr, percentile, precision_recall_f1, recall_at_k

__all__ = [
    "EvalDataset",
    "accuracy",
    "load_dataset",
    "mrr",
    "percentile",
    "precision_recall_f1",
    "recall_at_k",
]
