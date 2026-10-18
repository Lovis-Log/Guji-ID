"""Public catalog tests use synthetic records only in pytest's isolated DB."""

import json

import pytest

from app.catalog import get_alias, get_person, public_counts, search_people
from app.db import connect_db


@pytest.fixture
def public_db(db_path):
    with connect_db(db_path) as connection:
        connection.executemany("INSERT INTO people (person_id, canonical_name, dynasty, birth_year, death_year, native_place, identity_description, verification_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            ("TEST-P1", "仅测试同名人物", "测试甲朝", -10, 20, "仅测试甲地", "仅测试：甲身份，非历史资料", "verified"),
            ("TEST-P2", "仅测试同名人物", "测试乙朝", None, None, "仅测试乙地", "仅测试：乙身份，非历史资料", "verified"),
            ("TEST-P3", "仅测试待核人物", None, None, None, None, None, "pending"),
            ("TEST-P4", "仅测试存疑人物", None, None, None, None, None, "disputed"),
            ("TEST-P5", "仅测试无别称人物", None, None, None, None, None, "verified"),
        ])
        connection.execute("INSERT INTO sources VALUES ('TEST-S1', 'TEST_ONLY_SYNTHETIC_NOT_A_BOOK', '仅测试卷', NULL, 'TEST_ONLY', 'https://example.invalid/test-only?revision=7&part=1', NULL)")
        aliases = [
            ("TEST-A1", "TEST-P1", "仅测试共用字", "courtesy", "verified"),
            ("TEST-A2", "TEST-P2", "仅测试共用字", "courtesy", "verified"),
            ("TEST-A3", "TEST-P1", "仅测试甲号", "art", "verified"),
            ("TEST-A4", "TEST-P1", "仅测试无证据称呼", "other", "verified"),
            ("TEST-A5", "TEST-P1", "仅测试待核称呼", "art", "pending"),
            ("TEST-A6", "TEST-P1", "仅测试存疑称呼", "art", "disputed"),
            ("TEST-A7", "TEST-P3", "仅测试隐藏归属", "art", "verified"),
            ("TEST-A8", "TEST-P4", "仅测试隐藏归属", "art", "verified"),
            ("TEST-A9", "TEST-P1", "仅测试间接证据", "other", "verified"),
        ]
        connection.executemany("INSERT INTO aliases VALUES (?, ?, ?, ?, ?, ?)", [(aid, pid, name, name, kind, status) for aid, pid, name, kind, status in aliases])
        for aid in ("TEST-A1", "TEST-A2", "TEST-A3", "TEST-A5", "TEST-A6", "TEST-A7", "TEST-A8", "TEST-A9"):
            connection.execute("INSERT INTO evidences VALUES (?, ?, 'TEST-S1', ?, 'supports', ?, 'verified', ?)", ("TEST-E-" + aid, aid, "TEST ONLY synthetic evidence for " + aid, "indirect" if aid == "TEST-A9" else "direct", json.dumps({"relation_type": "后世文献记载其自称", "context": "TEST ONLY context", "remarks": "仅测试，不是史料", "raw_path": "TEST_PRIVATE_PATH"}, ensure_ascii=False)))
    return db_path


def test_exact_aliases_and_canonical_name_use_independent_ids(public_db):
    assert {p["person_id"] for p in search_people(public_db, "仅测试甲号")} == {"TEST-P1"}
    assert {p["person_id"] for p in search_people(public_db, "仅测试共用字")} == {"TEST-P1", "TEST-P2"}
    assert {p["person_id"] for p in search_people(public_db, "仅测试同名人物")} == {"TEST-P1", "TEST-P2"}
    assert len(search_people(public_db, "仅测试共用字")) == 2


