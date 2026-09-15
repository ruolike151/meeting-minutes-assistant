# 会议纪要助手

对话式会议知识库：把会议录音变成**可检索、可追溯、可追问**的纪要。

输入可以是音频，也可以是问题；输出永远带原文证据。

产品目标与范围见 [`docs/产品设计文档.md`](docs/产品设计文档.md)，
分层结构、选型理由与接口约定见 [`docs/技术方案.md`](docs/技术方案.md)，
里程碑的落地计划见 [`docs/M2-感知阶段实施计划.md`](docs/M2-感知阶段实施计划.md)（已完成）与
[`docs/M3-编排与检索阶段实施计划.md`](docs/M3-编排与检索阶段实施计划.md)（待评审），
界面基线是 [`prototype/`](prototype/) 里的可交互原型。

---

## M1 交付了什么

按《技术方案》17 节的里程碑，M1「骨架」的验收口径是：

> FastAPI 服务 + 静态前端托管；Pydantic 契约与 SQLite 表；固定样例数据跑通对话与会议库。

本仓库当前状态：

| 交付项 | 落地位置 | 说明 |
| --- | --- | --- |
| FastAPI 服务 + 静态前端托管 | `app/main.py`、`web/` | 一个进程同时提供 API 与无构建前端 |
| Pydantic 契约 | `app/domain/models.py` | 兼作 LLM 结构化输出的目标 schema 与入库校验 |
| SQLite 表与迁移 | `app/store/db.py`、`app/store/repos*.py` | 含 FTS5 中文索引、纪要 JSON、人工修改 override 表 |
| 固定样例数据 | `app/store/seeds.py` | 3 场会议（32 个片段）取自原型 mock 数据 |
| 对话链路 | `app/agent/` | 规则路由 → 范围解析 → 检索/装配 → 证据校验 → 落库 |
| 检索层（M1 半段） | `app/retrieval/` | jieba 预分词 + FTS5 BM25 + 覆盖率阈值拒答 + 相邻片段组装 |
| 工具层 | `app/tools/` | 7 个工具，入参/出参都是 Pydantic，可脱离框架单测 |
| 会议库与详情 | `app/api/meetings.py`、`web/panel.js` | 列表筛选、纪要 / 待办 / 待确认 / 原声四个标签页 |
| 人在回路的接口 | `app/api/review.py` | 待确认区读取、确认 / 忽略，写 override 表 |
| 导出 | `app/api/export.py` | 纪要 Markdown、待办 CSV、结构化 JSON、对话记录 |
| 音频处理（离线后端） | `app/perception/offline.py` | 无 GPU、无模型 Key 也能跑通「上传 → 进度 → 纪要」 |
| 测试 | `tests/` | 91 项，全部无需网络、无需模型、无需 GPU |

**M1 刻意不做**：真实转写与声纹（M2）、LangGraph 状态图与向量检索（M3）、
证据回跳与人工确认的完整交互（M4）、评测报告（M5）。
M1 的规则路由与关键词检索是**可替换的临时实现**，
接口形状与 M3 的最终实现一致，替换时上层不用改。

---

## M2 交付了什么（感知层）

按《技术方案》17 节，M2 的目标是「`perception/` 接入真实转写与声纹，`offline.py` 保留为测试后端」。

| 交付项 | 落地位置 | 说明 |
| --- | --- | --- |
| 感知层契约 | `app/perception/base.py` | `PerceptionBackend` 协议 + `RawSegment` / `SpeakerTurn` 数据契约 |
| 后端工厂 | `app/perception/factory.py` | `MA_PERCEPTION_BACKEND=offline\|local\|auto`；`local` 不可用时回落离线后端并给出原因，`MA_PERCEPTION_STRICT=1` 时改为直接报错 |
| 音频标准化 | `app/perception/audio_io.py` | ffmpeg → 16kHz 单声道；无 ffmpeg 时 wav 直通（约束 C3） |
| 转写 | `app/perception/transcribe.py` | faster-whisper（懒加载、VAD、片段合并/切分），片段粒度 1.2–25 秒 |
| 声纹 | `app/perception/embedding.py`、`voiceref.py` | SpeechBrain ECAPA-TDNN（VoxCeleb 192 维）+ 滑窗匹配（2.5s/1.25s、每人 top-3 相似度均值、margin 判定） |
| 身份对齐 | `app/perception/align.py` | 片段 × 声纹窗口按「重叠时长 × 置信度」加权投票；无证据则判未识别 |
| 声纹库 | `app/store/speakers_repo.py`、`app/tools/speakers.py`、`scripts/enroll_speaker.py` | 全局发言人表 + 多枚向量存 BLOB；`register_speaker` 工具补齐（文档 8 节契约） |
| 任务执行 | `app/store/tasks_repo.py`、`app/api/audio.py` | 后台单线程池执行 + 任务状态落库；SSE 改为轮询进度，断流不再中断处理 |
| 模型与素材 | `model/`（不入库）、`samples/` | 权重由 `scripts/download_models.py` 下载；`samples/` 含 2 场会议、4 段注册音频与人工标注 |
| 评测 | `scripts/eval_perception.py`、`docs/感知层评测报告.md` | 窗口级准确率、宏平均召回、混淆矩阵、CER/WER、RTF |

