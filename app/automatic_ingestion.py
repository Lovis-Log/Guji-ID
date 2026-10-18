"""A/A+/B admission; structured records never pretend to be ancient quotes."""
import json
import re
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from app.batch_import import backup_database, database_counts, existing_connection
from app.collector import digest, identifier, now
from app.db import PROJECT_ROOT, TABLES, connect_db, init_db
from app.importer import COLUMNS, _validate_evidence_chain
from app.normalization import normalize_name
from app.source_acquisition import verify_hash
from app.verification import json_write

POLICY = "cbdb-a-admission-v2"
TYPE_MAP = {3: ("别名、曾用名", "name"), 4: ("字", "courtesy"),
 5: ("室名、別號", "art"), 6: ("諡號", "posthumous"), 7: ("行第", "other"),
 8: ("封爵", "other"), 9: ("小名", "other"), 10: ("小字", "other"),
 11: ("賜號", "other"), 13: ("俗名", "name"), 14: ("廟號", "other"),
 15: ("尊號", "other"), 19: ("法號", "other"), 20: ("道號", "other"), 21: ("洗名/教名", "other")}
REQUIRED_SCHEMA = {"BIOG_MAIN": {"c_personid", "c_name_chn", "c_dy"},
 "ALTNAME_DATA": {"c_personid", "c_alt_name_chn", "c_alt_name_type_code", "c_source", "c_pages"},
 "ALTNAME_CODES": {"c_name_type_code", "c_name_type_desc_chn"},
 "TEXT_CODES": {"c_textid", "c_title_chn"}, "DYNASTIES": {"c_dy", "c_dynasty_chn"},
 "BIOG_SOURCE_DATA": {"c_personid", "c_textid", "c_pages"}}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@lru_cache(maxsize=1)
def converter():
    from opencc import OpenCC
    return OpenCC("t2s")


def search_key(value):
    return converter().convert(normalize_name(value))


def migrate(db):
    init_db(db)
    schema = (PROJECT_ROOT / "app/migrations/006_automatic_admission.sql").read_text(encoding="utf-8")
    with connect_db(db) as c:
        c.executescript("BEGIN IMMEDIATE;\n" + schema + "\nCOMMIT;")


def inspect_source(source, release):
    verify_hash(source, release["sqlite_sha256"])
    with existing_connection(source) as c:
        schema = {t: [dict(r) for r in c.execute(f"PRAGMA table_info({t})")] for t in REQUIRED_SCHEMA}
        for table, fields in REQUIRED_SCHEMA.items():
            if not fields <= {r["name"] for r in schema[table]}:
                raise ValueError(f"实际CBDB架构缺少必要字段：{table}")
        count = c.execute("SELECT count(*) FROM BIOG_MAIN WHERE c_personid>0").fetchone()[0]
        if release["license_id"] != "CC-BY-NC-SA-4.0" or release["version"] > release["license_scope_date"] or count > release["license_scope_max_people"]:
            raise ValueError("CBDB日期/总量不在许可范围，禁止自动准入")
        if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("外部SQLite完整性检查失败")
        types = [dict(r) for r in c.execute("SELECT * FROM ALTNAME_CODES")]
    return {"schema": schema, "altname_codes": types, "positive_people": count}


