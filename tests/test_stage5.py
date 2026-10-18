"""Synthetic fixtures only. Formal-data checks live in formal_acceptance.py."""
import pytest
from fastapi.testclient import TestClient

from app.catalog import get_person, search_examples, search_people
from app.db import connect_db
from app.main import create_app
from test_catalog import public_db


def test_identity_comparison_uses_only_approved_public_direct_evidence(public_db):
    person = get_person(public_db, "TEST-P1")
    assert {e["alias_id"] for e in person["identity_evidences"]} == {"TEST-A1", "TEST-A3"}
    with connect_db(public_db) as c:
        c.execute("UPDATE evidences SET original_text='TEST ONLY PRIVATE UNAPPROVED' WHERE alias_id='TEST-A5'")
    result = search_people(public_db, "仅测试同名人物")[0]
    assert all("PRIVATE UNAPPROVED" not in e["original_text"] for e in result["identity_evidences"])
    with TestClient(create_app(public_db)) as client:
        page = client.get("/search", params={"q": "仅测试同名人物"}).text
        assert "史料中的身份线索" in page and "TEST ONLY PRIVATE UNAPPROVED" not in page


@pytest.mark.parametrize("change", ["pending", "mentions", "indirect", "unsafe_url"])
def test_identity_excerpt_disappears_with_unapproved_support(public_db, change):
    with connect_db(public_db) as c:
        if change == "unsafe_url":
            c.execute("UPDATE sources SET original_url='javascript:alert(1)'")
        else:
            column = {"pending": "verification_status", "mentions": "usage_relation", "indirect": "evidence_nature"}[change]
            c.execute(f"UPDATE evidences SET {column}=?", (change,))
    assert get_person(public_db, "TEST-P1")["identity_evidences"] == []


def test_search_examples_resolve_and_never_suggest_hidden_terms(public_db):
    examples = search_examples(public_db)
    assert examples
    assert "仅测试隐藏归属" not in examples
    assert all(search_people(public_db, term) for term in examples)


def test_identity_excerpt_deduplicates_quote_without_changing_source(public_db):
    with connect_db(public_db) as c:
        c.execute("UPDATE evidences SET original_text='TEST ONLY SAME QUOTE' WHERE alias_id IN ('TEST-A1','TEST-A3')")
    items = get_person(public_db, "TEST-P1")["identity_evidences"]
    assert len(items) == 1 and items[0]["alias_type"] == "courtesy"
    assert items[0]["original_url"] == "https://example.invalid/test-only?revision=7&part=1"


def test_html_validation_and_404_keep_machine_json_contract(db_path):
    with TestClient(create_app(db_path)) as client:
        for route, code in (("/search?q=" + "测" * 101, 422), ("/missing", 404)):
            html = client.get(route, headers={"accept": "text/html"})
            assert html.status_code == code and "text/html" in html.headers["content-type"]
            assert "返回称呼检索" in html.text
            api = client.get(route, headers={"accept": "application/json"})
            assert api.status_code == code and "error" in api.json()


def test_html_database_error_is_safe_and_actionable(db_path):
    with TestClient(create_app(db_path)) as client:
        with connect_db(db_path) as c:
            c.execute("DROP TABLE evidences")
        response = client.get("/", headers={"accept": "text/html"})
        assert response.status_code == 503 and "数据库暂时不可用" in response.text
        assert "no such table" not in response.text and str(db_path) not in response.text


def test_no_javascript_core_search_and_evidence_still_server_rendered(public_db):
    with TestClient(create_app(public_db)) as client:
        home = client.get("/").text
        assert 'method="get"' in home and 'enterkeyhint="search"' in home
        person = client.get("/people/TEST-P1", params={"q": "仅测试共用字"}).text
        assert "#results" in person and "史料中的身份线索" in person
        alias = client.get("/aliases/TEST-A1").text
        assert "TEST ONLY synthetic evidence" in alias and "复制原文" in alias
        assert 'class="copy-quote"' in alias and " hidden>" in alias


def test_ai_explanation_is_labeled_without_exposing_model_internals(public_db):
    import json
    with connect_db(public_db) as c:
        c.execute("UPDATE evidences SET notes=? WHERE alias_id='TEST-A1'", (json.dumps({"remarks": "TEST ONLY AI-assisted explanation", "reviewer_model": "TEST_ONLY_MODEL_PRIVATE", "raw_path": "TEST_ONLY_INTERNAL_PATH"}),))
    with TestClient(create_app(public_db)) as client:
        page = client.get("/aliases/TEST-A1").text
        assert "机器辅助说明（来自既有核验记录）" in page
        assert "TEST_ONLY_MODEL_PRIVATE" not in page and "TEST_ONLY_INTERNAL_PATH" not in page
