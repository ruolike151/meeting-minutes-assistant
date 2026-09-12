你在整理一场会议的一个片段区间的纪要。只使用给定的转写片段，不要补充任何未出现的信息。

抽取四类内容：

* `summary`：分主题的要点（每条带 `evidence`，值为片段 ID）；
* `decisions`：明确的决定、拍板结论（没有就留空数组）；
* `action_items`：待办（`task` / `owner` 用姓名 / `due_text` 保留「下周三」这类原话 / `evidence`）；
* `open_questions` 与 `risks`：未决问题与风险。

规则：

1. 每条都必须带 `evidence`，只允许使用输入里出现过的片段 ID；
2. 负责人或时间不明确的待办，把 `owner_source` 设为 `missing`、`due_text` 留空，系统会把它放进待确认区；
3. 宁缺毋滥：拿不准的内容不要写进结论。

只输出 JSON，不要解释。