def stage_records(source, release, staging, limit=1200):
    if not 1 <= limit <= 10000:
        raise ValueError("候选批量范围应为1–10000；更大任务应分段增量运行")
    Path(staging).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(staging)) as stage, existing_connection(source) as src:
        stage.execute("""CREATE TABLE IF NOT EXISTS candidates(
          candidate_id TEXT PRIMARY KEY, provider TEXT NOT NULL, version TEXT NOT NULL,
          payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
          decision TEXT NOT NULL DEFAULT 'PENDING', reason TEXT NOT NULL DEFAULT '',
          processed_batch TEXT)""")
        stage.execute("CREATE TABLE IF NOT EXISTS stage_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        release_hash = digest(canonical_json(release))
        previous = stage.execute("SELECT value FROM stage_meta WHERE key='release_hash'").fetchone()
        if previous and previous[0] != release_hash:
            raise ValueError("暂存区已绑定其他发布快照，不覆盖")
        stage.execute("INSERT OR IGNORE INTO stage_meta VALUES('release_hash',?)", (release_hash,))
        stage.execute("INSERT OR REPLACE INTO stage_meta VALUES('release',?)", (canonical_json(release),))
        cursor_row=stage.execute("SELECT value FROM stage_meta WHERE key='alt_cursor'").fetchone()
        offset=int(cursor_row[0]) if cursor_row else 0
        exhausted=stage.execute("SELECT 1 FROM stage_meta WHERE key='source_exhausted' AND value='1'").fetchone()
        if offset >= limit or exhausted:
            return {'source_alt_rows_requested':limit,'source_alt_rows_consumed':offset,'persons_seen':stage.execute("SELECT count(DISTINCT json_extract(payload_json,'$.person.c_personid')) FROM candidates").fetchone()[0],
                    'staged_candidates':stage.execute('SELECT count(*) FROM candidates').fetchone()[0],'cache_reused':True}
        types = {r["c_name_type_code"]: dict(r) for r in src.execute("SELECT * FROM ALTNAME_CODES")}
        dynasties = {r["c_dy"]: r["c_dynasty_chn"] for r in src.execute("SELECT * FROM DYNASTIES")}
        people_seen = set()
        rows = src.execute("SELECT * FROM ALTNAME_DATA ORDER BY c_personid,c_alt_name_chn,c_alt_name_type_code LIMIT ? OFFSET ?", (limit-offset,offset))
        consumed=0
        def save(payload):
            text = canonical_json(payload)
            cid = identifier("C-CBDB", release["version"], payload["table"], payload["key"])
            existing = stage.execute("SELECT payload_sha256 FROM candidates WHERE candidate_id=?", (cid,)).fetchone()
            if existing and existing[0] != digest(text):
                raise ValueError("同一源主键内容发生变化；不覆盖暂存记录")
            stage.execute("INSERT OR IGNORE INTO candidates(candidate_id,provider,version,payload_json,payload_sha256) VALUES(?,?,?,?,?)", (cid, "CBDB", release["version"], text, digest(text)))
        for raw in rows:
            consumed+=1
            row = dict(raw)
            pid = row["c_personid"]
            person_row = src.execute("SELECT * FROM BIOG_MAIN WHERE c_personid=?", (pid,)).fetchone()
            person = dict(person_row) if person_row else {}
            common = {"person": person, "dynasty": dynasties.get(person.get("c_dy")), "provider": "CBDB"}
            if pid not in people_seen:
                people_seen.add(pid)
                refs = [dict(r) for r in src.execute("SELECT * FROM BIOG_SOURCE_DATA WHERE c_personid=?", (pid,))]
                save({**common, "table": "BIOG_MAIN", "key": {"c_personid": pid}, "raw": person,
                      "alias": person.get("c_name_chn", ""), "alias_type": "name", "type_row": None, "bibliography": refs})
            sid = row.get("c_source")
            source_row = src.execute("SELECT c_textid,c_title_chn FROM TEXT_CODES WHERE c_textid=?", (sid,)).fetchone() if sid else None
            save({**common, "table": "ALTNAME_DATA", "key": {k: row[k] for k in ("c_personid", "c_alt_name_chn", "c_alt_name_type_code")},
                  "raw": row, "alias": row["c_alt_name_chn"], "type_row": types.get(row["c_alt_name_type_code"]),
                  "alias_type": TYPE_MAP.get(row["c_alt_name_type_code"], ("", "unknown"))[1],
                  "bibliography": dict(source_row) if source_row else None})
        stage.execute("INSERT OR REPLACE INTO stage_meta VALUES('alt_cursor',?)",(str(offset+consumed),))
        if consumed < limit-offset:
            stage.execute("INSERT OR REPLACE INTO stage_meta VALUES('source_exhausted','1')")
        stage.commit()
        return {"source_alt_rows_requested": limit, "persons_seen": len(people_seen),
                "staged_candidates": stage.execute("SELECT count(*) FROM candidates").fetchone()[0]}


def validate_auxiliary_review(response, key):
    return isinstance(response, dict) and response.get("ok") is True and response.get("record_key") == key and response.get("verdict") == "supported" and response.get("text_match") is True


def decide(payload, formal):
    p, raw, text = payload["person"], payload["raw"], payload["alias"]
    if not p or not isinstance(p.get("c_personid"), int) or p["c_personid"] <= 0:
        return "B", "missing_stable_person_id"
    name = p.get("c_name_chn")
    if not isinstance(name, str) or not 1 < len(name.strip()) <= 30 or re.search(r"[\x00-\x1f]", name) or any(x in name for x in ("未詳", "不詳", "佚名", "[", "]", "(", ")", "?", "？", "+", "□")):
        return "B", "ambiguous_or_placeholder_person"
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 40 or text in {"未詳", "不詳", "未知", "某"} or re.search(r"[\x00-\x1f\[\]()?？+□/；;]", text) or text != text.strip():
        return "B", "invalid_or_uncertain_alias"
    if payload["table"] == "ALTNAME_DATA":
        code, row = raw["c_alt_name_type_code"], payload["type_row"]
        if code not in TYPE_MAP or not row or row["c_name_type_desc_chn"] != TYPE_MAP[code][0] or payload["alias_type"] != TYPE_MAP[code][1]:
            return "B", "unknown_or_conflicting_type"
        if raw.get("c_source") and not payload["bibliography"]:
            return "B", "dangling_bibliographic_source"
        if text == name and payload["alias_type"] != "name":
            return "B", "name_and_other_type_identical"
    mapped = formal.execute("SELECT * FROM external_people WHERE provider='CBDB' AND external_id=?", (str(p["c_personid"]),)).fetchone()
    if mapped:
        current = formal.execute("SELECT canonical_name,dynasty FROM people WHERE person_id=?", (mapped["person_id"],)).fetchone()
        if not current or current[0] != name or current[1] != payload.get('dynasty'):
            return "B", "external_identity_conflict"
    else:
        for old in formal.execute("SELECT canonical_name FROM people WHERE person_id NOT IN (SELECT person_id FROM external_people WHERE provider='CBDB')"):
            if search_key(old[0]) == search_key(name):
                return "B", "possible_legacy_identity_overlap"
    if 'ai_review' in payload and not validate_auxiliary_review(payload["ai_review"], payload["key"]):
        return "B", "invalid_or_failed_ai_review"
    return "A", "official_structured_record"


def put(c, table, row, batch, inserted):
    columns = COLUMNS[table]
    clean = {k: row.get(k) for k in columns}
    existing = c.execute(f"SELECT * FROM {table} WHERE {columns[0]}=?", (clean[columns[0]],)).fetchone()
    if existing:
        differences=[k for k in columns if existing[k]!=clean[k]]
        # Preserve optional facts filled by Stage 6.5 when an old source payload omits them.
        if table=='people':
            differences=[k for k in differences if not (k in {'birth_year','death_year','native_place','identity_description'} and clean[k] is None)]
        if differences:
            raise ValueError(f"已有{table}记录冲突，禁止覆盖：{clean[columns[0]]}")
        return False
    c.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(clean.values()))
    c.execute("INSERT INTO batch_entities VALUES(?,?,?,?)", (batch, table, clean[columns[0]], digest(canonical_json(clean))))
    inserted[table] += 1
    return True


