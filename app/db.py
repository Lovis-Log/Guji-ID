"""使用短连接；每次连接都开启外键，写操作显式控制事务。"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TABLES = ("people", "aliases", "sources", "evidences")


def default_db_path() -> Path:
    configured = os.getenv("GUJI_DB_PATH")
    return Path(configured).expanduser().resolve() if configured else PROJECT_ROOT / "data" / "guji.db"


@contextmanager
def connect_db(path: str | Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def init_db(path: str | Path) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with connect_db(db_path) as connection:
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + schema + "\nCOMMIT;")
        except sqlite3.Error:
            if connection.in_transaction:
                connection.rollback()
            raise

