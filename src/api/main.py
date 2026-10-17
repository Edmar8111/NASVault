import asyncio
import logging
import os
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
import mimetypes
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, status, UploadFile, Request, Depends, Cookie
from fastapi.responses import HTMLResponse, Response, StreamingResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from .db_conn import Database, execute, query_all, query_one
from .modulo_chuncks import SendFile
from .chunck_streaming import iterar_intervalo, parse_range_header
from .utils import gerar_ou_validar_token, validar_token, medir_tempo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadJob:
    """Representa um upload temporário persistido, pronto para ser gravado em chunks."""

    file_id: int
    owner_id: int
    file_name: str
    file_path: Path


class UploadEventHub:
    """Mantem o ultimo status de cada upload em memoria, por proprietario."""

    def __init__(self) -> None:
        self._states: dict[tuple[int, int], dict] = {}

    def publish(self, owner_id: int, payload: dict) -> None:
        event = payload.copy()
        key = (owner_id, int(event["id"]))
        self._states[key] = event
        while len(self._states) > 100:
            self._states.pop(next(iter(self._states)))

    def snapshot(self, owner_id: int) -> list[dict]:
        """Retorna os status conhecidos do proprietario sem consultar o banco."""
        return [event.copy() for (event_owner, _), event in self._states.items() if event_owner == owner_id]

    def get(self, owner_id: int, file_id: int) -> dict | None:
        event = self._states.get((owner_id, file_id))
        return event.copy() if event is not None else None

    def remove(self, owner_id: int, file_id: int) -> None:
        """Remove da fila visual um upload que não pôde ser enfileirado."""
        self._states.pop((owner_id, file_id), None)


UPLOAD_STAGING_DIR = Path(
    os.environ.get(
        "UPLOAD_STAGING_DIR",
        str(Path(os.environ.get("TMPDIR", "/tmp")) / "nasvault-uploads"),
    )
)


def _stage_upload_file(source: BinaryIO, file_id: int) -> Path:
    """Persiste o arquivo recebido no volume durável antes de enfileirá-lo."""
    UPLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = UPLOAD_STAGING_DIR / f"{file_id}.uploading"
    staged_path = UPLOAD_STAGING_DIR / f"{file_id}.upload"
    source.seek(0)
    try:
        with temporary_path.open("wb") as staged_file:
            shutil.copyfileobj(source, staged_file, length=1024 * 1024)
            staged_file.flush()
            os.fsync(staged_file.fileno())
        os.replace(temporary_path, staged_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return staged_path


def _create_upload_record(owner_id: int, file_name: str, file_size: int) -> int:
    """Cria o registro de recebimento antes de adicionar o arquivo a fila."""
    database = Database()
    try:
        with database.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO arquivos (owner_id, file_name, file_size, status) VALUES (%s, %s, %s, 'uploading')",
                (owner_id, file_name, file_size),
            )
            file_id = int(cursor.lastrowid)
        database.connection.commit()
        return file_id
    except Exception:
        database.connection.rollback()
        raise
    finally:
        database.close()


def _list_pending_uploads(owner_id: int) -> list[dict]:
    """Busca no banco os uploads do usuário que aguardam processamento."""
    database = Database()
    try:
        return database.fetch_all(
            "SELECT id, owner_id, file_name FROM arquivos "
            "WHERE owner_id = %s AND status = 'uploading' ORDER BY id",
            (owner_id,),
        )
    finally:
        database.close()


async def _recover_pending_uploads(request: Request, owner_id: int) -> None:
    """Reenfileira uploads pendentes que ainda possuem o arquivo temporário."""
    recovered_owners: set[int] = request.app.state.recovered_upload_owners
    if owner_id in recovered_owners:
        return

    uploads = await run_in_threadpool(_list_pending_uploads, owner_id)
    events: UploadEventHub = request.app.state.upload_events
    queue: asyncio.Queue[UploadJob] = request.app.state.upload_queue
    for upload in uploads:
        file_id = int(upload["id"])
        file_name = str(upload["file_name"])
        if events.get(owner_id, file_id) is not None:
            continue
        file_path = UPLOAD_STAGING_DIR / f"{file_id}.upload"
        if not file_path.is_file():
            execute(
                "UPDATE arquivos SET status = 'failed' "
                "WHERE id = %s AND owner_id = %s AND status = 'uploading'",
                (file_id, owner_id),
            )
            events.publish(owner_id, {
                "id": file_id,
                "file_name": file_name,
                "status": "failed",
                "progress": 0,
                "message": "O arquivo temporário não está disponível para retomada.",
            })
            continue

        events.publish(owner_id, {
            "id": file_id,
            "file_name": file_name,
            "status": "queued",
            "progress": 0,
            "message": "Upload retomado após a reinicialização.",
        })
        queue.put_nowait(UploadJob(file_id, owner_id, file_name, file_path))
    recovered_owners.add(owner_id)


