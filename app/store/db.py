"""SQLite 连接、建表与迁移。

迁移用 `PRAGMA user_version` 做版本号，逐条叠加：
v1 骨架（M1）、v2 声纹与任务（M2）、v3 片段向量与对话 trace（M3）。

每一项要么是一段 SQL（`executescript`），要么是一个函数——因为 v3 里的
`ALTER TABLE conversations ADD COLUMN summary` 需要先判断列是否存在，
SQLite 没有 `ADD COLUMN IF NOT EXISTS`。
"""

from __future__ import annotations

import sqlite3
from typing import Callable
from pathlib import Path

from app.config import Settings

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meetings(
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    date        TEXT NOT NULL,
    time        TEXT,
    duration    REAL NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'processed',
    audio_file  TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS participants(
    meeting_id       TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    speaker_id       TEXT NOT NULL,
    name             TEXT NOT NULL,
    role             TEXT,
    speaking_seconds REAL NOT NULL DEFAULT 0,
    embedding        BLOB,
    PRIMARY KEY(meeting_id, speaker_id)
);

CREATE TABLE IF NOT EXISTS segments(
    meeting_id          TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    id                  TEXT NOT NULL,
    start               REAL NOT NULL,
    end                 REAL NOT NULL,
    speaker_id          TEXT,
    speaker_confidence  REAL NOT NULL DEFAULT 0,
    text                TEXT NOT NULL,
    text_tokens         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(meeting_id, id)
);

-- 中文检索必须是「jieba 预分词 + FTS5」：unicode61 不切分中日韩文字，
-- 直接索引原文会让整句变成一个 token（《技术方案》6.2）。
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text_tokens,
    content='segments',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS minutes(
    meeting_id     TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    payload        TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations(
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages(
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    evidence        TEXT NOT NULL DEFAULT '[]',
    answer          TEXT,
    created_at      TEXT NOT NULL
);

-- 人工修改单独存表：重新生成纪要时以 override 为准，
-- 否则一次重算就把人工确认的结果覆盖掉了（《技术方案》6.2）。
CREATE TABLE IF NOT EXISTS action_item_overrides(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL,
    item_id    TEXT NOT NULL,
    status     TEXT,
    owner      TEXT,
    due        TEXT,
    resolution TEXT,
    note       TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(meeting_id, item_id)
);

CREATE TABLE IF NOT EXISTS llm_cache(
    prompt_hash   TEXT PRIMARY KEY,
    model         TEXT NOT NULL,
    response      TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost          REAL NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_segments_meeting ON segments(meeting_id);
CREATE INDEX IF NOT EXISTS idx_segments_speaker ON segments(meeting_id, speaker_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_overrides_meeting ON action_item_overrides(meeting_id);
"""

SCHEMA_V2 = """
-- 全局发言人（声纹库）：participants 是「某场会议的参会人」，
-- speakers 是「一个人」，两者分开存才能跨会议复用声纹（《技术方案》5.3）。
CREATE TABLE IF NOT EXISTS speakers(
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    role       TEXT,
    encoder    TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    centroid   BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 注册音频切窗后得到的多枚向量。匹配用的是「每人 top-k 相似度均值」，
-- 因此必须存矩阵而不只是质心（这是参考实现的关键细节）。
CREATE TABLE IF NOT EXISTS speaker_embeddings(
    speaker_id TEXT NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    PRIMARY KEY(speaker_id, idx)
);

-- 音频任务状态落库：M1 放在进程内存里，断流即中断、重启即丢失。
CREATE TABLE IF NOT EXISTS tasks(
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    path        TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,
    stage_index INTEGER NOT NULL DEFAULT 0,
    stages      TEXT NOT NULL,
    meeting_id  TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""

SCHEMA_V3 = """
-- 片段向量：与 segments 分表存。segments_fts 是 content='segments' 的外部内容表，
-- 对内容表做列变更会牵扯索引重建；独立一张表既不碰 FTS，也能让同一片段
-- 在不同编码器下的向量并存（换模型后旧向量不参与召回）。
CREATE TABLE IF NOT EXISTS segment_embeddings(
    meeting_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    encoder    TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(meeting_id, segment_id, encoder)
);

-- 每轮回答的 trace：节点耗时、模型、token、费用。
-- 它同时是调试依据、成本看板与 M5 评测的数据源（Langfuse 留 M5，决策 ⑨）。
CREATE TABLE IF NOT EXISTS chat_traces(
    id              TEXT PRIMARY KEY,
    conversation_id TEXT,
    intent          TEXT NOT NULL,
    answer_mode     TEXT NOT NULL,
    route_source    TEXT,
    nodes           TEXT NOT NULL,
    model           TEXT,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cost            REAL NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_embeddings_meeting ON segment_embeddings(meeting_id);
CREATE INDEX IF NOT EXISTS idx_traces_conversation ON chat_traces(conversation_id);
CREATE INDEX IF NOT EXISTS idx_traces_created ON chat_traces(created_at);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """幂等加列：SQLite 不支持 `ADD COLUMN IF NOT EXISTS`，只能先问 PRAGMA。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}
    if column not in existing:
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, ddl))


def _migrate_v3(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_V3)
    # 会话摘要：多轮上下文压缩的落点（M3 第 5.7 节）
    _ensure_column(conn, "conversations", "summary", "TEXT")
    # 任务的降级说明：模型不可用、纪要某段抽取失败都记在这里（不是失败）
    _ensure_column(conn, "tasks", "notes", "TEXT")


# 一个迁移项要么是 SQL 脚本，要么是函数（需要条件判断时用函数）
MigrationStep = str | Callable[[sqlite3.Connection], None]

MIGRATIONS: list[tuple[int, MigrationStep]] = [
    (1, SCHEMA_V1),
    (2, SCHEMA_V2),
    (3, _migrate_v3),
]


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开连接。行工厂用 sqlite3.Row，仓储层直接按列名取值。

    `check_same_thread=False` 是必要的：FastAPI 把同步依赖与同步路由都丢进
    线程池执行，两者不一定落在同一个工作线程上，而默认的
    `check_same_thread=True` 会直接抛 ProgrammingError。
    这里仍然是「一个请求一条连接」，串行使用，因此关掉该检查是安全的
    （唯一不能做的是把同一条连接并发共享给多个线程）。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL：M2 起后台 worker 与请求线程会同时读写同一个库文件，
    # 默认的 rollback journal 会让两边互相等锁。
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """按序补齐迁移，返回最终版本号。重复调用是幂等的。"""
    current = schema_version(conn)
    for version, step in MIGRATIONS:
        if version > current:
            if callable(step):
                step(conn)
            else:
                conn.executescript(step)
            conn.execute("PRAGMA user_version = %d" % version)
            current = version
    conn.commit()
    return current


def open_db(settings: Settings) -> sqlite3.Connection:
    """开库并保证表结构就绪。"""
    settings.ensure_dirs()
    conn = connect(settings.db_path)
    migrate(conn)
    return conn