def materialize(c, payload, release, batch, inserted):
    p = payload["person"]
    pid = f"P-CBDB-{p['c_personid']}"
    eid = identifier("E-CBDB", release["version"], payload["table"], payload["key"])
    name, kind = payload["alias"], payload["alias_type"]
    aid = identifier("A-CBDB", pid, name, kind)
    sid = identifier("S-CBDB", release["version"], payload["table"])
    release_id = "CBDB-" + release["version"]
    new_person = put(c, "people", {"person_id": pid, "canonical_name": p["c_name_chn"], "dynasty": payload.get("dynasty"),
        "identity_description": f"CBDB人物ID {p['c_personid']}；结构化来源核准，非古籍原文核验。", "verification_status": "verified"}, batch, inserted)
    if new_person:
        ptext = canonical_json(p)
        c.execute("INSERT INTO external_people VALUES(?,?,?,?,?,?,?)", ("CBDB", str(p["c_personid"]), pid, release_id, ptext, digest(ptext), batch))
    put(c, "aliases", {"alias_id": aid, "person_id": pid, "alias_name": name, "normalized_name": normalize_name(name), "alias_type": kind, "verification_status": "verified"}, batch, inserted)
    put(c, "sources", {"source_id": sid, "book_title": "中国历代人物传记资料库（CBDB）", "volume": release["version"], "chapter": payload["table"],
        "platform": "CBDB 官方单机版 / Hugging Face", "original_url": release["download_url"], "edition": "CBDB " + release["version"] + " / CC BY-NC-SA 4.0"}, batch, inserted)
    public_record = {"table": payload["table"], "key": payload["key"], "c_name_chn": p["c_name_chn"], "c_personid": p["c_personid"], "alias_name": name,
        "c_alt_name_type_code": payload["raw"].get("c_alt_name_type_code"), "type_label": payload["type_row"]["c_name_type_desc_chn"] if payload["type_row"] else "姓名", "bibliography": payload["bibliography"]}
    record = canonical_json(payload)
    notes = {"verification_method": "A", "evidence_kind": "structured_record", "record_key": payload["key"], "source_version": release["version"],
        "relation_type": "结构化数据库直接记录的称呼关系", "evidence_type": "结构化数据库记录（非古籍原文）", "remarks": "仅证明CBDB在此版本记录了该关系；未取得古籍原句。", "license_id": release["license_id"]}
    inserted_e = put(c, "evidences", {"evidence_id": eid, "alias_id": aid, "source_id": sid, "original_text": json.dumps(public_record, ensure_ascii=False, indent=2),
        "usage_relation": "supports", "evidence_nature": "direct", "verification_status": "verified", "notes": canonical_json(notes)}, batch, inserted)
    if inserted_e:
        c.execute("INSERT INTO automatic_evidence VALUES(?,?,?,?,?,?,?,?,?,?)", (eid, release_id, "A", "structured_record", payload["table"], canonical_json(payload["key"]), record, digest(record), canonical_json({"sqlite_sha256": release["sqlite_sha256"], "key": payload["key"]}), batch))
    for term in {normalize_name(name), search_key(name)}:
        c.execute("INSERT OR IGNORE INTO search_variants VALUES(?,?,?,?,?,?)", (identifier("V-CBDB", aid, term), term, pid, aid, "NFC/OpenCC t2s (retrieval only)", batch))