**实测（`meeting.mp3`，86.5 秒，4 人，faster-whisper small + ECAPA）**：
窗口级说话人准确率 **93.75%**、宏平均召回 **92.86%**、片段级准确率 **100%**；
端到端 RTF **0.093**（RTX 4060 / CUDA）与 **0.484**（纯 CPU）。与历史项目（同为 ECAPA 方案）的
93.75% / 92.86% 对齐，说明移植后的链路没有退化。

---

## M3 交付了什么（编排与检索）

按《技术方案》17 节，M3 的目标是「LangGraph 状态图 + 自研混合检索 + 带引用的问答」。
详细计划见 [`docs/M3-编排与检索阶段实施计划.md`](docs/M3-编排与检索阶段实施计划.md)，
实测数字见 [`docs/M3-编排与检索评测报告.md`](docs/M3-编排与检索评测报告.md)。

| 交付项 | 落地位置 | 说明 |
| --- | --- | --- |
| LangGraph 状态图 | `app/agent/graph.py` | **全项目唯一 import langgraph 的地方**；节点是纯函数，框架出问题炸不到业务逻辑 |
| 编排节点 | `app/agent/nodes/` | route / resolve_scope / clarify / create_task / retrieve / grade / refuse / synthesize / verify_evidence / finalize |
| 模型路由 + 规则兜底 | `app/agent/nodes/routing_nodes.py` | 模型结论要过三道护栏（会议真实存在、发言人有效、intent 合法），否则回落 M1 的规则表 |
| 混合检索 | `app/retrieval/{encoder,vector,fusion,timefilter}.py` | 过滤下推 → BM25 + 向量 → RRF 融合 → 双闸拒答 → 相邻片段组装 |
| 文本向量三档 | `app/retrieval/encoder.py` | 本地 BGE（默认）/ 云端 embedding API（显式启用）/ 纯标准库哈希兜底（保证无权重也能跑） |
| LLM 适配 | `app/llm/{openai_compat,cache,structured,factory}.py` | OpenAI 兼容协议 + 缓存记账 + 结构化输出四级降级 |
| 纪要生成 | `app/agent/minutes.py` | map-reduce + schema 校验重试 + 单条降级待确认；替换 M2 的占位纪要 |
| 多轮上下文 | `app/agent/history.py` | 最近 3 轮原文 + 更早轮次摘要；**范围跨轮继承**（"那这场会……"不用重新指定） |
| 成本可观测 | `app/store/traces_repo.py`、`chat_traces` 表 | 每轮回答记录节点耗时、模型、token、费用 |
| 参数评测 | `app/retrieval/evaluate.py`、`scripts/eval_retrieval.py` | 22 条标注集上的 Recall@k / MRR / 拒答正确率 + 扫参 |
| 测试 | `tests/` | **289 项，全部无需网络、无需模型、无需 GPU、无需 API Key** |

**实测（3 场样例会议 / 32 条片段 / 22 条标注查询 / k=8）**：

| 编码器 | Recall@8 | MRR | 拒答正确率（域外） |
| --- | ---: | ---: | ---: |
| `keyword-only`（M1 基线） | 1.000 | 0.857 | 1.000 |
| `hash-bigram-v1#256`（兜底） | 1.000 | 0.809 | 1.000 |
| `bge-small-zh-v1.5#512` | 1.000 | **0.881** | 1.000 |