def test_conservative_normalization_and_literal_parameters(public_db):
    assert len(search_people(public_db, " \t\u3000仅测试甲号\n")) == 1
    for query in ("", " \t\u3000", "仅测试", "仅 测试甲号", "%", "_", "' OR 1=1 --", "没有收录的测试称呼"):
        assert search_people(public_db, query) == []
    with connect_db(public_db) as connection:
        connection.execute("UPDATE people SET canonical_name = ? WHERE person_id = 'TEST-P5'", ("TEST-e\u0301",))
    assert search_people(public_db, "TEST-é")[0]["person_id"] == "TEST-P5"


@pytest.mark.parametrize("name", ["仅测试待核人物", "仅测试存疑人物", "仅测试无证据称呼", "仅测试待核称呼", "仅测试存疑称呼", "仅测试隐藏归属", "仅测试间接证据"])
def test_unverified_or_unsupported_aliases_never_become_public(public_db, name):
    assert search_people(public_db, name) == []


def test_public_counts_separate_status_flags_from_usable_records(public_db):
    assert public_counts(public_db) == {"people": 3, "aliases": 3}


def test_profile_exposes_only_public_aliases_and_preserves_unknowns(public_db):
    person = get_person(public_db, "TEST-P1")
    assert {a["alias_id"] for a in person["aliases"]} == {"TEST-A1", "TEST-A3"}
    assert person["birth_label"] == "公元前10年"
    assert person["death_label"] == "公元20年"
    empty = get_person(public_db, "TEST-P5")
    assert empty["aliases"] == []
    assert empty["birth_label"] == empty["death_label"] == "暂无已核实信息"
    for person_id in ("TEST-P3", "TEST-P4", "TEST-MISSING"):
        assert get_person(public_db, person_id) is None


def test_profile_page_shows_types_identity_and_true_empty_state(public_db):
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app(public_db)) as client:
        response = client.get("/people/TEST-P1")
        assert response.status_code == 200
        assert "测试甲朝" in response.text and "仅测试甲地" in response.text
        assert "公元前10年" in response.text
        assert 'href="http://testserver/aliases/TEST-A1"' in response.text
        assert "仅测试无证据称呼" not in response.text
        assert "仅测试待核称呼" not in response.text
        empty = client.get("/people/TEST-P5")
        assert empty.status_code == 200
        assert "暂无已核实信息" in empty.text
        assert "暂无可公开展示的已核对别称" in empty.text
        for person_id in ("TEST-P3", "TEST-P4", "TEST-MISSING"):
            hidden = client.get("/people/" + person_id)
            assert hidden.status_code == 404
            assert "仅测试待核人物" not in hidden.text


def test_evidence_page_preserves_database_url_text_and_reported_self(public_db):
    from fastapi.testclient import TestClient
    from app.main import create_app

    alias = get_alias(public_db, "TEST-A1")
    assert alias["person"]["person_id"] == "TEST-P1"
    assert len(alias["evidences"]) == 1
    evidence = alias["evidences"][0]
    assert evidence["relation_type"] == "后世文献记载其自称"
    assert evidence["original_url"] == "https://example.invalid/test-only?revision=7&part=1"
    with TestClient(create_app(public_db)) as client:
        response = client.get("/aliases/TEST-A1")
        assert response.status_code == 200
        for text in ("TEST_ONLY_SYNTHETIC_NOT_A_BOOK", "仅测试卷", "TEST ONLY synthetic evidence for TEST-A1", "TEST_ONLY", "支持归属", "直接证据", "后世文献记载其自称", "证据已核对"):
            assert text in response.text
        assert 'href="https://example.invalid/test-only?revision=7&amp;part=1"' in response.text
        assert "TEST_PRIVATE_PATH" not in response.text