async def _process_upload_queue(
    queue: asyncio.Queue[UploadJob],
    events: UploadEventHub,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Grava cada upload em sequencia e publica o progresso de cada chunk."""
    while True:
        job = await queue.get()
        events.publish(job.owner_id, {
            "id": job.file_id,
            "file_name": job.file_name,
            "status": "uploading",
            "progress": 0,
        })
        keep_staged_file = False

        def publish_progress(processed: int, total: int) -> None:
            progress = 100 if total == 0 else min(99, int(processed * 100 / total))
            loop.call_soon_threadsafe(
                events.publish,
                job.owner_id,
                {
                    "id": job.file_id,
                    "file_name": job.file_name,
                    "status": "uploading",
                    "progress": progress,
                },
            )

        try:
            with job.file_path.open("rb") as file_object:
                await run_in_threadpool(
                    SendFile,
                    file_object,
                    job.owner_id,
                    job.file_name,
                    job.file_id,
                    publish_progress,
                )
            events.publish(job.owner_id, {
                "id": job.file_id,
                "file_name": job.file_name,
                "status": "ready",
                "progress": 100,
            })
        except asyncio.CancelledError:
            keep_staged_file = True
            raise
        except Exception:
            logger.exception("Falha ao processar upload %s", job.file_id)
            try:
                execute("UPDATE arquivos SET status = 'failed' WHERE id = %s", (job.file_id,))
            except Exception:
                logger.exception("Falha ao registrar erro do upload %s", job.file_id)
            latest = events.get(job.owner_id, job.file_id) or {}
            events.publish(job.owner_id, {
                "id": job.file_id,
                "file_name": job.file_name,
                "status": "failed",
                "progress": latest.get("progress", 0),
            })
        finally:
            if not keep_staged_file:
                try:
                    job.file_path.unlink(missing_ok=True)
                except OSError:
                    logger.exception("Falha ao remover o arquivo temporário do upload %s", job.file_id)
            queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    upload_queue: asyncio.Queue[UploadJob] = asyncio.Queue()
    upload_events = UploadEventHub()
    app.state.upload_queue = upload_queue
    app.state.upload_events = upload_events
    app.state.recovered_upload_owners = set()
    worker = asyncio.create_task(_process_upload_queue(upload_queue, upload_events, asyncio.get_running_loop()))
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


class User(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=150)


app = FastAPI(
    # docs_url='/access-docs',  # URL para a documentação Swagger
    # redoc_url=None,
    # openapi_url=None,
    title="NASVault",
    version="1.0.0", 
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:8000",
        "http://localhost:8001",
        ],  # Em produção, informe o domínio específico.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/docs", include_in_schema=False)
async def mock_docs():
    return JSONResponse(
        status_code=200,
        content={
            "message": "Documentação indisponível"
        },
    )
@app.middleware("http")
async def block_old_browsers(request: Request, call_next):
    user_agent = request.headers.get("user-agent", "")
    if "MSIE" in user_agent or "Trident" in user_agent:
        return Response(
            content="Navegadores antigos não são suportados. Por favor, atualize seu navegador.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    response = await call_next(request)
    return response



PROJECT_DIR = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=str(PROJECT_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(PROJECT_DIR / "static")), name="static")


def current_user(
    access_token: str = Cookie(default=None, alias="access_token")
):
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token de acesso não fornecido.")
    return validar_token(access_token)


def _has_valid_access_token(access_token: str | None) -> bool:
    if not access_token:
        return False
    try:
        validar_token(access_token)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            raise
        return False
    return True


@app.get("/", include_in_schema=False, response_class=HTMLResponse)
async def home(
    request: Request,
    access_token: str = Cookie(default=None, alias="access_token"),
):
    if _has_valid_access_token(access_token):
        return RedirectResponse(url="/upload-request", status_code=status.HTTP_302_FOUND)
    response = templates.TemplateResponse(request, "home.html")
    if access_token:
        response.delete_cookie(key="access_token", path="/")
    return response



@app.get('/login-page', response_class=HTMLResponse)
async def login_page(access_token: str = Cookie(default=None, alias="access_token")):
    if _has_valid_access_token(access_token):
        return RedirectResponse(url="/upload-request", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@app.get("/signup-page", response_class=HTMLResponse)
async def signup_page(
    request: Request,
    access_token: str = Cookie(default=None, alias="access_token"),
):
    if _has_valid_access_token(access_token):
        return RedirectResponse(url="/upload-request", status_code=status.HTTP_302_FOUND)
    response = templates.TemplateResponse(request, "signup_page.html")
    if access_token:
        response.delete_cookie(key="access_token", path="/")
    return response


@app.post("/login")
async def login(request: Request):
    # Lógica de login aqui
    form = await request.form()
    user_id: str = form.get("user_id")
    username: str = form.get("username")
    _users: list = _require_all_users()
    _ids: list = [str(_id['id']) for _id in _users]
    _names: list = [str(_name['username']) for _name in _users]
    print(f'{_ids=}, {_names=}')
    if user_id not in _ids or username not in _names:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas.")
    resp = gerar_ou_validar_token(user_id=user_id, username=username)
    response = RedirectResponse(url="/upload-request", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token", 
        value=resp["token"],
        # impede o javascript de acessar o cookie, aumentando a segurança contra XSS
        httponly=True,
        secure=False,  # Em produção, use True para HTTPS
        # protege contra ataques CSRF, mas pode causar problemas em alguns navegadores
        samesite="lax",  # Pode ser "strict" ou "lax
        max_age=900,  # Tempo de vida do cookie em segundos (15 minutos)
        path="/",  # O cookie será enviado para todas as rotas
    )
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse(
        url="/",
        status_code=status.HTTP_302_FOUND
    )
    response.delete_cookie(
        key="access_token",
        path="/",  # O cookie será removido de todas as rotas
        )
    return response


@app.get("/upload-request", include_in_schema=False, response_class=HTMLResponse)
async def upload_request(
    request: Request,
    user: dict = Depends(current_user),
    access_token: str = Cookie(default=None, alias="access_token")
):
    return templates.TemplateResponse(request, "request_upload.html")


def _require_user(user_id: int) -> dict:
    _query, _param = "SELECT id, username, email FROM usuario WHERE id = %s", (user_id,)
    user = query_one(_query, _param)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario nao encontrado")
    return user

@query_all
def _require_all_users(users: str="SELECT id, username, email FROM usuario") -> list[dict]:
    if not users:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuarios nao encontrados")
    return users


@query_all
def _require_all_files(owner_id: int) -> str:
    owner_id = int(owner_id)
    return (
        "SELECT id, file_name, file_size, total_chunks, file_hash, status, created_at "
        f"FROM arquivos WHERE owner_id = {owner_id} ORDER BY created_at DESC, id DESC"
    )


def require_file(file_id: int, owner_id: int) -> dict:
    file = query_one(
        "SELECT id, file_name, file_size, total_chunks, file_hash, status, created_at "
        "FROM arquivos WHERE id = %s AND owner_id = %s",
        (file_id, owner_id),
    )
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arquivo nao encontrado")
    return file




@app.get("/read-all/files/v2/")
async def read_files(request: Request, user: dict = Depends(current_user)) -> HTMLResponse:
    files = _require_all_files(int(user["user_id"]))
    return templates.TemplateResponse(
        request,
        "response_all_files.html",
        {"files": files},
    )


def _render_upload_sidebar(request: Request, owner_id: int, status_code: int = status.HTTP_200_OK) -> HTMLResponse:
    """Renderiza a fila do usuario a partir do status mantido em memoria."""
    uploads = request.app.state.upload_events.snapshot(owner_id)
    active_uploads = any(upload["status"] in {"queued", "uploading"} for upload in uploads)
    return templates.TemplateResponse(
        request,
        "upload_sidebar.html",
        {
            "uploads": uploads,
            "active_uploads": active_uploads,
        },
        status_code=status_code,
    )


@app.get("/uploads/sidebar", response_class=HTMLResponse)
async def upload_sidebar(
    request: Request,
    user: dict = Depends(current_user),
) -> HTMLResponse:
    """Retoma uploads pendentes e atualiza a fila sem recarregar a pagina de envio."""
    await _recover_pending_uploads(request, int(user["user_id"]))
    return _render_upload_sidebar(request, int(user["user_id"]))


@app.get("/read/file/v2/{id_file}", response_class=HTMLResponse)
async def read_file(
    id_file: int,
    request: Request,
    user: dict = Depends(current_user),
) -> HTMLResponse:
    file = require_file(id_file, int(user["user_id"]))
    if file["status"] != "ready":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Arquivo ainda nao esta disponivel")

    media_type, preview_kind = _identify_file_type(file)
    return templates.TemplateResponse(
        request,
        "response_file.html",
        {
            "file": file,
            "media_type": media_type,
            "preview_kind": preview_kind,
            "content_url": f"/read/file/v2/{id_file}/content",
        },
    )


@app.get("/read/file/v2/{id_file}/content")
async def read_file_content(
    id_file: int,
    request: Request,
    user: dict = Depends(current_user),
) -> Response:
    file = require_file(id_file, int(user["user_id"]))
    if file["status"] != "ready":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Arquivo ainda nao esta disponivel")

    media_type, preview_kind = _identify_file_type(file)
    if preview_kind not in {"image", "video"}:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Arquivo sem preview de midia")

    file_size = int(file["file_size"])
    range_header = request.headers.get("range")
    try:
        byte_range = parse_range_header(range_header, file_size)
    except ValueError:
        return Response(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
        )

    start, end = byte_range or (0, file_size - 1)
    content_length = max(0, end - start + 1)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(file['file_name'], safe='')}",
        "X-Content-Type-Options": "nosniff",
    }
    response_status = status.HTTP_200_OK
    if byte_range is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        response_status = status.HTTP_206_PARTIAL_CONTENT

    return StreamingResponse(
        iterar_intervalo(id_file, start, end),
        status_code=response_status,
        media_type=media_type,
        headers=headers,
    )


@app.get("/read/file/v2/{id_file}/download")
async def download_file(
    id_file: int,
    request: Request,
    user: dict = Depends(current_user),
) -> Response:
    file = require_file(id_file, int(user["user_id"]))
    if file["status"] != "ready":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Arquivo ainda nao esta disponivel")

    media_type = mimetypes.guess_type(str(file["file_name"]))[0] or "application/octet-stream"
    file_size = int(file["file_size"])
    range_header = request.headers.get("range")
    try:
        byte_range = parse_range_header(range_header, file_size)
    except ValueError:
        return Response(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
        )

    start, end = byte_range or (0, file_size - 1)
    content_length = max(0, end - start + 1)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file['file_name'], safe='')}",
        "X-Content-Type-Options": "nosniff",
    }
    response_status = status.HTTP_200_OK
    if byte_range is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        response_status = status.HTTP_206_PARTIAL_CONTENT

    return StreamingResponse(
        iterar_intervalo(id_file, start, end),
        status_code=response_status,
        media_type=media_type,
        headers=headers,
    )


@lru_cache(maxsize=512)
def _identify_file_type_cached(file_id: int, file_name: str, file_size: int, file_hash: str) -> tuple[str, str]:
    header = b""
    if file_size:
        header = b"".join(iterar_intervalo(file_id, 0, min(file_size - 1, 511)))

    if header.startswith(bytes.fromhex("89504e470d0a1a0a")):
        return "image/png", "image"
    if header.startswith(bytes.fromhex("ffd8ff")):
        return "image/jpeg", "image"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "image"
    if header.startswith(b"BM"):
        return "image/bmp", "image"
    if header.startswith((bytes.fromhex("49492a00"), bytes.fromhex("4d4d002a"))):
        return "image/tiff", "image"
    if header.startswith(bytes.fromhex("00000100")):
        return "image/x-icon", "image"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp", "image"

    if header.startswith(bytes.fromhex("1a45dfa3")):
        guessed_type = mimetypes.guess_type(file_name)[0]
        if guessed_type and guessed_type.startswith("audio/"):
            return guessed_type, "other"
        return (guessed_type if guessed_type and guessed_type.startswith("video/") else "video/webm"), "video"
    if header.startswith(b"RIFF") and header[8:12] == b"AVI ":
        return "video/x-msvideo", "video"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in {b"avif", b"avis"}:
            return "image/avif", "image"
        if brand in {b"M4A ", b"M4B ", b"M4P "}:
            return mimetypes.guess_type(file_name)[0] or "audio/mp4", "other"
        return ("video/quicktime" if brand == b"qt  " else "video/mp4"), "video"
    if header.startswith((bytes.fromhex("000001ba"), bytes.fromhex("000001b3"))):
        return "video/mpeg", "video"

    guessed_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    if guessed_type.startswith(("image/", "video/")):
        return "application/octet-stream", "other"
    return guessed_type, "other"


def _identify_file_type(file: dict) -> tuple[str, str]:
    return _identify_file_type_cached(
        int(file["id"]),
        str(file["file_name"]),
        int(file["file_size"]),
        str(file["file_hash"]),
    )


@app.post("/create/users/v2", status_code=status.HTTP_201_CREATED)
async def create_user(user: User) -> dict:
    try:
        _query_insert, _param_insert = "INSERT INTO usuario (username, email) VALUES (%s, %s)", (user.username, user.email)
        print(f"{_query_insert=}")
        execute(_query_insert, _param_insert)
        _query_read, _param_read = "SELECT id, username, email, data_criacao FROM usuario WHERE email = %s", (user.email,)
        print(f"{_query_read=}")
        return query_one(
            _query_read,
            _param_read
        ) or {}
    except Exception as _:
        if "Duplicate entry" in str(_):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Usuario: '{user.username}' ja existe")
        raise Exception(f"Erro ao criar usuario {_}")

@app.get("/read/user/v2/{id}")
async def read_user(id: int) -> dict:
    return _require_user(id)


@app.get("/read-all/user/v2")
async def read_all_users(request: Request) -> list[dict]:
    users = _require_all_users()
    return templates.TemplateResponse(request, "teste.html", {"users": users})
    


@app.put("/update/user/v2/{id}", status_code=status.HTTP_200_OK)
async def update_user(id: int, user: User) -> dict:
    _require_user(id)
    _query_update, _param_update = "UPDATE usuario SET username = %s, email = %s WHERE id = %s", (user.username, user.email, id)
    execute(
        _query_update,
        _param_update
    )
    return _require_user(id)


@app.delete("/del/user/v2/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(id: int) -> None:
    _require_user(id)
    execute("DELETE FROM usuario WHERE id = %s", (id,))
    
@medir_tempo
@app.post("/upload", response_class=HTMLResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_arquivo(
    arquivo: UploadFile, 
    request: Request,
    user: dict = Depends(current_user)
    ):
    """Recebe o arquivo, persiste-o temporariamente e enfileira a gravação em partes."""
    print("Recebendo upload...")
    nome = Path(arquivo.filename or "upload.bin").name
    arquivo.file.seek(0, os.SEEK_END)  # Posiciona o cursor no fim do arquivo para medir seu tamanho.
    tamanho = arquivo.file.tell()  # Obtém o tamanho do arquivo em bytes na posição atual do cursor.
    arquivo.file.seek(0)  # Retorna o cursor ao início para que o arquivo possa ser lido/enfileirado.
    owner_id = int(user["user_id"])
    queued_path = None
    file_id = None
    try:
        file_id = await run_in_threadpool(_create_upload_record, owner_id, nome, tamanho)
        queued_upload = {
            "id": file_id,
            "file_name": nome,
            "status": "queued",
            "progress": 0,
            "message": "Arquivo recebido e adicionado à fila.",
        }
        request.app.state.upload_events.publish(owner_id, queued_upload)
        queued_path = await run_in_threadpool(_stage_upload_file, arquivo.file, file_id)
        request.app.state.upload_queue.put_nowait(UploadJob(file_id, owner_id, nome, queued_path))
    except Exception:
        if queued_path is not None:
            queued_path.unlink(missing_ok=True)
        if file_id is not None:
            execute("DELETE FROM arquivos WHERE id = %s AND owner_id = %s", (file_id, owner_id))
            request.app.state.upload_events.remove(owner_id, file_id)
        raise
    finally:
        await arquivo.close()

    return _render_upload_sidebar(request, owner_id, status.HTTP_202_ACCEPTED)
    
    
    