两条如实说明：召回在这份小标注集上已经饱和，语义检索的收益主要落在排序（MRR +2.8%）；
向量余弦阈值由 0.35 扫到 **0.50**（拒答正确率 0.25 → 1.00），依据见评测报告第 3 节。

**M3 刻意不做**：checkpointer 持久化与 `interrupt` 挂起恢复（M4）、证据回跳与
待确认区的完整交互（M4）、Langfuse 看板与扩展标注集（M5）、重排（决策 ③ 留 M5）。

---

## M4 交付了什么（协同 / 人在回路）

按《技术方案》17 节，M4 的目标是「checkpointer + interrupt 支撑待确认区；证据回跳；
人工修改落 override 表」。计划见 [`docs/M4-协同阶段实施计划.md`](docs/M4-协同阶段实施计划.md)，
验收见 [`docs/M4-协同阶段验收报告.md`](docs/M4-协同阶段验收报告.md)。

| 交付项 | 落地位置 | 说明 |
| --- | --- | --- |
| 挂起与恢复 | `app/agent/graph.py`、`app/agent/checkpoint.py` | 新增 minutes 图：生成 → 校验 → `review_gate(interrupt)` → 收尾；状态落 `SqliteSaver`（`data/checkpoints.sqlite`） |
| 恢复链路 | `app/agent/review.py`、`app/api/review.py` | 确认最后一条 → `Command(resume=…)` 恢复图 → 会议转 `processed`；没有检查点时降级为直接收尾 |
| 待确认区 | `app/store/minutes_repo.py::pending_items` | 待办 + 低置信度决议；`confirm` / `dismiss` / `edit` 三种动作 |
| 人工修改留痕 | `action_item_overrides` + `review_events` | 结论与流水分开存；重新生成纪要不会覆盖人工结论 |
| 证据回跳 | `GET /api/meetings/{id}/audio`、`web/panel.js` | 点引用 → 原声定位高亮 → 播放器从该句起点播放；支持跨会议跳转 |
| 状态机 | `meetings.status` | `processing → awaiting_review → processed`，与会议库徽标同源 |

**实测**：全量 **384 项测试通过、1 项跳过、0 失败**；挂起、恢复、降级三条路径各有用例。

---

## M5 交付了什么（评测）

按《产品设计文档》8 节，M5 要把每一项指标跑成可复现的数字。计划见
[`docs/M5-评测阶段实施计划.md`](docs/M5-评测阶段实施计划.md)，
数字、失败归因与缺口见 [`docs/M5-评测报告.md`](docs/M5-评测报告.md)。

| 交付项 | 落地位置 | 说明 |
| --- | --- | --- |
| 数据集契约 | `app/eval/dataset.py`、`app/eval/fixtures.py`、`samples/annotations/m5/` | 70 检索 / 34 路由 / 29 问答 / 5 场纪要金标；真实语料由 `scripts/build_eval_corpus.py` 从转写稿 + 人工金标合成后灌入评测库 |
| 指标库 | `app/eval/metrics.py` | P/R/F1、Recall@k、MRR、准确率、混淆矩阵、拒答两方向、分位数、kappa、成本汇总 |
| 运行器与报告 | `app/eval/runner.py`、`app/eval/report.py` | 一次运行产出 `raw.json` / `metrics.json` / `report.md`；离线基线无网络无 Key |
| 重排消融 | `app/retrieval/rerank.py` | cross-encoder 可插拔、不可用回落、原因可见；实测 MRR 0.579 → 0.661（+14.1%） |
| 可观测 | `app/observability/langfuse.py`、`docker-compose.langfuse.yml` | 未启用时**不 import** langfuse；上报失败只记日志；口径见 [`docs/M5-可观测看板.md`](docs/M5-可观测看板.md) |
| 一键复现 | `scripts/run_eval_all.py` | `--offline` / `--cache-first` / `--live` / `--dry-run` / `--rerank` |

---

## 快速开始

> 第一次上手看 [`docs/操作教程.md`](docs/操作教程.md)（傻瓜式逐步操作 + 排错 + 演示脚本）；
> 面试讲解看 [`docs/MVP技术报告.md`](docs/MVP技术报告.md)（架构、选型、指标、追问问答）。

### 0. 配置 `.env`（可选，不配也能跑）

```powershell
Copy-Item .env.example .env      # 然后把 MA_LLM_API_KEY 填上你自己的 Key
```

