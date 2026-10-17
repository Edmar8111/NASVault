import gzip
from collections.abc import Iterator

from pymysql.cursors import SSCursor

from .db_conn import Database
from .modulo_chuncks import CHUNK_SIZE


def parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    if range_header is None:
        return None
    if file_size <= 0 or not range_header.startswith("bytes=") or "," in range_header:
        raise ValueError("Invalid byte range")

    range_start, separator, range_end = range_header[6:].partition("-")
    if not separator:
        raise ValueError("Invalid byte range")

    try:
        if not range_start:
            suffix_size = int(range_end)
            if suffix_size <= 0:
                raise ValueError("Invalid byte range")
            return max(file_size - suffix_size, 0), file_size - 1

        start = int(range_start)
        end = int(range_end) if range_end else file_size - 1
    except ValueError as error:
        raise ValueError("Invalid byte range") from error

    if start < 0 or start >= file_size or end < start:
        raise ValueError("Invalid byte range")
    return start, min(end, file_size - 1)


def iterar_intervalo(file_id: int, start: int, end: int) -> Iterator[bytes]:
    if start < 0 or end < start:
        return

    first_chunk = start // CHUNK_SIZE
    last_chunk = end // CHUNK_SIZE
    database = Database()
    try:
        with database.connection.cursor(SSCursor) as cursor:
            cursor.execute(
                """
                SELECT c.chunk_id, c.chunk_content
                FROM arquivo_chunks AS c
                JOIN arquivos AS a ON a.id = c.arquivo_id
                WHERE c.arquivo_id = %s
                    AND c.chunk_id BETWEEN %s AND %s
                    AND a.status = 'ready'
                ORDER BY c.chunk_id
                """,
                (file_id, first_chunk, last_chunk),
            )
            while row := cursor.fetchone():
                chunk_id, compressed_chunk = row
                chunk = gzip.decompress(compressed_chunk)
                chunk_start = chunk_id * CHUNK_SIZE
                slice_start = max(start - chunk_start, 0)
                slice_end = min(end - chunk_start + 1, len(chunk))
                if slice_start < slice_end:
                    yield chunk[slice_start:slice_end]
    finally:
        database.close()