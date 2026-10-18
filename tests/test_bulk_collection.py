"""Synthetic public-API-shaped fixtures; no real HTTP or provider calls."""

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode

import pytest

from app.bulk_collection import BatchClient, catalogue_plan, collect_bulk, discover_sections, retry_deadline
from app.collector import API, Client, FetchFailure, digest, plain, read_csv
from app.verification import group_packet, json_read, json_write, parse_review


def sample_page():
    html = '<h2>仅测试甲</h2><p>仅测试甲，字仅测字，测试甲地人。仅测试虚构正文。</p><h3>孝廣</h3><p>孝廣，字仅测二。</p><h2>仅测试乙</h2><p>仅测试乙，字仅测号，测试乙地人。</p>'
    return {"title": "TEST_ONLY_SYNTHETIC_BOOK/卷1", "text": plain(html), "html": html, "sections": [{"line": "仅测试甲", "level": "2"}, {"line": "孝廣", "level": "3"}, {"line": "仅测试乙", "level": "2"}], "url": "https://zh.wikisource.org/w/index.php?oldid=1", "revision": 1, "raw_path": "raw/TEST_ONLY.body"}


@pytest.fixture
def bulk_config(tmp_path):
    archive = tmp_path / "archive"
    (archive / "raw").mkdir(parents=True)
    page = sample_page()
    url = API + "?" + urlencode({"action": "parse", "page": page["title"], "prop": "text|links|revid", "format": "json"})
    body = json.dumps({"parse": {"title": page["title"], "text": {"*": page["html"]}, "sections": page["sections"], "links": [], "revid": 1}}, ensure_ascii=False).encode()
    key = digest(url)
    (archive / "raw" / (key + ".body")).write_bytes(body)
    json_write(archive / "raw" / (key + ".json"), {"request_url": url, "http_status": 200, "error": "", "sha256": digest(body), "retrieved_at": "2026-10-16T00:00:00+08:00"})
    config = {"batch_name": "TEST_ONLY_BATCH", "target_total_people": 2, "dynasties": ["唐"], "source_priority": ["维基文库"], "previous_batches": [], "output_root": str(tmp_path / "batches"), "cache_dir": str(archive), "use_cache": True, "offline": True, "interval": 2, "timeout": 2, "max_requests": 0, "max_catalogue_pages": 4, "max_candidates": 10, "max_aliases_per_person": 2, "catalogues": [{"title": page["title"], "dynasty": "唐"}], "volume_ranges": []}
    path = tmp_path / "config.json"
    json_write(path, config)
    return path, config


def test_discovery_uses_exact_section_opening_and_skips_surname_omissions():
    found = list(discover_sections(sample_page(), "唐"))
    assert [p["canonical_name"] for p in found] == ["仅测试甲", "仅测试乙"]
    assert all(p["native_place_candidate"] for p in found)
    assert "孝廣" not in {p["canonical_name"] for p in found}
    bad = sample_page()
    bad["text"] = bad["text"].replace("仅测试甲，字", "仅测试丙，字")
    assert "仅测试甲" not in {p["canonical_name"] for p in discover_sections(bad, "唐")}


def test_offline_collect_resume_has_stable_ids_and_no_duplicate_rows(bulk_config, monkeypatch):
    path, config = bulk_config
    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *a, **k: pytest.fail("离线不得联网"))
    first = collect_bulk(path)
    root = Path(config["output_root"]) / config["batch_name"]
    files = ("candidate_people.csv", "aliases_pending.csv", "evidence_pending.csv")
    before = {f: digest((root / f).read_bytes()) for f in files}
    assert first["cumulative_candidate_people"] == 2 and first["located_evidences"] == 4
    again = collect_bulk(path, resume=True)
    assert again["current_network_requests"] == 0
    assert {f: digest((root / f).read_bytes()) for f in files} == before
    assert len({r["evidence_id"] for r in read_csv(root / "evidence_pending.csv")}) == 4
    assert all(r["verification_status"] == "pending" for r in read_csv(root / "aliases_pending.csv"))
    # Name and 字 intentionally share the exact same complete source sentence.
    evidence = read_csv(root / "evidence_pending.csv")
    assert len({e["original_text"] for e in evidence}) == 2


