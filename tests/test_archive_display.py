"""Synthetic fixtures only; no test scan or person is historical material."""

import json

from app import archive_display
from app.db import connect_db


def test_empty_scan_index_falls_back_to_a(db_path):
    assert archive_display.scan_for_person(db_path, "TEST-P1") is None


def test_scan_requires_exact_public_person_evidence_and_source(db_path, tmp_path, monkeypatch):
    with connect_db(db_path) as connection:
        connection.execute("INSERT INTO people(person_id,canonical_name,verification_status) VALUES('TEST-P1','虚构同名','verified')")
        connection.execute("INSERT INTO people(person_id,canonical_name,verification_status) VALUES('TEST-P2','虚构同名','verified')")
        connection.execute("INSERT INTO aliases(alias_id,person_id,alias_name,normalized_name,alias_type,verification_status) VALUES('TEST-A1','TEST-P1','测试名','测试名','name','verified')")
        connection.execute("INSERT INTO sources(source_id,book_title,chapter,platform,original_url) VALUES('TEST-S1','TEST ONLY, NOT A BOOK','TEST-LOC','TEST ONLY','https://example.invalid/source')")
        connection.execute("INSERT INTO evidences(evidence_id,alias_id,source_id,original_text,usage_relation,evidence_nature,verification_status) VALUES('TEST-E1','TEST-A1','TEST-S1','TEST ONLY, NOT AN ORIGINAL QUOTE','supports','direct','verified')")
    entry = {"person_id": "TEST-P1", "evidence_id": "TEST-E1", "provider": "TEST ONLY",
             "image_url": "https://example.invalid/scan.jpg", "source_url": "https://example.invalid/source",
             "license": "TEST ONLY", "review_status": "verified",
             "highlight": {"reviewed": False, "x": .2, "y": .2, "width": .1, "height": .1}}
    index = tmp_path / "scans.json"
    monkeypatch.setattr(archive_display, "INDEX_PATH", index)
    index.write_text(json.dumps([entry]), encoding="utf-8")
    assert archive_display.scan_for_person(db_path, "TEST-P2") is None
    result = archive_display.scan_for_person(db_path, "TEST-P1")
    assert result["evidence_id"] == "TEST-E1" and result["highlight"] is None
    entry["source_url"] = "https://example.invalid/wrong-source"
    index.write_text(json.dumps([entry]), encoding="utf-8")
    assert archive_display.scan_for_person(db_path, "TEST-P1") is None
    entry["source_url"] = "https://example.invalid/source"
    entry["review_status"] = "pending"
    index.write_text(json.dumps([entry]), encoding="utf-8")
    assert archive_display.scan_for_person(db_path, "TEST-P1") is None
