"""Mapping checks for the AI antiquarian-style illustrations.

The ten images are presentation assets only. These tests seed a temporary
database with the real person IDs and canonical names, but never read or write
the formal ``data/guji.db``.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import archive_display
from app.archive_display import FIGURE_DISCLOSURE, figure_gallery, figures_for_person
from app.db import connect_db, init_db
from app.main import create_app

EXPECTED = {
    "P-CBDB-32540": "李白",
    "P-CBDB-3915": "杜甫",
    "P-CBDB-32227": "白居易",
    "P-CBDB-32174": "王維",
    "P-0180cd77-04c3-471a-937a-cc8e77709e52": "苏轼",
    "P-CBDB-19713": "李清照",
    "P-CBDB-30359": "辛棄疾",
    "P-CBDB-3835": "陶潛",
    "P-S7-e471cf86a00e": "屈原",
    "P-S7-949c8f1cd5e7": "司馬遷",
}
FIGURES = Path(archive_display.__file__).with_name("figure_media.json")
STATIC = Path(archive_display.__file__).with_name("static")


def seed_people(path, people):
    init_db(path)
    with connect_db(path) as connection:
        connection.executemany(
            "INSERT INTO people(person_id,canonical_name,dynasty,verification_status) VALUES(?,?,?,?)",
            [(person_id, name, "测试朝", "verified") for person_id, name in people.items()],
        )


def test_manifest_contains_ten_unique_ai_figures_with_explicit_disclosure():
    data = json.loads(FIGURES.read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert data["disclosure"] == FIGURE_DISCLOSURE == "AI生成·拟古演绎，非真实古籍书影"
    assert "不进入古籍证据表" in data["evidence_warning"]
    items = data["items"]
    assert len(items) == 10
    assert len({item["person_id"] for item in items}) == 10
    assert {item["person_id"]: item["db_name"] for item in items} == EXPECTED
    for item in items:
        assert item["ai_generated"] is True
        assert item["review_status"] == "verified"
        assert item["image_url"].startswith("/static/figure-media/")
        assert item["image_url"].endswith(".webp")
        assert (STATIC / item["image_url"].removeprefix("/static/")).is_file()


def test_gallery_binds_each_image_to_the_verified_canonical_name(tmp_path):
    path = tmp_path / "TEST_ONLY_figures.db"
    seed_people(path, EXPECTED)
    gallery = figure_gallery(path)
    assert len(gallery) == 10
    assert {item["person_id"]: item["display_name"] for item in gallery} == {
        "P-CBDB-32540": "李白",
        "P-CBDB-3915": "杜甫",
        "P-CBDB-32227": "白居易",
        "P-CBDB-32174": "王維",
        "P-0180cd77-04c3-471a-937a-cc8e77709e52": "苏轼",
        "P-CBDB-19713": "李清照",
        "P-CBDB-30359": "辛棄疾",
        "P-CBDB-3835": "陶淵明",
        "P-S7-e471cf86a00e": "屈原",
        "P-S7-949c8f1cd5e7": "司馬遷",
    }
    for item in gallery:
        assert item["disclosure"] == FIGURE_DISCLOSURE
        assert item["ai_generated"] is True
        assert "evidence_id" not in item
    assert "P-CBDB-175377" not in {item["person_id"] for item in gallery}


def test_missing_or_wrong_disclosure_rejects_all_figures(tmp_path, monkeypatch):
    path = tmp_path / "TEST_ONLY_disclosure.db"
    seed_people(path, {"P-CBDB-32540": "李白"})
    data = json.loads(FIGURES.read_text(encoding="utf-8"))
    data["disclosure"] = "拟古图像"
    broken = tmp_path / "TEST_ONLY_figures.json"
    broken.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(archive_display, "FIGURE_INDEX_PATH", broken)
    assert figure_gallery(path) == []


def test_wrong_canonical_name_is_rejected(tmp_path):
    path = tmp_path / "TEST_ONLY_wrong_name.db"
    seed_people(path, {"P-CBDB-32540": "不是李白"})
    assert figure_gallery(path) == []
    assert figures_for_person(path, "P-CBDB-32540") == []


def test_person_page_b_area_separates_real_scans_from_ai_figures(tmp_path):
    path = tmp_path / "TEST_ONLY_figure_page.db"
    seed_people(path, {"P-CBDB-32540": "李白"})
    with TestClient(create_app(path)) as client:
        page = client.get("/people/P-CBDB-32540")
    assert page.status_code == 200
    text = page.text
    assert 'id="archive-mode-b"' in text and "disabled" not in text.split('id="archive-mode-b"')[1].split(">")[0]
    assert "B · 书影与图像" in text
    assert "真实书影" in text and "AI 拟古绘像" in text
    assert "暂无可核验的真实书影" in text
    assert "/static/figure-media/li_bai.webp" in text
    assert FIGURE_DISCLOSURE in text
    assert "不写入古籍证据表" in text
    assert "交互展示 · 非古籍原文扫描" in text


def test_home_neutral_archive_shows_the_ten_figure_gallery(tmp_path):
    path = tmp_path / "TEST_ONLY_figure_home.db"
    seed_people(path, EXPECTED)
    with TestClient(create_app(path)) as client:
        text = client.get("/").text
    assert text.count("/static/figure-media/") == 10
    assert 'id="archive-mode-b"' in text
    assert "B · 书影与图像" in text
    assert FIGURE_DISCLOSURE in text
    assert "十位人物的 AI 拟古绘像图录" in text


def test_webp_assets_are_served_with_the_webp_mime_type(tmp_path):
    path = tmp_path / "TEST_ONLY_figure_static.db"
    init_db(path)
    with TestClient(create_app(path)) as client:
        response = client.get("/static/figure-media/li_bai.webp")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert len(response.content) > 100_000