`.env` 已被 `.gitignore` 排除，Key 不会入库；**不填 Key 也能跑通全部功能和测试**——
`MA_LLM_MODE=auto` 在没有 Key 时等价于 `off`，链路自动落到
「规则路由 + 混合检索 + 模板装配」，行为与 M1 等价但已经走图。

换 LLM 供应商只改这一处（provider 是协议名，不是厂商名）：

```ini
MA_LLM_PROVIDER=openai
MA_LLM_BASE_URL=https://api.deepseek.com   # 换成任意 OpenAI 兼容端点
MA_LLM_MODEL_ANSWER=deepseek-flash
```

虚拟环境按要求建在项目根目录下：`meeting_assistant/`（已加入 `.gitignore`，不入库）。
新克隆的仓库里没有这个目录，先建一次即可（Python 3.12）：

```powershell
# 0. 建虚拟环境（首次或换机器时执行；已存在则跳过）
python -m venv meeting_assistant

# 1. 安装依赖
.\meeting_assistant\Scripts\python.exe -m pip install -r requirements.txt

# 2. 启动服务（默认监听 8000，数据落在 data/ 目录）
.\meeting_assistant\Scripts\python.exe -m uvicorn app.main:app --reload

# 3. 打开浏览器
start http://127.0.0.1:8000
```

首次启动会自动建库并写入 3 场固定样例会议，因此打开页面就能直接提问：

- 这场周会定了什么？
- C 一共答应了哪些事？
- 谁提到了活动弹窗的问题？
- 这三场会有什么风险？
- 公司食堂今天中午吃什么？（会明确拒答，演示不编造）

也可以点输入框左侧的回形针上传任意音频文件，走一遍
「音频标准化 → 转写 → 声纹 → 生成纪要」的离线流程（M1 返回固定样例内容）。

数据目录可用环境变量覆盖：`MA_DATA_DIR=D:\ma_data`。

---

### 跑真实感知链路（可选，需要额外依赖）

```powershell
# 1. 安装感知层依赖（CUDA 版 torch 约 2.5GB；没有 GPU 也能装 CPU 版跑通）
.\meeting_assistant\Scripts\python.exe -m pip install -r requirements-perception.txt

# 2. 下载权重到 model/（约 550MB：small 转写模型 + ECAPA 声纹模型）
.\meeting_assistant\Scripts\python.exe scripts\download_models.py --sizes small

# 3. 用样例音频注册 4 个发言人声纹
.\meeting_assistant\Scripts\python.exe scripts\enroll_samples.py

# 4. 跑一遍真实链路并输出评测报告
.\meeting_assistant\Scripts\python.exe scripts\smoke_perception.py --meeting samples\audio\meeting.mp3
.\meeting_assistant\Scripts\python.exe scripts\eval_perception.py --skip-enroll
```

默认后端仍是 `offline`（固定样例数据），要启用真实模型：

```powershell
$env:MA_PERCEPTION_BACKEND = "local"
```

模型权重与样例音频的说明见 [`samples/README.md`](samples/README.md)。

---

### 语义检索（可选，用本地 BGE）

```powershell
# 1. 安装向量依赖（torch 已由感知层依赖提供，不会重复下载）
.\meeting_assistant\Scripts\python.exe -m pip install -r requirements-embedding.txt

# 2. 下载中文向量权重到 model/（约 95MB，走 hf-mirror.com）
.\meeting_assistant\Scripts\python.exe scripts\download_models.py --embedding-only
```

`MA_TEXT_ENCODER` 三档：

| 取值 | 行为 |
| --- | --- |
| `auto`（默认） | 有权重用本地 BGE，没有则落到哈希兜底，并把回落原因写进 trace |
| `bge` | 强制本地 BGE；权重缺失直接报错（适合演示前确认） |
| `hash` | 纯标准库兜底：确定性、零依赖，只捕捉字面重合 |
| `api` | 云端 embedding（OpenAI 兼容 `/embeddings`，**必须显式指定**，因为要走网络与计费） |

评测与扫参：

```powershell
.\meeting_assistant\Scripts\python.exe scripts\eval_retrieval.py --encoder keyword
.\meeting_assistant\Scripts\python.exe scripts\eval_retrieval.py --encoder bge
.\meeting_assistant\Scripts\python.exe scripts\eval_retrieval.py --encoder bge --sweep
```

