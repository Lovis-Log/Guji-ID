from fastapi.testclient import TestClient

from app.db import connect_db
from app.importer import import_batch
from app.main import create_app


def test_startup_home_health_static_and_template_downloads(tmp_path):
    path = tmp_path / "startup" / "test_only.db"
    with TestClient(create_app(path)) as client:
        assert path.exists()
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "尚未导入正式资料" in response.text
        assert 'role="search"' in response.text
        assert "暂无可公开检索的已核对人物" in response.text
        assert client.get("/health").json() == {"status": "ok", "database": "ok", "stage": 7}
        css = client.get("/static/styles.css")
        assert css.status_code == 200 and "text/css" in css.headers["content-type"]
        assert client.get("/static/app.js").status_code == 200
        for table in ("people", "aliases", "sources", "evidences"):
            template = client.get(f"/templates/{table}.csv")
            assert template.status_code == 200
            assert len(template.text.strip().splitlines()) == 1
            assert "attachment" in template.headers["content-disposition"]


def test_home_counts_are_live_and_isolated(db_path, tmp_path, batch, write_batch):
    import_batch(write_batch(batch), db_path)
    with TestClient(create_app(db_path)) as client:
        page = client.get("/").text
        assert "<strong>3</strong>" in page
        assert "其中已核对 1 条" in page
        assert "尚未导入正式资料" not in page
        # 即使测试库有夹具，另外一个实例的空库仍是空库。
        with TestClient(create_app(tmp_path / "other_empty.db")) as empty_client:
            assert "尚未导入正式资料" in empty_client.get("/").text


def test_not_found_and_method_errors_are_structured(db_path):
    with TestClient(create_app(db_path)) as client:
        for path in ("/not-found", "/templates/not-a-table.csv", "/static/missing.css"):
            response = client.get(path)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "http_404"
        response = client.post("/health")
        assert response.status_code == 405
        assert response.headers.get("allow") == "GET"


def test_missing_table_makes_health_fail_without_leaking_details(db_path):
    with TestClient(create_app(db_path)) as client:
        with connect_db(db_path) as connection:
            connection.execute("DROP TABLE evidences")
        for route in ("/", "/health"):
            response = client.get(route)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "database_unavailable"
            assert "no such table" not in response.text
            assert str(db_path) not in response.text


def test_unexpected_exception_is_safe(db_path):
    application = create_app(db_path)

    @application.get("/test-only-error")
    def fail():
        raise RuntimeError("TEST-ONLY-PRIVATE-DETAIL")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/test-only-error")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
        assert "PRIVATE-DETAIL" not in response.text


def test_parameter_validation_is_structured(db_path):
    application = create_app(db_path)

    @application.get("/test-only-validation/{number}")
    def echo(number: int):
        return {"number": number}

    with TestClient(application) as client:
        response = client.get("/test-only-validation/not-an-int")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

