"""Optional licensed local classical text support; never turn DB fields into quotes."""
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen

from app.automatic_ingestion import canonical_json, put, quality_check
from app.batch_import import backup_database, existing_connection
from app.collector import digest, identifier, now
from app.db import TABLES, connect_db
from app.source_acquisition import file_hash
from app.verification import json_read, json_write


def github_get(url):
    with urlopen(Request(url, headers={"User-Agent": "GujiIDCourse/0.6"}), timeout=35) as r:
        return r.read()


def acquire_texts(source, staging, output, max_volumes=10):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    receipt = root / "texts.json"
    if receipt.exists():
        result = json_read(receipt)
        for item in result["files"]:
            if file_hash(root / item["path"]) != item["sha256"]:
                raise ValueError("已保存古籍正文哈希不符")
        return result
    repo = "kanripo/KR2a0032"
    commit = json.loads(github_get(f"https://api.github.com/repos/{repo}/commits/master"))["sha"]
    listing = json.loads(github_get(f"https://api.github.com/repos/{repo}/contents?ref={commit}"))
    json_write(root / "repository_listing.json", listing)
    (root / "Readme.org").write_bytes(github_get(f"https://raw.githubusercontent.com/{repo}/{commit}/Readme.org"))
    with existing_connection(source) as src, closing(sqlite3.connect(staging)) as stage:
        ids = {r[0] for r in src.execute("SELECT c_textid FROM TEXT_CODES WHERE c_title_chn='宋史'")}
        volumes = Counter()
        for (text,) in stage.execute("SELECT payload_json FROM candidates WHERE json_extract(payload_json,'$.table')='BIOG_MAIN'"):
            payload = json.loads(text)
            for ref in payload["bibliography"]:
                match = re.match(r"(\d{1,3})[.:：]", str(ref.get("c_pages") or ""))
                if ref["c_textid"] in ids and match:
                    volumes[int(match[1])] += 1
            # These are download leads only. Notes are not admitted as quotes;
            # a later independent full-name/courtesy declaration is required.
            for match in re.finditer(r"\bSS\s*[;,.]?\s*(\d{3})[.:]", payload['person'].get('c_notes') or ''):
                volumes[int(match[1])] += 1
    available = {item["name"]: item for item in listing if item["type"] == "file"}
    files = []
    for volume, frequency in volumes.most_common():
        name = f"KR2a0032_{volume:03d}.txt"
        if name not in available:
            continue
        raw_url = f"https://raw.githubusercontent.com/{repo}/{commit}/{name}"
        raw = github_get(raw_url)
        (root / name).write_bytes(raw)
        # Git blob ID is supplied by the actual pinned repository listing.
        import hashlib
        blob_sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blob_sha != available[name]["sha"]:
            raise ValueError("古籍文件与固定Git版本的blob哈希不符")
        files.append({"path": name, "book_title": "宋史", "volume": f"卷{volume}", "chapter": f"卷{volume}",
            "edition": "Kanripo KR2a0032 / WYG（以仓库Readme及正文头信息为准）", "sha256": digest(raw), "git_blob_sha1": blob_sha,
            "url": f"https://github.com/{repo}/blob/{commit}/{name}", "retrieval_url": raw_url})
        if len(files) >= max_volumes:
            break
    result = {"provider": "Kanripo", "repository": repo, "commit": commit, "retrieved_at": now(),
              "license_id": "CC-BY-SA-4.0", "license_basis_url": "https://github.com/kanripo",
              "license_observation": "官方组织说明：premodern Chinese texts, Licensed as CC BY SA 4.0。",
              "files": files, "max_volumes": max_volumes}
    json_write(receipt, result)
    return result


