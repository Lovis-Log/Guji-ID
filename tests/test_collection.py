"""Synthetic transport/provenance fixtures only; never historical evidence.

All output is inside pytest's work directory; no external HTTP request is made.
"""

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from app.collector import (
    ALIAS_FIELDS, EVIDENCE_FIELDS, PEOPLE_FIELDS, Client, FetchFailure,
    collect, digest, discover, discover_biography, extract, identifier,
    load_seeds, plain, write_csv,
)
from app.collection_review import export_reviewed, import_reviewed
from app.db import connect_db


@pytest.fixture
def seed():
    return {"person_id": "TEST-SYNTHETIC-P1", "person_name": "仅测试甲", "query_name": "仅测试甲", "name_variants": "仅测试甲", "dynasty": "测试非历史朝代", "identity_hint": "仅测试，不是史料"}


def page(html, title="TEST_ONLY_SYNTHETIC_BOOK/卷1", sections=None):
    return {"title": title, "html": html, "text": plain(html), "sections": sections or [], "url": "https://zh.wikisource.org/w/index.php?oldid=1", "revision": 1, "raw_path": "raw/test.body", "raw_sha256": "", "text_path": "text/test.txt", "text_sha256": digest(plain(html)), "retrieved_at": "2026-10-16T00:00:00+08:00"}


def test_discovery_is_source_based_and_separates_same_name_people(seed):
    source = page('<div>仅测试甲，字仅测试字，號仅测试号。</div><h2>作品</h2><p>外號伪目录名</p>')
    first = discover(seed, source)
    assert {a["alias_name"] for a in first} == {"仅测试甲", "仅测试字", "仅测试号"}
    other = discover({**seed, "person_id": "TEST-SYNTHETIC-P2"}, source)
    assert not {a["alias_id"] for a in first} & {a["alias_id"] for a in other}
    assert all(a["verification_status"] == "pending" and a["review_status"] == "待审核" for a in first + other)


def test_parser_removes_styles_navigation_and_notes():
    assert plain('<style>fake</style><table class="header"><tr><td>fake</td></tr></table><p>TEST<sup>note</sup> TEXT</p><div class="licenseContainer">fake</div>') == "TEST TEXT"


def test_biography_is_scoped_to_person_and_keeps_reported_self_distinct(seed):
    source = page('<h2>仅测试甲</h2><p>仅测试甲，字仅测试字。</p><p>甲自號「仅测试号」。</p><h2>仅测试乙</h2><p>乙自號仅测试假。</p>', sections=[{"line": "仅测试甲", "level": "2"}, {"line": "仅测试乙", "level": "2"}])
    aliases = discover_biography(seed, source)
    assert "仅测试假" not in {a["alias_name"] for a in aliases}
    rows = extract(seed, aliases, source)
    assert all("乙" not in e["original_text"] for e in rows)
    art = next(e for e in rows if e["alias_type"] == "art")
    assert art["relation_type"] == "后世文献记载其自称"
    assert art["usage_relation"] == "uncertain"
    assert source["text"][art["start_offset"]:art["end_offset"]] == art["original_text"]


def test_child_biography_is_not_attributed_to_parent(seed):
    source = page('<h2>仅测试甲</h2><p>仅测试甲，字仅测试字。</p><h3>子 乙</h3><p>乙自號「仅测试假」。</p>', sections=[{"line": "仅测试甲", "level": "2"}, {"line": "子 乙", "level": "3"}])
    assert "仅测试假" not in {a["alias_name"] for a in discover_biography(seed, source)}


def test_work_title_index_and_unrelated_embedded_name_are_excluded(seed):
    aliases = discover(seed, page('<div>仅测试甲，字仅测试字。</div>'))
    source = page('<h1>仅测试字的篇名</h1><ul><li>仅测试甲的目录</li></ul><p>本文提及仅测试甲。</p><p>出游仅测试字窟。</p>', title="仅测试字的篇名")
    rows = extract(seed, aliases, source)
    assert all(e["alias_name"] != "仅测试字" for e in rows)
    assert all("目录" not in e["original_text"] for e in rows)


class FakeResponse(BytesIO):
    status = 200
    headers = {}


def test_429_saves_response_and_stops_host_without_retry(tmp_path):
    client = Client(tmp_path / "run", interval=0.5)
    calls = []

    def limited(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 429, "limited", {}, BytesIO(b"TEST rate limit"))

    client.opener.open = limited
    with pytest.raises(FetchFailure):
        client.fetch("https://example.invalid/first", "test")
    with pytest.raises(FetchFailure):
        client.fetch("https://example.invalid/second", "test")
    assert len(calls) == 1
    assert list((client.output / "raw").glob("*.body"))[0].read_bytes() == b"TEST rate limit"
    assert [log["status"] for log in client.logs] == ["restricted", "skipped_host"]


def test_request_budget_and_timeout_do_not_retry(tmp_path):
    client = Client(tmp_path / "run", max_requests=1)
    calls = []

    def timeout(request, timeout):
        calls.append(request.full_url)
        raise TimeoutError("TEST timeout")

    client.opener.open = timeout
    with pytest.raises(FetchFailure):
        client.fetch("https://example.invalid/one", "test")
    with pytest.raises(FetchFailure):
        client.fetch("https://another.invalid/two", "test")
    assert len(calls) == 1
    assert client.logs[-1]["status"] == "budget_exhausted"


