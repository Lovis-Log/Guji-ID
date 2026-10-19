"""Stage 7 tests use synthetic fixtures only, never formal historical data."""

import json
import sqlite3

from fastapi.testclient import TestClient

from app.classical_text import (
    clean_wikitext,
    locate_quote,
    normalised_quote,
    parse_kanripo,
    parse_wikisource,
    sha256_text,
    verify_quote,
)
from app.classics import highlight_segments
from app.db import connect_db, init_db
from app.main import create_app

TEST_TEXT = (
    "卷之一\n\n"
    "試人，字測字，測郡人也。少好學，通經史。\n\n"
    "年二十，舉進士，調校書郎。後遷御史，直言無隱。\n\n"
    "卒於官，年五十七。子二人，長曰甲，次曰乙。\n"
)
SPECIAL_TEXT = "䶮𠀀𪚥，字測字。\n\n異體字：栁、㣲、曌、𠀀。\n"


def _seed_library(path):
    init_db(path)
    with connect_db(path) as connection:
        connection.execute("INSERT INTO people(person_id,canonical_name,verification_status) VALUES('P-TEST','试人','verified')")
        connection.execute("INSERT INTO aliases(alias_id,person_id,alias_name,normalized_name,alias_type,verification_status) VALUES('A-TEST','P-TEST','测字','测字','courtesy','verified')")
        connection.execute("INSERT INTO sources(source_id,book_title,volume,chapter,platform,original_url,edition) VALUES('S-TEST','测试史','卷一','试人传','测试平台','https://example.invalid/test','测试版本')")
        chapter_text = TEST_TEXT
        connection.execute(
            """INSERT INTO classical_works(work_id,title,author,dynasty,category,source_platform,
               source_url,digital_edition,license_id,license_url,retrieved_at,created_at)
               VALUES('W-TEST','测试史','测试作者','测试朝','正史','测试平台','https://example.invalid/work',
               '测试版本','CC-BY-SA-4.0','https://creativecommons.org/licenses/by-sa/4.0/','2026-01-01','2026-01-01')"""
        )
        connection.execute(
            """INSERT INTO classical_chapters(chapter_id,work_id,volume,chapter_title,sequence,
               source_path,source_url,source_sha256,text_sha256,original_text,search_text,char_count,
               paragraph_count,page_unit_count,completeness,verification_status,license_id,retrieved_at,created_at)
               VALUES('C-TEST','W-TEST','卷一','试人传',1,'test.txt','https://example.invalid/chapter',
               ?,?,?,?,?,3,1,'full','verified','CC-BY-SA-4.0','2026-01-01','2026-01-01')""",
            (sha256_text("raw"), sha256_text(chapter_text), chapter_text,
             chapter_text.replace("\n", ""), len(chapter_text)),
        )
        for ordinal, (start, end) in enumerate([(0, len(chapter_text))], 1):
            connection.execute(
                """INSERT INTO classical_paragraphs(paragraph_id,chapter_id,ordinal,scheme,text,
                   char_start,char_end,page_label) VALUES(?,?,?,?,?,?,?,?)""",
                (f"P-TEST-{ordinal}", "C-TEST", ordinal, "reading",
                 chapter_text[start:end], start, end, "试人"),
            )
        quote = "試人，字測字，測郡人也。"
        start = chapter_text.index(quote)
        connection.execute(
            """INSERT INTO evidences(evidence_id,alias_id,source_id,original_text,usage_relation,
               evidence_nature,verification_status,notes) VALUES('E-TEST','A-TEST','S-TEST',?,
               'supports','direct','verified',?)""",
            (quote, json.dumps({"evidence_kind": "classical_text"})),
        )
        connection.execute(
            """INSERT INTO classical_evidence_links(link_id,evidence_id,person_id,alias_id,chapter_id,
               paragraph_id,quote,quote_start,quote_end,evidence_category,claim,explanation,
               identity_match,verification_status,quote_sha256,created_at)
               VALUES('L-TEST','E-TEST','P-TEST','A-TEST','C-TEST','P-TEST-1',?,?,?,
               'direct_alias','本传记载其字','','confirmed','verified',?,'2026-01-01')""",
            (quote, start, start + len(quote), sha256_text(quote)),
        )
        connection.execute(
            """INSERT INTO person_profiles(person_id,tier,biography_modern,completeness_json,
               created_at,updated_at) VALUES('P-TEST','A','测试人物简介','{}','2026-01-01','2026-01-01')"""
        )


def test_kanripo_parser_preserves_text_pages_and_special_chars():
    raw = (
        "# -*- mode: mandoku-view -*-\n#+TITLE: 测试\n#+PROPERTY: ID TEST\n"
        "<pb:TEST_001-1a>¶\n第一行，¶\n有異體字栁㣲曌。¶\n"
        "<pb:TEST_001-1b><pb:TEST_001-2a>¶\n第二页𠀀𪚥。¶\n"
    )
    drafts = parse_kanripo(raw, fallback_title="测试卷", fallback_volume="卷一")
    assert len(drafts) == 1
    draft = drafts[0]
    assert "栁㣲曌" in draft.text
    assert "𠀀𪚥" in draft.text
    assert len(draft.pages) == 2
    assert draft.text.count("第一行") == 1
    assert "¶" not in draft.text
    assert "pb:" not in draft.text


def test_wikisource_cleaner_keeps_paragraphs_and_removes_templates():
    raw = (
        "{{header|title=测试}}\n==人物==\n"
        "試人，字測字。[[link|連結文字]]<ref>注</ref>\n\n"
        "第二段，有䶮𪚥與栁。\n[[Category:测试]]\n"
    )
    draft = parse_wikisource(raw, fallback_title="测试", fallback_volume="卷一")
    assert "連結文字" in draft.text
    assert "{{" not in draft.text
    assert "Category" not in draft.text
    assert "第二段" in draft.text
    assert len(draft.paragraphs) == 2
    assert "人物的" not in draft.text


