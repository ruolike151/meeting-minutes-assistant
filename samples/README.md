# 样例素材（M2 感知层）

来自实习项目《员工声纹识别 Demo》的四人会议素材，用于 M2 的冒烟、调参与评测。
音频与标注都随仓库分发，`model/` 里的权重不入库（见根目录 `.gitignore`）。

## 内容

| 文件 | 用途 | 规格 |
| --- | --- | --- |
| `audio/meeting.mp3` | 会议主样例（单人依次汇报） | 86.5 秒，中文，4 人 |
| `audio/meeting2.mp3` | 长会议样例（用于压力与稳定性验证） | 358.0 秒，中文，4 人 |
| `audio/A_solo.mp3` … `D_solo.mp3` | 声纹注册音频 | 17.8–22.4 秒 / 人 |
| `annotations/meeting.demo.json` | `meeting.mp3` 的人工标注（逐段说话人 + 正文 + 时间戳） | 7 段 |
| `annotations/meeting2.demo.json` | `meeting2.mp3` 的人工标注 | 24 段 |
| `annotations/speakers.json` | 标签 A–D ↔ 角色名 ↔ 注册音频 的映射 | 4 人 |

## 两点说明

1. **标注既是"标准答案"也是评测基准。** 参考实现用它做人工校正（把自动结果改成正确的），
   M2 反过来用它当 ground truth：**不喂给模型**，只在评测脚本里比对，衡量自动链路的真实水平。
2. **`GROUP` 表示多人合说。** 这是真实会议里存在、但当前领域模型（`Segment`）没有字段承载的情况。
   M2 的处理口径是：合说窗口的声纹匹配不确定，按低置信度处理，评测时把 `GROUP` 段排除在准确率之外
   （与参考实现同口径），单独统计。

## 跑法

```powershell
# 1. 下载权重到 model/（约 600MB：small 转写模型 + ECAPA 声纹模型）
.\meeting_assistant\Scripts\python.exe scripts\download_models.py --sizes small

# 2. 用样例音频注册 4 个发言人声纹
.\meeting_assistant\Scripts\python.exe scripts\enroll_samples.py

# 3. 跑真实感知链路（转写 + 声纹匹配）
.\meeting_assistant\Scripts\python.exe scripts\smoke_perception.py --meeting samples\audio\meeting.mp3

# 4. 出评测报告（窗口级准确率、召回率、CER/WER、RTF）
.\meeting_assistant\Scripts\python.exe scripts\eval_perception.py
```