def test_replay_never_networks_and_verifies_hash(tmp_path):
    archive = tmp_path / "archive"
    (archive / "raw").mkdir(parents=True)
    url = "https://example.invalid/test"
    key = digest(url)
    (archive / "raw" / (key + ".body")).write_bytes(b"TEST original")
    (archive / "raw" / (key + ".json")).write_text(json.dumps({"request_url": url, "sha256": digest(b"TEST changed"), "http_status": 200, "error": ""}), encoding="utf-8")
    client = Client(tmp_path / "run", replay=archive)
    client.opener.open = lambda *a, **k: pytest.fail("离线模式不得联网")
    with pytest.raises(FetchFailure):
        client.fetch(url, "test")
    with pytest.raises(FetchFailure):
        client.fetch("https://example.invalid/missing", "test")
    assert client.requests == 0


@pytest.fixture
def review_run(tmp_path, seed):
    root = tmp_path / "TEST_ONLY_REVIEW"
    (root / "raw").mkdir(parents=True)
    (root / "text").mkdir()
    source = page('<h2>仅测试甲</h2><p>仅测试甲，字仅测试字。</p>', sections=[{"line": "仅测试甲", "level": "2"}])
    raw = json.dumps({"parse": {"title": source["title"], "text": {"*": source["html"]}, "revid": 1}}, ensure_ascii=False).encode()
    source["raw_sha256"] = digest(raw)
    (root / source["raw_path"]).write_bytes(raw)
    (root / source["text_path"]).write_text(source["text"], encoding="utf-8")
    alias = next(a for a in discover(seed, page('<div>仅测试甲，字仅测试字。</div>')) if a["alias_type"] == "courtesy")
    evidence = extract(seed, [alias], source)[0]
    person = {field: "" for field in PEOPLE_FIELDS}
    person.update(person_id=seed["person_id"], canonical_name=seed["person_name"], review_status="待审核", verification_status="pending")
    tables = [("candidate_people.csv", PEOPLE_FIELDS, person), ("aliases_pending.csv", ALIAS_FIELDS, alias), ("evidence_pending.csv", EVIDENCE_FIELDS, evidence)]

    def save(approve=False):
        for filename, fields, row in tables:
            if approve:
                row.update(review_status="通过", reviewer="TEST_ONLY_REVIEWER", reviewed_at="2026-10-16T12:00:00+08:00")
            write_csv(root / filename, fields, [row])

    save()
    return root, tables, save


def test_pending_cannot_export_or_import_and_no_database_created(tmp_path, review_run):
    root, _, _ = review_run
    output = tmp_path / "export"
    with pytest.raises(ValueError, match="没有人工通过"):
        export_reviewed(root, output)
    assert not output.exists()
    db = tmp_path / "must_not_exist.db"
    with pytest.raises(ValueError, match="先运行"):
        import_reviewed(root, db)
    assert not db.exists()


def test_approved_chain_exports_exact_headers_and_imports_idempotently(tmp_path, review_run):
    root, _, save = review_run
    save(True)
    output = tmp_path / "export"
    assert export_reviewed(root, output) == {"people": 1, "aliases": 1, "sources": 1, "evidences": 1}
    db = tmp_path / "test.db"
    assert import_reviewed(output, db)["aliases"]["inserted"] == 1
    assert import_reviewed(output, db)["aliases"]["skipped"] == 1
    with connect_db(db) as conn:
        assert conn.execute("SELECT verification_status FROM aliases").fetchone()[0] == "verified"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("problem", ["reviewer", "reviewed_at", "usage_relation", "evidence_nature", "person_id", "original_text", "raw_path", "text_path", "source_url", "start_offset"])
def test_review_rejects_incomplete_chain_and_tampered_evidence(tmp_path, review_run, problem):
    root, tables, save = review_run
    save(True)
    evidence = tables[2][2]
    evidence[problem] = {"reviewer": "", "reviewed_at": "2026-10-16", "usage_relation": "mentions", "evidence_nature": "indirect", "person_id": "TEST_OTHER_PERSON", "original_text": "TEST invented quote", "raw_path": "../outside.body", "text_path": "../outside.txt", "source_url": "https://example.invalid/fake", "start_offset": "-2"}[problem]
    save()
    with pytest.raises(ValueError):
        export_reviewed(root, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_import_rejects_export_modified_after_review(tmp_path, review_run):
    root, _, save = review_run
    save(True)
    output = tmp_path / "export"
    export_reviewed(root, output)
    with (output / "aliases.csv").open("a", encoding="utf-8") as handle:
        handle.write("TEST tampered")
    db = tmp_path / "no.db"
    with pytest.raises(ValueError, match="CSV 已改变"):
        import_reviewed(output, db)
    assert not db.exists()


def test_duplicate_seed_ids_rejected_but_duplicate_names_allowed(tmp_path, seed):
    path = tmp_path / "seeds.csv"
    other = {**seed, "person_id": "TEST-SYNTHETIC-P2"}
    write_csv(path, seed.keys(), [seed, other])
    assert len(load_seeds(path)) == 2
    write_csv(path, seed.keys(), [seed, seed])
    with pytest.raises(ValueError, match="ID 重复"):
        load_seeds(path)


def test_unknown_seed_dynasty_and_identity_hint_may_remain_empty(tmp_path, seed):
    path = tmp_path / "seeds.csv"
    write_csv(path, seed.keys(), [{**seed, "dynasty": "", "identity_hint": ""}])
    assert load_seeds(path)[0]["dynasty"] == ""


def test_existing_run_cannot_be_overwritten(tmp_path, seed):
    seeds = tmp_path / "seeds.csv"
    write_csv(seeds, seed.keys(), [seed])
    with pytest.raises(ValueError, match="已存在"):
        collect(seeds, tmp_path)