def test_quote_location_tolerates_layout_whitespace_only():
    text = "試人，\n字測字，\n測郡人也。"
    quote = "試人，字測字，測郡人也。"
    located = locate_quote(text, quote)
    assert located is not None
    start, end = located
    assert normalised_quote(text, start, end) == quote.replace("\n", "")
    assert verify_quote(text, quote, start, end)
    assert not verify_quote(text, "不存在的引文", 0, 5)


def test_highlight_merges_overlapping_ranges_and_marks_selected():
    text = "甲乙丙丁戊己庚辛"
    ranges = [
        {"link_id": "L1", "quote_start": 2, "quote_end": 6, "person_id": "P1",
         "canonical_name": "甲", "evidence_category": "direct_alias", "category_label": "直接称谓证据"},
        {"link_id": "L2", "quote_start": 4, "quote_end": 8, "person_id": "P1",
         "canonical_name": "甲", "evidence_category": "biography", "category_label": "生平事迹"},
    ]
    segments = highlight_segments(text, ranges, selected="L2")
    assert "".join(segment["text"] for segment in segments) == text
    marked = [segment for segment in segments if segment["marked"]]
    assert len(marked) == 1
    assert marked[0]["text"] == "丙丁戊己庚辛"
    assert marked[0]["selected"] is True


def test_reader_routes_person_modules_and_no_internal_json(tmp_path):
    path = tmp_path / "test_library.db"
    _seed_library(path)
    client = TestClient(create_app(path))

    index = client.get("/classics")
    assert index.status_code == 200 and "测试史" in index.text
    work = client.get("/classics/W-TEST")
    assert work.status_code == 200 and "试人传" in work.text
    chapter = client.get("/classics/W-TEST/C-TEST")
    assert chapter.status_code == 200
    assert "試人，字測字，測郡人也。" in chapter.text
    assert "卒於官，年五十七。" in chapter.text
    assert "篇章正文" in chapter.text
    assert "quote_sha256" not in chapter.text
    assert "classical_evidence_links" not in chapter.text

    person = client.get("/people/P-TEST")
    assert person.status_code == 200
    for label in ("生平概述", "古籍原文与证据", "在篇章中定位", "文献来源索引"):
        assert label in person.text
    assert "测字" in person.text
    assert "展开全部古籍证据" in person.text
    assert "档案摘要" in person.text

    selected = client.get("/classics/W-TEST/C-TEST",
                          params={"person": "P-TEST", "evidence": "L-TEST"})
    assert selected.status_code == 200
    assert "is-selected" in selected.text
    assert "上下文模式" in selected.text
    assert "本篇证据目录" in selected.text
    assert "返回所选证据与解释" in selected.text
    assert "試人，字測字" in selected.text


def test_unverified_or_mismatched_link_is_not_public(tmp_path):
    path = tmp_path / "test_library.db"
    _seed_library(path)
    with connect_db(path) as connection:
        connection.execute("UPDATE classical_evidence_links SET identity_match='rejected' WHERE link_id='L-TEST'")
    client = TestClient(create_app(path))
    person = client.get("/people/P-TEST")
    assert person.status_code == 200
    assert "古籍原文与证据" in person.text
    assert "尚未取得可定位的古籍原文" in person.text
    assert "在篇章中定位" not in person.text
    from app.classics import person_library
    assert person_library(path, "P-TEST")["links"] == []


def test_long_special_text_round_trip_without_truncation(tmp_path):
    path = tmp_path / "test_library.db"
    init_db(path)
    long_text = (SPECIAL_TEXT + "長文測試。\n\n") * 2000
    with connect_db(path) as connection:
        connection.execute(
            """INSERT INTO classical_works(work_id,title,source_platform,source_url,license_id,
               license_url,retrieved_at,created_at) VALUES('W-LONG','长文测试','测试','https://example.invalid',
               'CC-BY-SA-4.0','https://creativecommons.org/licenses/by-sa/4.0/','2026-01-01','2026-01-01')"""
        )
        connection.execute(
            """INSERT INTO classical_chapters(chapter_id,work_id,chapter_title,sequence,source_path,
               source_url,source_sha256,text_sha256,original_text,search_text,char_count,
               paragraph_count,page_unit_count,completeness,verification_status,license_id,retrieved_at,created_at)
               VALUES('C-LONG','W-LONG','长文',1,'x','https://example.invalid/x',?,?,?,?,?,0,0,'full',
               'verified','CC-BY-SA-4.0','2026-01-01','2026-01-01')""",
            (sha256_text("raw"), sha256_text(long_text), long_text,
             long_text.replace("\n", ""), len(long_text)),
        )
    client = TestClient(create_app(path))
    response = client.get("/classics/W-LONG/C-LONG")
    assert response.status_code == 200
    assert response.text.count("長文測試") == 2000
    assert "𪚥" in response.text
    assert len(response.text) > len(long_text)


def test_database_rejects_empty_or_inverted_quote_offsets(tmp_path):
    path = tmp_path / "test_library.db"
    _seed_library(path)
    with connect_db(path) as connection:
        try:
            connection.execute(
                """INSERT INTO classical_evidence_links(link_id,evidence_id,person_id,alias_id,
                   chapter_id,quote,quote_start,quote_end,evidence_category,claim,identity_match,
                   verification_status,quote_sha256,created_at)
                   VALUES('L-BAD','E-TEST','P-TEST','A-TEST','C-TEST','x',5,5,'biography','x',
                   'confirmed','verified',?,'2026-01-01')""",
                (sha256_text("x"),),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("inverted offsets must be rejected")