def quality_check(c):
    _validate_evidence_chain(c)
    if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or c.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("关键质量检查失败，整批回滚")
    if c.execute("""SELECT count(*) FROM automatic_evidence p JOIN evidences e USING(evidence_id)
      WHERE p.method='A' AND (p.evidence_kind!='structured_record' OR json_extract(e.notes,'$.verification_method')!='A')""").fetchone()[0]:
        raise ValueError("结构化证据类型不一致")


def payload_matches_source(src, p):
    person = p.get("person", {})
    row = src.execute("SELECT * FROM BIOG_MAIN WHERE c_personid=?", (p["key"].get("c_personid"),)).fetchone()
    if row is None or dict(row) != person:
        return False
    dy = src.execute("SELECT c_dynasty_chn FROM DYNASTIES WHERE c_dy=?", (person.get("c_dy"),)).fetchone()
    if p.get("dynasty") != (dy[0] if dy else None):
        return False
    if p["table"] == "BIOG_MAIN":
        return p["raw"] == person and p["alias"] == person["c_name_chn"] and p["alias_type"] == "name"
    if p["table"] != "ALTNAME_DATA":
        return False
    key = p["key"]
    row = src.execute("SELECT * FROM ALTNAME_DATA WHERE c_personid=? AND c_alt_name_chn=? AND c_alt_name_type_code=?", (key["c_personid"], key["c_alt_name_chn"], key["c_alt_name_type_code"])).fetchone()
    if row is None or dict(row) != p["raw"] or p["alias"] != row["c_alt_name_chn"]:
        return False
    code = src.execute("SELECT * FROM ALTNAME_CODES WHERE c_name_type_code=?", (row["c_alt_name_type_code"],)).fetchone()
    return (dict(code) if code else None) == p["type_row"]