---

### 用真实 LLM 跑一遍（需要 Key）

```powershell
$env:MA_RUN_LLM_SMOKE = "1"        # 没有这个开关，脚本不会发任何请求
.\meeting_assistant\Scripts\python.exe scripts\eval_llm_smoke.py

# 演示前预热缓存：之后可以用 MA_LLM_MODE=cache_first 断网演示
.\meeting_assistant\Scripts\python.exe scripts\warm_cache.py
```

### 跑一遍全部评测（M5，不需要 Key）

```powershell
# 离线基线：12 项指标 + 重排消融，全程无网络、无模型
.\meeting_assistant\Scripts\python.exe scripts\run_eval_all.py --offline --encoder hash --rerank

# 只跑分项
.\meeting_assistant\Scripts\python.exe scripts\eval_minutes.py      # 纪要质量
.\meeting_assistant\Scripts\python.exe scripts\eval_dialogue.py     # 路由与多轮

# 真实模型（需要 .env 里的 MA_LLM_API_KEY，先 --dry-run 看预算）
.\meeting_assistant\Scripts\python.exe scripts\run_eval_all.py --live --judge llm --dry-run
.\meeting_assistant\Scripts\python.exe scripts\run_eval_all.py --live --judge llm

# 开启重排需要先下权重（约 1.1GB）
.\meeting_assistant\Scripts\python.exe scripts\download_models.py --rerank --skip-embedding --skip-speaker
```

产物落在 `data/eval/<run_id>/`（`raw.json` 逐条明细 / `metrics.json` 汇总 /
`report.md` 报告）；当前实测数字与缺口见 [`docs/M5-评测报告.md`](docs/M5-评测报告.md)。

单场会议重新生成纪要（换了模型或改了提示词时不用重新上传音频）：

```powershell
curl -X POST http://127.0.0.1:8000/api/meetings/m_20260911/minutes/regenerate
```

---

## 运行测试

```powershell
.\meeting_assistant\Scripts\python.exe -m pytest
```

CI 不出现任何真实网络调用：模型出口由 `FakeLLMClient` 堵死，
转写出口由 `perception/offline.py` 的固定数据后端顶替（约束 C3）。
文档一致性测试 `tests/test_docs.py` 也可以脱离 pytest 直接跑：

```powershell
.\meeting_assistant\Scripts\python.exe tests\test_docs.py
```

---

## 目录结构

```text
app/
  main.py            FastAPI 入口：挂载路由与静态前端
  config.py          运行目录 / 检索参数 / LLM 与向量配置（环境变量或 .env 覆盖）
  api/               HTTP 层：路由 + DTO + 依赖注入
  agent/             编排层：graph（LangGraph 装配）、nodes（纯函数节点）、
                     minutes（LLM 纪要生成）、history（多轮上下文）、pipeline（门面）
  tools/             工具层：Pydantic 契约 + JSON Schema 导出
  retrieval/         检索层：tokenize / keyword(FTS5+BM25) / encoder / vector / fusion /
                     timefilter / pipeline / evaluate（评测指标）
  perception/         感知层：offline（默认）/ local（faster-whisper + ECAPA）
  llm/               LLM 适配：openai_compat / cache（缓存+记账）/ structured / factory / fake
  store/             SQLite 建表迁移、仓储（含片段向量、对话 trace）、种子数据、任务表
  domain/            领域模型（Pydantic）
web/                 无构建前端：index.html / styles.css / app.js + 4 个 ES 模块
scripts/             运维与评测脚本：下模型（含 BGE）/ 注册声纹 / 感知冒烟 / 检索评测 /
                     LLM 冒烟 / 缓存预热
samples/             样例素材：2 场会议 + 4 段注册音频 + 人工标注（M2 评测基准）
model/               模型权重目录（不入库，由 scripts/download_models.py 生成）
tests/               领域 / 存储 / 检索 / 编码器 / 融合 / 节点 / 图 / LLM / 结构化输出 /
                     纪要 / 多轮 / 评测 / 工具 / API / 端到端 / 前端契约 / 文档一致性
prototype/           交互原型（界面基线，见其 README）
docs/                产品设计文档与技术方案
```

---

## 实现要点

**意图路由可断言。** `app/agent/routing.py` 用规则表把输入映射成
`(intent, answer_mode)` 二元组，测试里逐条断言（约束 C1）。
模型接入后规则仍然作为兜底，路由结论不因换模型而变化。