def test_new_batch_automatically_recognizes_previous_candidates(bulk_config):
    path, config = bulk_config
    collect_bulk(path)
    second = collect_bulk(path, {"batch_name": "TEST_ONLY_SECOND"})
    assert second["previous_candidate_people"] == 2
    assert second["new_candidate_people"] == 0
    assert second["cumulative_candidate_people"] == 2


def test_resume_rejects_reviewed_data_and_changed_identity_config(bulk_config):
    path, config = bulk_config
    collect_bulk(path)
    with pytest.raises(ValueError, match="不能改变"):
        collect_bulk(path, {"target_total_people": 3}, resume=True)
    root = Path(config["output_root"]) / config["batch_name"]
    text = (root / "aliases_pending.csv").read_text(encoding="utf-8-sig")
    (root / "aliases_pending.csv").write_text(text.replace("待审核", "通过", 1), encoding="utf-8-sig")
    with pytest.raises(ValueError, match="人工审核"):
        collect_bulk(path, resume=True)


def test_ranges_are_configured_and_not_person_lists(bulk_config):
    _, config = bulk_config
    config["volume_ranges"] = [{"book": "新唐書", "start": 202, "end": 204, "dynasty": "唐"}]
    titles = [p["title"] for p in catalogue_plan(config)]
    assert titles[-3:] == ["新唐書/卷202", "新唐書/卷203", "新唐書/卷204"]
    assert len(titles) == len(set(titles))


def test_retry_after_supports_seconds_and_http_date():
    current = datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert retry_deadline("180", current) == current + timedelta(seconds=180)
    assert retry_deadline("Fri, 16 Oct 2026 01:00:00 GMT", current) == current + timedelta(hours=1)
    assert retry_deadline("invalid", current) == current


def test_429_persists_cooldown_and_still_allows_legitimate_cache(bulk_config, tmp_path):
    _, config = bulk_config
    config.update(offline=False, max_requests=2)
    out = tmp_path / "rate-limit-run"
    client = BatchClient(out, config, [])
    calls = []
    def limited(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 429, "TEST", {"Retry-After": "180"}, BytesIO(b"TEST RATE LIMIT"))
    client.opener.open = limited
    with pytest.raises(FetchFailure):
        client.fetch(API + "?action=parse&page=TEST_MISSING", "test")
    assert len(calls) == 1
    meta = next(json_read(p) for p in (out / "raw").glob("*.json"))
    assert meta["headers"]["Retry-After"] == "180"
    cool = json_read(Path(config["cache_dir"]) / "cooldowns.json")["zh.wikisource.org"]
    assert datetime.fromisoformat(cool["not_before"]) > datetime.now(timezone.utc) + timedelta(seconds=170)
    # A later process refuses a request before Retry-After but can read its cache.
    next_client = BatchClient(tmp_path / "next-run", config, [])
    next_client.opener.open = lambda *a, **k: pytest.fail("退避期间不得请求")
    with pytest.raises(FetchFailure):
        next_client.fetch(API + "?action=parse&page=TEST_MISSING", "test")
    cached = client.page(sample_page()["title"])
    assert cached["revision"] == 1 and len(calls) == 1


def test_grouping_never_drops_relations_or_rewrites_quotes():
    task = {"task_id": "TEST1", "source_id": "S1", "original_text": "TEST ORIGINAL", "context_before": "before", "context_after": "after", "raw_html_excerpt": "<p>TEST ORIGINAL</p>"}
    result = group_packet({"tasks": [task, {**task, "task_id": "TEST2"}]})
    assert len(result["tasks"]) == 2 and len(result["text_blocks"]) == 1
    assert result["text_blocks"][result["tasks"][0]["text_block"]]["original_text"] == "TEST ORIGINAL"


def test_explicit_free_text_adapter_preserves_text_and_downgrades_unknown_identity():
    env = {"analysis": {"summary": "TEST", "findings": [{"issue": "TEST1", "evidence": "V|L|1|-|E|仅测试原文明确记其字|D1", "risk": "A"}], "alternatives": [], "verification": ["D1=仅测试理由"]}}
    with pytest.raises(ValueError):
        parse_review(env, [{"task_id": "TEST1"}])
    result = parse_review(env, [{"task_id": "TEST1"}], allow_free_text_narration=True)["TEST1"]
    assert result["relation_type"] == "仅测试原文明确记其字"
    assert result["identity_ambiguity"] == "unresolved_identity"
    assert result["raw_identity_ambiguity"] == "-"
    assert result["narration_format"] == "verbatim_free_text"