def test_multiple_verified_evidences_keep_their_distinct_nature_and_narrator(public_db):
    with connect_db(public_db) as connection:
        rows = [
            ("TEST-E-SECOND", "supports", "direct", "verified", json.dumps({"relation_type": "人物本人直接自称", "evidence_type": "本人作品", "remarks": "仅测试原作，不是史料"}, ensure_ascii=False)),
            ("TEST-E-REFUTE", "refutes", "indirect", "verified", "仅测试反证备注"),
            ("TEST-E-PENDING", "supports", "direct", "pending", "不得公开的测试待核文本"),
            ("TEST-E-DISPUTED", "supports", "direct", "disputed", "不得公开的测试存疑文本"),
        ]
        connection.executemany("INSERT INTO evidences VALUES (?, 'TEST-A1', 'TEST-S1', ?, ?, ?, ?, ?)", [(eid, "TEST ONLY " + eid, usage, nature, status, notes) for eid, usage, nature, status, notes in rows])
    alias = get_alias(public_db, "TEST-A1")
    assert len(alias["evidences"]) == 3
    assert alias["has_non_support"] is True
    modes = {e["relation_type"] for e in alias["evidences"]}
    assert "人物本人直接自称" in modes
    assert "后世文献记载其自称" in modes
    assert "暂无已核实信息" in modes
    assert {e["usage_relation"] for e in alias["evidences"]} == {"supports", "refutes"}


@pytest.mark.parametrize("alias_id", ["TEST-A4", "TEST-A5", "TEST-A6", "TEST-A7", "TEST-A8", "TEST-A9", "TEST-MISSING"])
def test_hidden_alias_and_direct_links_cannot_bypass_public_gate(public_db, alias_id):
    from fastapi.testclient import TestClient
    from app.main import create_app

    assert get_alias(public_db, alias_id) is None
    with TestClient(create_app(public_db)) as client:
        response = client.get("/aliases/" + alias_id)
        assert response.status_code == 404
        assert "TEST_ONLY_SYNTHETIC_NOT_A_BOOK" not in response.text


@pytest.mark.parametrize("change", ["pending", "mentions", "indirect", "missing_location", "unsafe_url"])
def test_all_views_apply_same_source_and_support_rules(public_db, change):
    with connect_db(public_db) as connection:
        if change == "pending":
            connection.execute("UPDATE evidences SET verification_status = 'pending' WHERE alias_id='TEST-A1'")
        elif change == "mentions":
            connection.execute("UPDATE evidences SET usage_relation = 'mentions' WHERE alias_id='TEST-A1'")
        elif change == "indirect":
            connection.execute("UPDATE evidences SET evidence_nature = 'indirect' WHERE alias_id='TEST-A1'")
        elif change == "missing_location":
            connection.execute("UPDATE sources SET volume=NULL, chapter=NULL")
        else:
            connection.execute("UPDATE sources SET original_url='javascript:alert(1)'")
    assert get_alias(public_db, "TEST-A1") is None
    assert "TEST-A1" not in {a["alias_id"] for a in get_person(public_db, "TEST-P1")["aliases"]}
    assert "TEST-P1" not in {p["person_id"] for p in search_people(public_db, "仅测试共用字")}


def test_no_self_naming_is_inferred_from_direct_or_free_text_notes(public_db):
    with connect_db(public_db) as connection:
        connection.execute("UPDATE evidences SET notes = '仅测试普通备注' WHERE alias_id='TEST-A1'")
    evidence = get_alias(public_db, "TEST-A1")["evidences"][0]
    assert evidence["nature_label"] == "直接证据"
    assert evidence["relation_type"] == "暂无已核实信息"
    assert evidence["remarks"] == "仅测试普通备注"


