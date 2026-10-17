from src.api import main
from collections.abc import Iterator
from threading import Event

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def authenticated_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    main.app.dependency_overrides[main.current_user] = lambda: {"user_id": 7, "username": "ana"}
    monkeypatch.setattr(main, "_list_pending_uploads", lambda owner_id: [])
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.pop(main.current_user, None)


def test_login_page_redirects_to_home(client: TestClient):
    response = client.get("/login-page", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/"


def test_home_renders_login_and_signup_link(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    assert 'action="/login"' in response.text
    assert 'name="user_id"' in response.text
    assert 'name="username"' in response.text
    assert 'href="/signup-page"' in response.text


def test_signup_page_renders_creation_form(client: TestClient):
    response = client.get("/signup-page")

    assert response.status_code == 200
    assert 'id="signup-form"' in response.text
    assert 'name="username"' in response.text
    assert 'name="email"' in response.text
    assert 'fetch("/create/users/v2"' in response.text


def test_home_redirects_authenticated_user_to_upload(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    monkeypatch.setattr(main, "validar_token", lambda token: {"user_id": 7, "username": "ana"})

    response = client.get("/", cookies={"access_token": "valid-token"}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/upload-request"


def test_signup_page_redirects_authenticated_user_to_upload(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    monkeypatch.setattr(main, "validar_token", lambda token: {"user_id": 7, "username": "ana"})

    response = client.get(
        "/signup-page",
        cookies={"access_token": "valid-token"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/upload-request"


def test_home_clears_invalid_access_cookie(client: TestClient):
    response = client.get("/", cookies={"access_token": "invalid-token"})

    assert response.status_code == 200
    assert 'action="/login"' in response.text
    assert 'access_token=""' in response.headers["set-cookie"]


def test_upload_request_renders_upload_form_and_logout(authenticated_client: TestClient):
    response = authenticated_client.get("/upload-request")

    assert response.status_code == 200
    assert 'action="/upload"' in response.text
    assert 'action="/logout"' in response.text
    assert 'enctype="multipart/form-data"' in response.text
    assert 'target="upload-sidebar-frame"' in response.text
    assert 'name="upload-sidebar-frame"' in response.text
    assert 'id="instant-upload-list"' in response.text
    assert 'addEventListener("submit"' in response.text
    assert 'className = "queue-item queue-item-queued"' in response.text
    assert 'className = "queue-item-heading"' in response.text
    assert 'className = "queue-progress"' in response.text


def test_login_sets_access_cookie(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    monkeypatch.setattr(main, "_require_all_users", lambda: [{"id": 7, "username": "ana"}])
    monkeypatch.setattr(main, "gerar_ou_validar_token", lambda **kwargs: {"token": "valid-token"})

    response = client.post(
        "/login",
        data={"user_id": "7", "username": "ana"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/upload-request"
    assert "access_token=valid-token" in response.headers["set-cookie"]


def test_logout_clears_access_cookie(client: TestClient):
    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert 'access_token=""' in response.headers["set-cookie"]


def test_create_user_api_creates_account(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    monkeypatch.setattr(main, "execute", lambda query, params: None)
    monkeypatch.setattr(
        main,
        "query_one",
        lambda query, params: {
            "id": 8,
            "username": "bia",
            "email": "bia@example.com",
        },
    )

    response = client.post(
        "/create/users/v2",
        json={"username": "bia", "email": "bia@example.com"},
    )

    assert response.status_code == 201
    assert response.json()["username"] == "bia"


def test_upload_is_accepted_before_queue_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: TestClient,
    tmp_path,
):
    worker_started = Event()
    release_worker = Event()

    def store_file(file_object, owner_id, file_name, file_id, progress_callback):
        assert file_object.read() == b"0123456789"
        assert owner_id == 7
        assert file_name == "dados.bin"
        assert file_id == 42
        progress_callback(5, 10)
        worker_started.set()
        release_worker.wait(timeout=5)

    monkeypatch.setattr(main, "_create_upload_record", lambda owner_id, name, size: 42)
    monkeypatch.setattr(main, "SendFile", store_file)
    monkeypatch.setattr(main, "UPLOAD_STAGING_DIR", tmp_path)

    try:
        response = authenticated_client.post(
            "/upload",
            files={"arquivo": ("dados.bin", b"0123456789", "application/octet-stream")},
        )

        assert response.status_code == 202
        assert "dados.bin" in response.text
        assert 'class="queue-progress"' in response.text
        assert 'http-equiv="refresh" content="1;url=/uploads/sidebar"' in response.text
        assert worker_started.wait(timeout=1)
    finally:
        release_worker.set()


def test_upload_sidebar_resumes_pending_upload(
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: TestClient,
    tmp_path,
):
    worker_started = Event()
    release_worker = Event()
    (tmp_path / "42.upload").write_bytes(b"retomar")

    def store_file(file_object, owner_id, file_name, file_id, progress_callback):
        assert file_object.read() == b"retomar"
        assert owner_id == 7
        assert file_name == "retomado.bin"
        assert file_id == 42
        worker_started.set()
        release_worker.wait(timeout=5)

    monkeypatch.setattr(
        main,
        "_list_pending_uploads",
        lambda owner_id: [{"id": 42, "owner_id": 7, "file_name": "retomado.bin"}],
    )
    monkeypatch.setattr(main, "SendFile", store_file)
    monkeypatch.setattr(main, "UPLOAD_STAGING_DIR", tmp_path)

    try:
        response = authenticated_client.get("/uploads/sidebar")

        assert response.status_code == 200
        assert "retomado.bin" in response.text
        assert worker_started.wait(timeout=1)
    finally:
        release_worker.set()


def test_file_list_uses_authenticated_owner(
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: TestClient,
):
    requested_owners = []
    monkeypatch.setattr(main, "_require_all_files", lambda owner_id: requested_owners.append(owner_id) or [])

    response = authenticated_client.get("/read-all/files/v2/")

    assert response.status_code == 200
    assert requested_owners == [7]
    assert 'action="/logout"' in response.text


def test_file_list_requires_authentication(client: TestClient):
    response = client.get("/read-all/files/v2/")

    assert response.status_code == 401


def test_file_lookup_is_scoped_to_authenticated_owner(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def query_one(query, params):
        captured["query"] = query
        captured["params"] = params
        return {"id": 42}

    monkeypatch.setattr(main, "query_one", query_one)

    assert main.require_file(42, 7) == {"id": 42}
    assert "owner_id = %s" in captured["query"]
    assert captured["params"] == (42, 7)


def test_file_download_streams_attachment_and_supports_ranges(
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: TestClient,
):
    file_content = b"0123456789"
    monkeypatch.setattr(
        main,
        "require_file",
        lambda file_id, owner_id: {
            "id": file_id,
            "file_name": "relatorio final.txt",
            "file_size": len(file_content),
            "status": "ready",
        },
    )
    monkeypatch.setattr(
        main,
        "iterar_intervalo",
        lambda file_id, start, end: iter((file_content[start : end + 1],)),
    )

    response = authenticated_client.get("/read/file/v2/42/download")
    partial_response = authenticated_client.get(
        "/read/file/v2/42/download",
        headers={"Range": "bytes=2-5"},
    )

    assert response.status_code == 200
    assert response.content == file_content
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''relatorio%20final.txt"
    assert response.headers["content-length"] == str(len(file_content))
    assert response.headers["accept-ranges"] == "bytes"
    assert partial_response.status_code == 206
    assert partial_response.content == b"2345"
    assert partial_response.headers["content-range"] == "bytes 2-5/10"


def test_upload_sidebar_uses_latest_chunk_progress(
    authenticated_client: TestClient,
):
    authenticated_client.app.state.upload_events.publish(
        7,
        {"id": 42, "file_name": "progresso.bin", "status": "uploading", "progress": 65},
    )

    response = authenticated_client.get("/uploads/sidebar")

    assert response.status_code == 200
    assert "progresso.bin" in response.text
    assert 'value="65"' in response.text
    assert 'http-equiv="refresh" content="1;url=/uploads/sidebar"' in response.text


def test_upload_sidebar_stops_refreshing_when_complete(
    authenticated_client: TestClient,
):
    authenticated_client.app.state.upload_events.publish(
        7,
        {"id": 42, "file_name": "pronto.bin", "status": "ready", "progress": 100},
    )

    response = authenticated_client.get("/uploads/sidebar")

    assert response.status_code == 200
    assert "pronto.bin" in response.text
    assert 'value="100"' in response.text
    assert 'http-equiv="refresh"' not in response.text


def test_upload_sidebar_requires_authentication(client: TestClient):
    response = client.get("/uploads/sidebar")

    assert response.status_code == 401


def test_upload_javascript_and_polling_routes_are_removed():
    paths = {route.path for route in main.app.routes}

    assert "/uploads/status" not in paths
    assert "/uploads/events" not in paths
    assert "/uploads/sidebar" in paths
    assert not (main.PROJECT_DIR / "src" / "static" / "upload.js").exists()


def test_upload_status_hub_is_scoped_to_owner():
    hub = main.UploadEventHub()
    own_event = {"id": 42, "file_name": "own.bin", "status": "uploading", "progress": 35}
    other_event = {"id": 84, "file_name": "other.bin", "status": "uploading", "progress": 70}

    hub.publish(7, own_event)
    hub.publish(8, other_event)
    next_event = {"id": 42, "file_name": "own.bin", "status": "ready", "progress": 100}
    hub.publish(7, next_event)

    assert hub.snapshot(7) == [next_event]
    assert hub.get(7, 42) == next_event
    assert hub.get(8, 42) is None


def test_product_routes_are_removed():
    paths = {route.path for route in main.app.routes}

    assert not any("product" in path for path in paths)