def locate_declaration(text, person_name, courtesy):
    if len(courtesy) < 2:
        return None
    # Only a direct full-name + 字 + exact courtesy declaration is considered.
    # No pronoun resolution, substring-only co-occurrence, model-created quotes,
    # page-marker removal or cross-paragraph concatenation.
    marker = re.escape(person_name) + r"\s*[，,]?\s*字\s*" + re.escape(courtesy)
    for match in re.finditer(marker, text):
        end = min(len(text), match.end() + 65)
        tail = text[match.end():end]
        boundary = re.search(r"人[也，。¶\s]", tail)
        if not boundary:
            continue
        right = match.end() + boundary.end()
        quote = text[match.start():right]
        if "\n\n" in quote:
            continue
        return {"start": match.start(), "end": right, "quote": quote,
                "line_start": text.count("\n", 0, match.start()) + 1,
                "line_end": text.count("\n", 0, right) + 1,
                "context": text[max(0, match.start()-100):min(len(text), right+150)]}
    return None


def enrich_classical(source, text_dir, db, output):
    root, output = Path(text_dir), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = json_read(root / "texts.json")
    if manifest.get('license_id') != 'CC-BY-SA-4.0' or manifest.get('repository') != 'kanripo/KR2a0032':
        raise ValueError('不在已确认的古籍许可与仓库范围')
    verified_files = []
    for item in manifest["files"]:
        if not re.fullmatch(r'KR2a0032_\d{3}\.txt', item['path']):
            raise ValueError('古籍路径不安全')
        if file_hash(root / item["path"]) != item["sha256"]:
            raise ValueError("古籍文件哈希改变")
        verified_files.append((item, (root / item["path"]).read_bytes().decode("utf-8")))
    selected, rejected, attempts = [], Counter(), []
    with existing_connection(source) as src, existing_connection(db) as formal:
        for row in formal.execute("SELECT a.*,p.canonical_name,p.dynasty FROM aliases a JOIN people p USING(person_id) WHERE a.person_id LIKE 'P-CBDB-%' AND a.alias_type='courtesy'"):
            if row['dynasty'] != '宋':
                rejected['dynasty_not_supported_by_selected_corpus'] += 1
                attempts.append({'alias_id':row['alias_id'],'decision':'B','reason':'dynasty_not_supported_by_selected_corpus'})
                continue
            same = src.execute("""SELECT count(DISTINCT b.c_personid) FROM BIOG_MAIN b JOIN ALTNAME_DATA a USING(c_personid)
                WHERE b.c_name_chn=? AND a.c_alt_name_chn=? AND a.c_alt_name_type_code=4""", (row["canonical_name"], row["alias_name"])).fetchone()[0]
            if same != 1:
                rejected["nonunique_name_courtesy_pair"] += 1
                attempts.append({'alias_id':row['alias_id'],'decision':'B','reason':'nonunique_name_courtesy_pair'})
                continue
            located = False
            for item, text in verified_files:
                match = locate_declaration(text, row["canonical_name"], row["alias_name"])
                if match:
                    assert text[match["start"]:match["end"]] == match["quote"]
                    selected.append((dict(row), item, match))
                    attempts.append({'alias_id':row['alias_id'],'decision':'A+','reason':'unique_pair_and_located_declaration','file':item['path'],'start':match['start'],'end':match['end']})
                    located = True
                    break
            if not located:
                rejected["no_strict_declaration_in_selected_volumes"] += 1
                attempts.append({'alias_id':row['alias_id'],'decision':'B','reason':'no_strict_declaration_in_selected_volumes'})
    json_write(output / ('text_candidates-'+now().replace(':','').replace('+','_')+'.json'), attempts)
    if not selected:
        result = {"status": "no_auto_text_admission", "admitted": 0, "unconfirmed": dict(rejected), "new_ai_calls": 0}
        json_write(output / "text_admission.json", result)
        return result
    signature = digest(canonical_json([(r["alias_id"], item["sha256"], m["start"], m["end"]) for r, item, m in selected]))
    batch = "AT-" + signature[:24]
    backup = backup_database(db, output / "backups")
    inserted = Counter({t: 0 for t in TABLES})
    with connect_db(db) as c:
        if c.execute("SELECT 1 FROM auto_batches WHERE batch_id=? AND status='committed'", (batch,)).fetchone():
            result = {"status": "idempotent_noop", "batch_id": batch, "admitted": 0, "unconfirmed": dict(rejected), "new_ai_calls": 0}
            json_write(output / "text_admission_replay.json", result)
            return result
        release_id = c.execute("SELECT release_id FROM data_releases WHERE provider='CBDB' ORDER BY version DESC LIMIT 1").fetchone()[0]
        c.execute("BEGIN IMMEDIATE")
        try:
            if c.execute('SELECT 1 FROM auto_batches WHERE batch_id=?',(batch,)).fetchone():
                c.execute('DELETE FROM batch_entities WHERE batch_id=?',(batch,))
                c.execute("UPDATE auto_batches SET status='committed',imported_at=?,counts_json='{}',backup_json=? WHERE batch_id=?",(now(),canonical_json(backup),batch))
            else:
                c.execute("INSERT INTO auto_batches VALUES(?,?,'committed',?,?,?,?)", (batch, release_id, now(), signature, "{}", canonical_json(backup)))
            for row, item, match in selected:
                sid = identifier("S-KR", manifest["commit"], item["path"])
                eid = identifier("E-KR", row["alias_id"], item["sha256"], match["start"], match["end"])
                source = {"source_id": sid, "book_title": item["book_title"], "volume": item["volume"], "chapter": item["chapter"],
                    "platform": "Kanripo", "original_url": item["url"] + f"#L{match['line_start']}-L{match['line_end']}", "edition": item["edition"]}
                # One source per volume; line ranges belong to evidence locators,
                # so a shared source URL must not change between quotations.
                source["original_url"] = item["url"]
                put(c, "sources", source, batch, inserted)
                notes = {"verification_method": "A+", "evidence_kind": "classical_text", "relation_type": "古籍明确记载姓名与字（程序核对）",
                    "evidence_type": "史传记载", "context": match["context"], "remarks": "CBDB结构化关系与本地古籍明确姓名/字句对应；不是人物本人自称。", "license_id": manifest["license_id"],
                    "locator": {"commit": manifest["commit"], "file": item["path"], "sha256": item["sha256"], **{k: match[k] for k in ("start", "end", "line_start", "line_end")}}}
                if put(c, "evidences", {"evidence_id": eid, "alias_id": row["alias_id"], "source_id": sid, "original_text": match["quote"],
                    "usage_relation": "supports", "evidence_nature": "direct", "verification_status": "verified", "notes": canonical_json(notes)}, batch, inserted):
                    locator = notes["locator"]
                    c.execute("INSERT INTO automatic_evidence VALUES(?,?,?,?,?,?,?,?,?,?)", (eid, release_id, "A+", "classical_text", "Kanripo", canonical_json(locator), canonical_json(match), digest(canonical_json(match)), canonical_json(locator), batch))
            quality_check(c)
            c.execute("UPDATE auto_batches SET counts_json=? WHERE batch_id=?", (canonical_json(dict(inserted)), batch))
            c.execute("INSERT INTO auto_events(batch_id,event_type,occurred_at,details_json) VALUES(?,'text_enrichment',?,?)", (batch, now(), canonical_json(dict(inserted))))
            c.commit()
        except Exception as exc:
            c.rollback()
            json_write(output/(batch+'-FAILED.json'),{'batch_id':batch,'status':'failed_rolled_back','error':str(exc),'backup':backup})
            raise
    result = {"status": "passed", "batch_id": batch, "admitted": inserted["evidences"], "inserted": dict(inserted), "unconfirmed": dict(rejected), "backup": backup, "new_ai_calls": 0}
    json_write(output / "text_admission.json", result)
    return result