def test_search_shows_all_candidates_and_complete_click_path(public_db):
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app(public_db)) as client:
        response = client.get("/search", params={"q": " \t仅测试共用字\u3000"}, follow_redirects=False)
        assert response.status_code == 200 and "location" not in response.headers
        assert 'data-person-id="TEST-P1"' in response.text
        assert 'data-person-id="TEST-P2"' in response.text
        assert "存在多个候选，不能确定" in response.text
        assert "测试甲朝" in response.text and "测试乙朝" in response.text
        assert "仅测试甲地" in response.text and "仅测试乙地" in response.text
        assert "前10" in response.text  # Search lifespan is compact; preserve the BCE marker.
        assert response.text.count("暂无已核实信息") <= 1  # Stage 6.5 folds missing fields instead of repeating placeholders.
        canonical = client.get("/search", params={"q": "仅测试同名人物"})
        assert canonical.text.count('class="candidate-card"') == 2
        single = client.get("/search", params={"q": "仅测试甲号"}, follow_redirects=False)
        assert single.status_code == 200 and single.text.count('class="candidate-card"') == 1
        assert "存在多个候选，不能确定" not in single.text
        profile = client.get("/people/TEST-P1", params={"q": "仅测试甲号"})
        assert profile.status_code == 200 and "/aliases/TEST-A1" in profile.text
        evidence = client.get("/aliases/TEST-A1")
        assert evidence.status_code == 200 and "TEST ONLY synthetic evidence" in evidence.text


def test_search_empty_missing_literal_and_input_length_states(public_db):
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app(public_db)) as client:
        for q in ("没有收录的测试称呼", "' OR 1=1 --", "%", "_", "仅测试"):
            response = client.get("/search", params={"q": q})
            assert response.status_code == 200
            assert "未找到已核对的匹配人物" in response.text
            assert 'class="candidate-card"' not in response.text
        assert "请先输入一个称呼" in client.get("/search", params={"q": " \t"}).text
        too_long = client.get("/search", params={"q": "测" * 101})
        assert too_long.status_code == 422
        assert too_long.json()["error"]["code"] == "invalid_request"


def test_rendering_escapes_input_and_database_fields(public_db):
    from fastapi.testclient import TestClient
    from app.main import create_app

    payload = '<script>alert("TEST ONLY")</script>'
    with connect_db(public_db) as connection:
        connection.execute("UPDATE people SET identity_description=? WHERE person_id='TEST-P1'", (payload,))
        connection.execute("UPDATE sources SET book_title=?", (payload,))
        connection.execute("UPDATE evidences SET original_text=?, notes=? WHERE alias_id='TEST-A1'", (payload, json.dumps({"context": payload, "relation_type": payload})))
    with TestClient(create_app(public_db)) as client:
        for url, params in (("/search", {"q": payload}), ("/search", {"q": "仅测试甲号"}), ("/people/TEST-P1", {}), ("/aliases/TEST-A1", {})):
            response = client.get(url, params=params)
            assert response.status_code == 200
            assert payload not in response.text
            assert "&lt;script&gt;" in response.text


def test_imported_text_ids_with_reserved_characters_are_routable(public_db):
    from fastapi.testclient import TestClient
    from urllib.parse import quote
    from app.main import create_app

    pid = "TEST/P5?part=1#segment"
    with connect_db(public_db) as connection:
        connection.execute("UPDATE people SET person_id=? WHERE person_id='TEST-P5'", (pid,))
    with TestClient(create_app(public_db)) as client:
        search = client.get("/search", params={"q": "仅测试无别称人物"})
        assert quote(pid, safe="") in search.text
        profile = client.get("/people/" + quote(pid, safe=""))
        assert profile.status_code == 200 and "仅测试无别称人物" in profile.text


def test_new_imported_verified_chain_is_immediately_searchable(db_path, batch, write_batch):
    # Verify integration with the existing four-CSV importer, not just SQL fixtures.
    from app.importer import import_batch

    batch["people"][0]["verification_status"] = "verified"
    import_batch(write_batch(batch), db_path)
    assert search_people(db_path, "仅测试共用别称")[0]["person_id"] == "TEST-P1"
    assert len(search_people(db_path, "仅测试共用别称")) == 1
    assert get_alias(db_path, "TEST-A1")["evidences"][0]["original_text"].startswith("TEST ONLY:")