def conflicting_source_identities(src, person):
    """Same dynasty + full name + courtesy on different IDs is unresolved.

    Equal names alone never merge or reject people. This stronger overlapping
    signature is quarantined, even if the other ID is outside the chosen slice.
    Index years are not birth years and are not used to guess away a conflict.
    """
    if not person or not person.get('c_personid'):
        return []
    rows=src.execute("""SELECT DISTINCT b.c_personid,b.c_dy FROM ALTNAME_DATA own
      JOIN ALTNAME_DATA other ON own.c_alt_name_chn=other.c_alt_name_chn AND other.c_alt_name_type_code=4
      JOIN BIOG_MAIN b ON b.c_personid=other.c_personid
      WHERE own.c_personid=? AND own.c_alt_name_type_code=4 AND b.c_name_chn=? AND b.c_personid!=?""",
      (person['c_personid'],person['c_name_chn'],person['c_personid'])).fetchall()
    return sorted({r['c_personid'] for r in rows if not r['c_dy'] or not person.get('c_dy') or r['c_dy']==person['c_dy']})


def run_pipeline(source, release, staging, db, output, *, target_people=400, batch_size=50, max_aliases=2, dry_run=False, max_batches=None):
    if not 1 <= batch_size <= 200 or target_people < 1 or not 1 <= max_aliases <= 10:
        raise ValueError("无效批次/目标/称呼上限")
    inspect_source(source, release)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if dry_run:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "work", prefix="auto-dry-") as tmp:
            clone, stage_copy = Path(tmp) / "dry.db", Path(tmp) / "stage.db"
            if Path(db).exists():
                with existing_connection(db) as src, closing(sqlite3.connect(clone)) as dest:
                    src.backup(dest)
            with closing(sqlite3.connect(staging)) as src, closing(sqlite3.connect(stage_copy)) as dest:
                src.backup(dest)
            result = run_pipeline(source, release, stage_copy, clone, output / "simulation", target_people=target_people, batch_size=batch_size, max_aliases=max_aliases, max_batches=max_batches)
            result.update(dry_run=True, formal_database_writes=0)
            json_write(output / "dry_run.json", result)
            return result
    # Also cover the additive schema/metadata operation with a backup.
    migration_backup = backup_database(db, output / "migration_backups")
    migrate(db)
    release_id = "CBDB-" + release["version"]
    with connect_db(db) as formal, closing(sqlite3.connect(staging)) as stage, existing_connection(source) as src:
        stage.row_factory = sqlite3.Row
        stage.execute('CREATE TABLE IF NOT EXISTS source_identity_conflicts(candidate_id TEXT PRIMARY KEY,other_ids_json TEXT NOT NULL,source_sha256 TEXT NOT NULL)')
        identity_cache={}
        existing = formal.execute("SELECT sqlite_sha256 FROM data_releases WHERE release_id=?", (release_id,)).fetchone()
        if existing and existing[0] != release["sqlite_sha256"]:
            raise ValueError("相同发布版本文件哈希冲突")
        formal.execute("INSERT OR IGNORE INTO data_releases VALUES(?,?,?,?,?,?,?)", (release_id, "CBDB", release["version"], release["sqlite_sha256"], release["archive_sha256"], release["license_id"], canonical_json(release)))
        for row in stage.execute("SELECT * FROM candidates ORDER BY candidate_id"):
            payload = json.loads(row["payload_json"])
            if digest(row["payload_json"]) != row["payload_sha256"] or not payload_matches_source(src, payload):
                decision, reason = "B", "source_snapshot_or_record_mismatch"
            else:
                person=payload['person']
                pid=person.get('c_personid')
                if pid not in identity_cache:
                    identity_cache[pid]=conflicting_source_identities(src,person)
                if identity_cache[pid]:
                    decision,reason='B','ambiguous_source_identity_signature'
                    stage.execute('INSERT OR REPLACE INTO source_identity_conflicts VALUES(?,?,?)',(row['candidate_id'],canonical_json(identity_cache[pid]),release['sqlite_sha256']))
                else:
                    decision, reason = decide(payload, formal)
            stage.execute("UPDATE candidates SET decision=?,reason=?,processed_batch=CASE WHEN ?='B' THEN NULL ELSE processed_batch END WHERE candidate_id=?", (decision, reason,decision,row["candidate_id"]))
        stage.commit()
        eligible = {}
        for row in stage.execute("SELECT * FROM candidates WHERE decision='A' ORDER BY json_extract(payload_json,'$.person.c_personid'),json_extract(payload_json,'$.table')='BIOG_MAIN' DESC,candidate_id"):
            p = json.loads(row["payload_json"])
            eligible.setdefault(p["person"]["c_personid"], []).append((dict(row), p))
        committed, total_inserted = [], Counter({t: 0 for t in TABLES})
        existing_people = formal.execute("SELECT count(*) FROM people").fetchone()[0]
        choices = []
        for external_id, items in eligible.items():
            pid = f"P-CBDB-{external_id}"
            exists = formal.execute("SELECT 1 FROM people WHERE person_id=?", (pid,)).fetchone()
            canonical = [item for item in items if item[1]["table"] == "BIOG_MAIN"]
            alternate = [item for item in items if item[1]["table"] == "ALTNAME_DATA"][:max_aliases]
            if not canonical or not alternate or (not exists and existing_people >= target_people):
                continue
            if not exists:
                existing_people += 1
            choices.append(canonical + alternate)
        for index in range(0, len(choices), batch_size):
            if max_batches is not None and len(committed) >= max_batches:
                break
            selected = [item for group in choices[index:index+batch_size] for item in group]
            manifest = [(row["candidate_id"], row["payload_sha256"]) for row, _ in selected]
            checksum = digest(canonical_json([POLICY, release_id, manifest]))
            batch = "AB-" + checksum[:24]
            previous = formal.execute("SELECT status FROM auto_batches WHERE batch_id=?", (batch,)).fetchone()
            if previous and previous[0] == "committed":
                for row,payload in selected:
                    stage.execute('UPDATE candidates SET processed_batch=? WHERE candidate_id=?',(batch,row['candidate_id']))
                stage.commit()
                continue
            if all(formal.execute("SELECT 1 FROM automatic_evidence WHERE evidence_id=?", (identifier("E-CBDB", release["version"], p["table"], p["key"]),)).fetchone() for _, p in selected):
                for row,payload in selected:
                    eid=identifier('E-CBDB',release['version'],payload['table'],payload['key'])
                    actual=formal.execute('SELECT batch_id FROM automatic_evidence WHERE evidence_id=?',(eid,)).fetchone()[0]
                    stage.execute('UPDATE candidates SET processed_batch=? WHERE candidate_id=?',(actual,row['candidate_id']))
                stage.commit()
                continue
            backup = backup_database(db, output / "backups")
            before, inserted = database_counts(db), Counter({t: 0 for t in TABLES})
            formal.execute("BEGIN IMMEDIATE")
            try:
                if previous:
                    formal.execute("DELETE FROM batch_entities WHERE batch_id=?", (batch,))
                    formal.execute("UPDATE auto_batches SET status='committed',imported_at=?,counts_json='{}',backup_json=? WHERE batch_id=?", (now(), canonical_json(backup), batch))
                else:
                    formal.execute("INSERT INTO auto_batches VALUES(?,?,'committed',?,?,?,?)", (batch, release_id, now(), checksum, "{}", canonical_json(backup)))
                for row, payload in selected:
                    if digest(row["payload_json"]) != row["payload_sha256"] or decide(payload, formal)[0] != "A":
                        raise ValueError("待写入记录校验变化，停止批次")
                    materialize(formal, payload, release, batch, inserted)
                quality_check(formal)
                formal.execute("UPDATE auto_batches SET counts_json=? WHERE batch_id=?", (canonical_json(dict(inserted)), batch))
                formal.execute("INSERT INTO auto_events(batch_id,event_type,occurred_at,details_json) VALUES(?,'import',?,?)", (batch, now(), canonical_json(dict(inserted))))
                formal.commit()
            except Exception as exc:
                formal.rollback()
                json_write(output / (batch + "-FAILED.json"), {"batch_id": batch, "status": "failed_rolled_back", "error": str(exc), "backup": backup, "before": before, "after": database_counts(db)})
                raise
            for row, _ in selected:
                stage.execute("UPDATE candidates SET processed_batch=? WHERE candidate_id=?", (batch, row["candidate_id"]))
            stage.commit()
            item = {"batch_id": batch, "manifest": manifest, "inserted": dict(inserted), "backup": backup, "counts_before": before, "counts_after": database_counts(db)}
            json_write(output / (batch + ".json"), item)
            committed.append(item)
            total_inserted.update(inserted)
        reason_counts = dict(stage.execute("SELECT reason,count(*) FROM candidates WHERE decision='B' GROUP BY reason").fetchall())
        staged = stage.execute("SELECT count(*) FROM candidates").fetchone()[0]
        quarantined = sum(reason_counts.values())
        formal.execute("INSERT OR REPLACE INTO pipeline_summary VALUES(1,?,?,?,?)", (now(), staged, quarantined, canonical_json(reason_counts)))
        quality_check(formal)
    report = {"status": "passed", "policy": POLICY, "dry_run": False, "formal_database_writes": sum(total_inserted.values()),
              "batches_committed_this_run": len(committed), "inserted": dict(total_inserted), "counts_after": database_counts(db),
              "staged_candidates": staged, "quarantined": quarantined, "quarantine_reasons": reason_counts,
              "target_people": target_people, "target_met": database_counts(db)["people"] >= target_people, "new_ai_calls": 0, "migration_backup": migration_backup}
    json_write(output / ("run-" + now().replace(":", "").replace("+", "_") + ".json"), report)
    return report


