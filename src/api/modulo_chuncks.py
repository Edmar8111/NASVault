import gzip
import math
import hashlib
from collections.abc import Iterator, Callable
from typing import BinaryIO, Optional

from pymysql.cursors import SSCursor

from .db_conn import Database
from .utils import medir_tempo

CHUNK_SIZE = 8 * 1024 * 1024
COMMIT_EVERY_CHUNKS = 8


def conectar() -> Database:
    """Abre a conexão MySQL configurada pelas variáveis DB_* do Compose."""
    return Database()

@medir_tempo
def salvar_arquivo(
    file_object: BinaryIO,
    owner_id: int,
    file_name: Optional[str] = None,
    file_id: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> dict:
    """Armazena o arquivo em chunks sem carregá-lo inteiro na memória."""
    file_object.seek(0, 2)
    file_size = file_object.tell()
    file_object.seek(0)
    all_chunks = math.ceil(file_size / CHUNK_SIZE)
    _all_chunks = (file_size // CHUNK_SIZE - 1) + 1
    print(f'{_all_chunks=}')
    database = conectar()
    connection = database.connection
    existing_file_id = file_id is not None
    file_hash = hashlib.sha256()
    total_chunks = 0
    processed_bytes = 0

    try:
        if file_id is None:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO arquivos (file_name, file_size, owner_id) VALUES (%s, %s, %s)",
                    (file_name or "upload.bin", file_size, owner_id),
                )
                file_id = cursor.lastrowid
        else:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM arquivo_chunks WHERE arquivo_id = %s",
                    (file_id,),
                )
                cursor.execute(
                    "UPDATE arquivos SET file_size = %s, status = 'uploading' "
                    "WHERE id = %s AND owner_id = %s",
                    (file_size, file_id, owner_id),
                )
        connection.commit()
        while chunk := file_object.read(CHUNK_SIZE):
            print(f"Processando chunk {total_chunks + 1} de {all_chunks} ({len(chunk)} bytes)")
            file_hash.update(chunk)
            compressed_chunk = gzip.compress(chunk, compresslevel=1)
            chunk_hash = hashlib.sha256(chunk).hexdigest()

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO arquivo_chunks
                        (arquivo_id, chunk_id, chunk_hash, chunk_content)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (file_id, total_chunks, chunk_hash, compressed_chunk),
                )

            total_chunks += 1
            processed_bytes += len(chunk)
            if progress_callback is not None:
                progress_callback(processed_bytes, file_size)
            if total_chunks % COMMIT_EVERY_CHUNKS == 0:
                connection.commit()

        final_hash = file_hash.hexdigest()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE arquivos
                SET total_chunks = %s, file_hash = %s, status = 'ready'
                WHERE id = %s
                """,
                (total_chunks, final_hash, file_id),
            )
        connection.commit()

        return {
            "id": file_id,
            "file_name": file_name or "upload.bin",
            "file_size": file_size,
            "total_chunks": total_chunks,
            "file_hash": final_hash,
        }
    except Exception:
        connection.rollback()
        if file_id is not None and existing_file_id:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM arquivo_chunks WHERE arquivo_id = %s", (file_id,))
                cursor.execute("UPDATE arquivos SET status = 'failed' WHERE id = %s", (file_id,))
            connection.commit()
        elif file_id is not None:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM arquivos WHERE id = %s", (file_id,))
            connection.commit()
        raise
    finally:
        database.close()


def buscar_arquivo(file_id: int) -> Optional[dict]:
    """Busca os metadados de um arquivo finalizado pelo identificador."""
    database = conectar()
    try:
        with database.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, file_name, file_size, total_chunks, file_hash, created_at
                FROM arquivos
                WHERE id = %s AND status = 'ready'
                """,
                (file_id,),
            )
            return cursor.fetchone()
    finally:
        database.close()


def iterar_chunks(file_id: int) -> Iterator[bytes]:
    """Lê e descomprime um chunk por vez, sem buscar o arquivo inteiro."""
    database = conectar()
    try:
        with database.connection.cursor(SSCursor) as cursor:
            cursor.execute(
                """
                SELECT c.chunk_content
                FROM arquivo_chunks AS c
                JOIN arquivos AS a ON a.id = c.arquivo_id
                WHERE c.arquivo_id = %s AND a.status = 'ready'
                ORDER BY c.chunk_id
                """,
                (file_id,),
            )
            while row := cursor.fetchone():
                yield gzip.decompress(row[0])
    finally:
        database.close()


class SendFile:
    def __init__(
        self,
        file_object: BinaryIO,
        owner_id: int,
        file_name: Optional[str] = None,
        file_id: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ):
        salvar_arquivo(file_object, owner_id, file_name, file_id, progress_callback)