**拒答先于生成。** 检索层先算「查询实词的覆盖率」，
低于阈值直接返回「未找到」，不把判断权交给模型（《技术方案》7.4）。
M1 的口径：命中片段覆盖率 < 0.34 即拒答。

**证据链是数据结构，不是展示格式。** 每条结论的 `evidence` 存片段 ID，
装配阶段用 `AnswerContext` 解析成 `Citation`，解析不到就不产生引用；
合成之后还有一道 `verify_evidence` 复核，引用全丢的结论会被标记为待确认。

**人工修改与模型生成分离。** 纪要正文存 `minutes.payload`，
待确认区的人工操作写 `action_item_overrides`，读取时合并。
因此重新生成纪要不会覆盖人工确认的结果（《技术方案》6.2）。
模型置信度与人工确认标记（`confidence` / `confirmed`）分开记录，
事后仍然看得出某条当初为什么被拦下来。

**对话与音频任务共用一套事件契约。** `POST /api/chat` 支持 JSON 与 SSE，
`GET /api/tasks/{id}/events` 独立推送音频阶段进度，
事件名（`stage` / `delta` / `citation` / `result` / `done` / `error`）与文档 11 节一致。

**编排节点的耗时进 trace。** 每次回答都记录 route / assemble / verify_evidence / persist
四步的耗时与结论，既是调试依据，也是 M5 评测数据的来源。

---

## 与文档的显式偏差

1. **片段与待办的 ID 是会议内编号**（`s1`、`a1`），全局定位用
   `(meeting_id, id)`；对外暴露的待办 ID 形如 `m_20260911:a1`。
   这样接口路径不需要额外的会议参数。
2. **`owner_source` 增加了两个取值**：`missing`（会上未指定）与
   `confirmed`（人工补录），它们是人在回路流程里真实存在的状态。
3. **前端拆成了 5 个 ES 模块**（`web/*.js`）而不是单个 `app.js`，
   仍然无构建、无框架，`index.html` 只引入 `app.js` 一个入口。
4. **`MeetingDetail` 是给界面用的聚合视图**，一次请求返回会议、参会人、片段、
   纪要条目与待确认清单；工具契约（`get_minutes` 等）保持独立，不被界面需求牵着走。
5. **`GET /api/conversations/{id}/export`** 是文档接口表之外新增的对话记录导出，
   对应产品文档 5.5 的「对话记录导出」。

## 后续里程碑

| 阶段 | 要做的事 | 落在哪 |
| --- | --- | --- |
| M2 感知 ✅ | 接入真实转写与声纹，`offline.py` 降级为测试后端；实测窗口级准确率 93.75%、宏平均召回 92.86%、RTF 0.09（GPU）/ 0.48（CPU） | `app/perception/`、`scripts/` |
| M3 编排与检索 ✅ | LangGraph 状态图（路由/澄清/拒答/重试）、向量召回 + RRF + 双闸拒答、LLM 合成与纪要生成；实测 Recall@8 1.000 / MRR 0.881 / 拒答正确率 1.000 → 计划见 [`docs/M3-编排与检索阶段实施计划.md`](docs/M3-编排与检索阶段实施计划.md)，数字见 [`docs/M3-编排与检索评测报告.md`](docs/M3-编排与检索评测报告.md) | `app/agent/`、`app/retrieval/` |
| M4 协同 ✅ | checkpointer + interrupt 支撑待确认区挂起恢复、证据回跳、override 完整交互 → 计划见 [`docs/M4-协同阶段实施计划.md`](docs/M4-协同阶段实施计划.md)，结果见 [`docs/M4-协同阶段验收报告.md`](docs/M4-协同阶段验收报告.md) | `app/agent/`、`web/` |
| M5 评测 ✅ | 语料 5 场 / 63 片段（3 场样例 + 2 场真实录音）、标注集（70 检索 / 34 路由 / 29 问答 / 5 场纪要金标）、12 项指标、重排消融（MRR +14.1%）、Langfuse 自托管验证、一键复现 → 计划见 [`docs/M5-评测阶段实施计划.md`](docs/M5-评测阶段实施计划.md)，数字与缺口见 [`docs/M5-评测报告.md`](docs/M5-评测报告.md) | `app/eval/`、`scripts/`、`docs/` |