def rollback_batch(db, batch, output):
    backup = backup_database(db, Path(output) / "rollback_backups")
    with connect_db(db) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            state = c.execute("SELECT status FROM auto_batches WHERE batch_id=?", (batch,)).fetchone()
            if not state or state[0] != "committed":
                raise ValueError("不是可回滚的已提交批次")
            owned = c.execute("SELECT * FROM batch_entities WHERE batch_id=?", (batch,)).fetchall()
            for item in owned:
                table = item["table_name"]
                row = c.execute(f"SELECT * FROM {table} WHERE {COLUMNS[table][0]}=?", (item["entity_id"],)).fetchone()
                if row is None or digest(canonical_json(dict(row))) != item["row_sha256"]:
                    raise ValueError("批次拥有的记录已改变，停止回滚保护后续修改")
            c.execute("DELETE FROM automatic_evidence WHERE batch_id=?", (batch,))
            c.execute("DELETE FROM search_variants WHERE batch_id=?", (batch,))
            c.execute("DELETE FROM external_people WHERE created_batch=?", (batch,))
            for table in ("evidences", "aliases", "sources", "people"):
                for item in owned:
                    if item["table_name"] == table:
                        c.execute(f"DELETE FROM {table} WHERE {COLUMNS[table][0]}=?", (item["entity_id"],))
            c.execute("UPDATE auto_batches SET status='rolled_back' WHERE batch_id=?", (batch,))
            c.execute("INSERT INTO auto_events(batch_id,event_type,occurred_at,details_json) VALUES(?,'rollback',?,?)", (batch, now(), canonical_json({'backup':backup,'removed_entities':[dict(r) for r in owned]})))
            c.execute('DELETE FROM batch_entities WHERE batch_id=?',(batch,))
            quality_check(c)
            c.commit()
        except Exception:
            c.rollback()
            raise
    return {"batch_id": batch, "status": "rolled_back", "backup": backup, "counts_after": database_counts(db)}
