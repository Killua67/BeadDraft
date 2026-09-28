"""
数据库连接（SQLAlchemy 2.0 + SQLite）。

- Base：所有 ORM 模型的基类
- get_db：FastAPI 依赖，每个请求一个会话，请求结束自动关闭
- init_db：启动时建表（表已存在则跳过），并补充已有表中缺少的新字段（轻量迁移）
"""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

settings.data_dir.mkdir(parents=True, exist_ok=True)

# check_same_thread=False：FastAPI 会在线程池中使用连接，SQLite 需要关闭同线程检查
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """ORM 模型基类。"""


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：提供数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 后续版本新增的字段：create_all 不会修改已存在的表，需要手动 ALTER TABLE 补上。
# 格式：{表名: {字段名: SQLite 字段定义}}，新增字段时在这里登记（字段含义见 models.py 中的 comment）
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "patterns": {"done_codes_json": "TEXT NOT NULL DEFAULT '[]'"},
}


def migrate(bind: Engine) -> list[str]:
    """给已存在的表补充 ADDED_COLUMNS 中缺少的字段，返回新增的「表.字段」列表。"""
    added = []
    inspector = inspect(bind)
    with bind.begin() as conn:
        for table, columns in ADDED_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    added.append(f"{table}.{name}")
                    logger.info("数据库迁移：%s 表新增字段 %s", table, name)
    return added


def init_db() -> None:
    """创建所有数据表，并补充旧数据库缺少的字段。"""
    from app import models  # noqa: F401  确保模型已注册到 Base.metadata

    Base.metadata.create_all(bind=engine)
    migrate(engine)
    logger.info("数据库初始化完成：%s", settings.database_url)
