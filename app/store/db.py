"""SQLite 连接、建表与迁移。

迁移用 `PRAGMA user_version` 做版本号，逐条叠加。M1 只有一个版本，
但接口先按「可叠加」的方式写，后续加表不必手工改库。
"""

from __future__ import annotations

import sqlite3
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

MIGRATIONS: list[tuple[int, str]] = [(1, SCHEMA_V1)]


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
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """按序补齐迁移，返回最终版本号。重复调用是幂等的。"""
    current = schema_version(conn)
    for version, script in MIGRATIONS:
        if version > current:
            conn.executescript(script)
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
