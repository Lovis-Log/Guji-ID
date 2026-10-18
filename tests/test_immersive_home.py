"""Synthetic-only display tests for the immersive A/B archive component.

No historical person, scan, or quotation is created here. The fixtures live in
pytest's temporary directory and never touch data/guji.db.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import archive_display
from app.db import connect_db, init_db
from app.main import create_app


@pytest.fixture
def immersive_db(tmp_path):
    path = tmp_path / "TEST_ONLY_immersive.db"
    init_db(path)
    with connect_db(path) as connection:
        connection.executemany(
            "INSERT INTO people(person_id,canonical_name,dynasty,verification_status) VALUES(?,?,?,?)",
            [
                ("TEST-P-ONE", "测试甲", "测试朝", "verified"),
                ("TEST-P-TWO", "测试同名", "测试朝一", "verified"),
                ("TEST-P-THREE", "测试同名", "测试朝二", "verified"),
            ],
        )
        connection.execute(
            "INSERT INTO sources(source_id,book_title,chapter,platform,original_url) "
            "VALUES('TEST-S-ONE','TEST ONLY, NOT A BOOK','TEST-LOCATION','TEST ONLY','https://example.invalid/source')"
        )
        connection.executemany(
            "INSERT INTO aliases(alias_id,person_id,alias_name,normalized_name,alias_type,verification_status) "
            "VALUES(?,?,?,?,?,?)",
            [
                ("TEST-A-ONE", "TEST-P-ONE", "测试字", "测试字", "courtesy", "verified"),
                ("TEST-A-TWO", "TEST-P-TWO", "测试同名字", "测试同名字", "courtesy", "verified"),
                ("TEST-A-THREE", "TEST-P-THREE", "测试同名字二", "测试同名字二", "art", "verified"),
            ],
        )
        connection.execute(
            "INSERT INTO evidences(evidence_id,alias_id,source_id,original_text,usage_relation,evidence_nature,verification_status) "
            "VALUES('TEST-E-ONE','TEST-A-ONE','TEST-S-ONE','TEST ONLY, NOT AN ORIGINAL QUOTE','supports','direct','verified')"
        )
        for evidence_id, alias_id in (("TEST-E-TWO", "TEST-A-TWO"), ("TEST-E-THREE", "TEST-A-THREE")):
            connection.execute(
                "INSERT INTO evidences(evidence_id,alias_id,source_id,original_text,usage_relation,evidence_nature,verification_status) "
                "VALUES(?,?,'TEST-S-ONE','TEST ONLY, NOT AN ORIGINAL QUOTE','supports','direct','verified')",
                (evidence_id, alias_id),
            )
    return path


@pytest.fixture
def immersive_client(immersive_db):
    with TestClient(create_app(immersive_db)) as client:
        yield client


def test_single_person_search_drives_title_slip_from_canonical_id(immersive_client):
    response = immersive_client.get("/search", params={"q": "测试甲"})
    assert response.status_code == 200
    page = response.text
    assert 'data-person-id="TEST-P-ONE"' in page
    assert 'class="archive-person-name">测试甲</strong>' in page
    assert "数字题签 · TEST-P-ONE" in page
    assert "暂无可展示的真实书影，当前使用 A 模式数字题签。" in page
    assert 'id="archive-mode-b"' in page and "disabled" in page
    # A mode is not a manuscript scan and must say so.
    assert "交互展示 · 非古籍原文扫描" in page
    # The baked-in prototype name must not leak into a different person's record.
    assert "李白" not in page


def test_person_page_uses_same_dynamic_slip_and_links_to_its_own_profile(immersive_client):
    response = immersive_client.get("/people/TEST-P-ONE")
    assert response.status_code == 200
    page = response.text
    assert 'class="archive-person-name">测试甲</strong>' in page
    assert 'href="#profile-title">查看下方完整人物档案' in page
    assert 'href="/people/TEST-P-ONE">查看完整人物档案' not in page
    assert "李白" not in page


def test_homonyms_keep_independent_results_and_neutral_archive(immersive_client):
    response = immersive_client.get("/search", params={"q": "测试同名"})
    page = response.text
    assert response.status_code == 200
    assert 'data-person-id="TEST-P-TWO"' in page
    assert 'data-person-id="TEST-P-THREE"' in page
    assert 'data-archive-display data-person-id=""' in page
    assert "数字题签 · 待选人物" in page
    assert "此处为中性视觉示意。检索并选择人物后，题签随规范 ID 更新。" in page


def test_mode_b_appears_only_for_verified_person_evidence_source(
    immersive_client, immersive_db, tmp_path, monkeypatch
):
    index = tmp_path / "TEST_ONLY_scans.json"
    monkeypatch.setattr(archive_display, "INDEX_PATH", index)
    entry = {
        "person_id": "TEST-P-ONE",
        "evidence_id": "TEST-E-ONE",
        "provider": "TEST ONLY PROVIDER",
        "image_url": "https://example.invalid/scan.jpg",
        "source_url": "https://example.invalid/source",
        "license": "TEST ONLY - NOT HISTORICAL",
        "review_status": "verified",
        "highlight": {"reviewed": False, "x": 0.2, "y": 0.3, "width": 0.1, "height": 0.08},
    }
    index.write_text(json.dumps([entry]), encoding="utf-8")

    page = immersive_client.get("/people/TEST-P-ONE").text
    assert 'id="archive-mode-b"' in page
    assert "https://example.invalid/scan.jpg" in page
    assert "TEST ONLY - NOT HISTORICAL" in page
    assert "TEST-E-ONE" in page
    assert "尚未定位到页内文字" in page
    assert "archive-scan-highlight" not in page

    entry["highlight"]["reviewed"] = True
    index.write_text(json.dumps([entry]), encoding="utf-8")
    page = immersive_client.get("/people/TEST-P-ONE").text
    assert "archive-scan-highlight" in page
    assert "left:20.0%;top:30.0%;width:10.0%;height:8.0%" in page

    other = immersive_client.get("/people/TEST-P-TWO").text
    assert "https://example.invalid/scan.jpg" not in other
    assert "暂无可展示的真实书影" in other


def test_empty_database_does_not_suggest_unresolved_quick_searches(tmp_path):
    path = tmp_path / "TEST_ONLY_empty.db"
    init_db(path)
    with TestClient(create_app(path)) as client:
        page = client.get("/").text
    assert "试寻" not in page
    assert "文徵明" not in page
