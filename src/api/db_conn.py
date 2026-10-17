import os
from collections.abc import Sequence
from typing import Any
from functools import wraps, lru_cache
import pymysql
from pymysql.cursors import DictCursor

class Database:
    def __init__(self) -> None:
        self.connection = pymysql.connect(
            host=os.environ.get("DB_HOST", "0.0.0.0"),
            port=int(os.environ.get("DB_PORT", "3306")),
            user=os.environ.get("DB_USER", "app_user"),
            password=os.environ.get("DB_PASSWORD", "app_pass"),
            database=os.environ.get("DB_NAME", "database"),
            charset="utf8mb4",
            autocommit=False,
            cursorclass=DictCursor,
            # connect_timeout=10,
            # read_timeout=30,
            # write_timeout=30,
        )

    @lru_cache(maxsize=1)
    def execute(self, query: str, params: Sequence[Any] = ()) -> int:
        try:
            with self.connection.cursor() as cursor:
                affected = cursor.execute(query, params)
            self.connection.commit()
            return affected
        except Exception:
            self.connection.rollback()
            raise

    @lru_cache(maxsize=1)
    def fetch_one(self, query: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()

    @lru_cache(maxsize=1)
    def fetch_all(self, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            return list(cursor.fetchall())

    def close(self) -> None:
        self.connection.close()

def query_one(query: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    database = Database()
    try:
        return database.fetch_one(query, params)
    finally:
        database.close()
        
        
def _query_all(query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    database = Database()
    try:
        return database.fetch_all(query, params)
    finally:
        database.close()

def query_all(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        print(f"Executando query_all com função: {f.__name__} e argumentos: {args}, {kwargs}")
        query = f(*args, **kwargs)
        print(f"Query SQL: {query}")
        _data = _query_all(query)
        print(f"Resultado: {_data}")
        return _data
    return wrapper

def execute(query: str, params: Sequence[Any] = ()) -> int:
    database = Database()
    try:
        return database.execute(query, params)
    finally:
        database.close()