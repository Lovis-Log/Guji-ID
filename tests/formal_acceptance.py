"""Explicitly invoked, read-only acceptance against data/guji.db.

Run: python -m pytest tests/formal_acceptance.py -q
No production writes, fixture injection, model calls or new source fetching.
"""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.batch_import import database_counts, existing_connection
from app.catalog import get_alias, get_person, public_counts, search_people
from app.collector import digest
from app.db import PROJECT_ROOT
from app.main import create_app
from app.verification import json_read

ROOT = PROJECT_ROOT / "outputs/stage5-release"
FORMAL = PROJECT_ROOT / "data/guji.db"
MANIFEST = json_read(ROOT / "human_approval/import_manifest.json")


@pytest.fixture(scope="module", autouse=True)
def unchanged():
    before = digest(FORMAL.read_bytes())
    yield
    assert digest(FORMAL.read_bytes()) == before


def test_formal_counts_integrity_and_approval_scope():
    # Stage-six expansion must preserve the exact historic approved subset;
    # additional independently admitted records are expected, not a regression.
    actual = database_counts(FORMAL)
    assert all(actual[t] >= n for t,n in {"people":10,"aliases":17,"sources":10,"evidences":17}.items())
    assert public_counts(FORMAL)['people'] >= 10 and public_counts(FORMAL)['aliases'] >= 17
    with existing_connection(FORMAL) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {r["alias_id"] for r in MANIFEST} <= {r[0] for r in c.execute("SELECT alias_id FROM aliases")}
        for row in MANIFEST:
            stored=c.execute('SELECT person_id,alias_name,alias_type FROM aliases WHERE alias_id=?',(row['alias_id'],)).fetchone()
            assert tuple(stored)==(row['person_id'],row['alias_name'],row['alias_type'])


@pytest.mark.parametrize("row", MANIFEST, ids=lambda r: r["alias_id"])
def test_formal_name_reverse_lookup_and_evidence_identity(row):
    assert row["person_id"] in {p["person_id"] for p in search_people(FORMAL, row["person_name"])}
    assert row["person_id"] in {p["person_id"] for p in search_people(FORMAL, row["alias_name"])}
    person = get_person(FORMAL, row["person_id"])
    assert person["identity_evidences"]
    # Optional seed facts were deliberately not promoted by approval.
    assert person["dynasty"] is None and person["native_place"] is None
    alias = get_alias(FORMAL, row["alias_id"])
    assert alias["person_id"] == row["person_id"]
    assert alias["evidences"][0]["original_url"] == row["source_url"]
    with TestClient(create_app(FORMAL)) as client:
        assert row["person_id"] in client.get("/search", params={"q": row["alias_name"]}).text
        assert client.get("/people/" + row["person_id"]).status_code == 200
        assert client.get("/aliases/" + row["alias_id"]).status_code == 200


@pytest.mark.parametrize("query,identifiers", [("文忠", {"苏轼", "欧阳修"}), ("王珪", {"王珪"})])
def test_formal_disambiguation_preserves_two_independent_people(query, identifiers):
    results = search_people(FORMAL, query)
    assert len(results) >= 2 and len({p["person_id"] for p in results}) == len(results)
    assert identifiers <= {p["canonical_name"] for p in results}
    expected={r['person_id'] for r in MANIFEST if r['alias_name']==query or (query=='王珪' and r['person_name']=='王珪')}
    assert expected <= {p['person_id'] for p in results}
    urls = {e["original_url"] for p in results for e in p["identity_evidences"]}
    assert len(urls) >= 2
    text = "".join(e["original_text"] for p in results for e in p["identity_evidences"])
    if query == "王珪":
        assert all(s in text for s in ("叔玠", "禹玉", "太原祁", "成都華陽"))


def test_formal_excluded_relations_and_people_are_invisible():
    excluded = json_read(ROOT / "human_approval/user_authorization.json")["excluded_alias_ids"]
    assert len(excluded) == 38
    with TestClient(create_app(FORMAL)) as client:
        for aid in excluded:
            assert get_alias(FORMAL, aid) is None
            assert client.get("/aliases/" + aid).status_code == 404
        # A later independent CBDB record may validly use a term also present in
        # a held old candidate. Its visibility must not upgrade that old ID.
        # The held Tang NAME alias is absent even though the approved person can
        # be searched by their core canonical identity and approved courtesy.
        assert get_alias(FORMAL, "A-0d5d106989f70b80b344b781") is None
