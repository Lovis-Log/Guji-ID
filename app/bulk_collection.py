"""Configurable, restartable biography collection on top of collector.Client.

Discovery requires a named section and a same-name opening '字' declaration.
No person-name lists, guessed quotes, automatic identity merges or DB writes.
"""

import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.catalog import safe_source_url
from app.collector import (
    ALIAS_FIELDS, API, EVIDENCE_FIELDS, LOG_FIELDS, PEOPLE_FIELDS, Client,
    FetchFailure, body_bounds, digest, discover_biography, extract, identifier,
    now, plain, read_csv, source_location, write_csv,
)
from app.db import PROJECT_ROOT
from app.normalization import normalize_name
from app.verification import json_read, json_write

DISCOVERY_FIELDS = ("candidate_id", "person_id", "canonical_name", "dynasty", "courtesy", "native_place_candidate", "book_title", "volume", "chapter", "discovery_url", "revision_id", "original_opening", "raw_path", "match_status", "possible_existing_person_ids", "ambiguity", "selected")


def project_path(value):
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    if not resolved.is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError("批次读写位置必须位于固定项目目录内")
    return resolved


def retry_deadline(value, current=None):
    current = current or datetime.now(timezone.utc)
    try:
        return current + timedelta(seconds=max(0, int(value)))
    except (ValueError, TypeError):
        try:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return max(current, stamp)
        except (ValueError, TypeError, OverflowError):
            return current


class BatchClient(Client):
    """Reuse successful saved responses; rate-limit state survives processes."""

    def __init__(self, output, config, cache_roots):
        super().__init__(output, timeout=config["timeout"], interval=config["interval"], max_requests=config["max_requests"], resume=True)
        self.config = config
        self.cache_root = project_path(config["cache_dir"])
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.cooldown_path = self.cache_root / "cooldowns.json"
        self.cooldowns = json_read(self.cooldown_path) if self.cooldown_path.exists() else {}
        self.index = defaultdict(list)
        # The current batch is always reusable, even if external cache is off.
        roots = [self.output] + ([self.cache_root, *cache_roots] if config["use_cache"] else [])
        for root in dict.fromkeys(map(Path, roots)):
            for path in sorted((root / "raw").glob("*.json")):
                try:
                    meta = json_read(path)
                    if meta.get("http_status") == 200 and not meta.get("error"):
                        self.index[self.cache_key(meta["request_url"])].append((root, meta))
                except (OSError, ValueError, KeyError):
                    continue

    @staticmethod
    def cache_key(url):
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if parsed.hostname == "zh.wikisource.org" and query.get("action") == ["parse"] and "page" in query:
            return ("parse", query["page"][0])
        return ("url", url)

    def fetch(self, url, operation, person_id=""):
        for cache_root, meta in self.index.get(self.cache_key(url), []):
            old_url = meta["request_url"]
            key = digest(old_url)
            try:
                body = (cache_root / "raw" / (key + ".body")).read_bytes()
                if digest(body) != meta["sha256"]:
                    raise ValueError("缓存哈希错误")
                # Use the original request URL; never manufacture provenance.
                original_replay = self.replay
                original_blocked = self.blocked
                self.replay = cache_root
                self.blocked = set()  # Cached bytes remain usable during a network stop.
                try:
                    return super().fetch(old_url, operation, person_id)
                finally:
                    self.replay = original_replay
                    self.blocked = original_blocked
            except (OSError, ValueError) as exc:
                self.log("cache", old_url, "cache_invalid", str(exc), person_id)
        if self.config["offline"]:
            self.log(operation, url, "offline_missing", "只用本地缓存，没有联网补抓", person_id)
            raise FetchFailure("离线缓存缺失")
        host = urlsplit(url).hostname
        block = self.cooldowns.get(host)
        if block and datetime.fromisoformat(block["not_before"]) > datetime.now(timezone.utc):
            self.log(operation, url, "deferred_retry_after", block["not_before"], person_id)
            raise FetchFailure("来源退避尚未结束；请在 not_before 之后显式续采")
        # Preserve earlier failed responses before an explicitly resumed attempt.
        key = digest(url)
        old_meta = self.output / "raw" / (key + ".json")
        if old_meta.exists():
            old = json_read(old_meta)
            archive = self.output / "attempts" / (key + "-" + digest(json.dumps(old, sort_keys=True))[:12])
            archive.parent.mkdir(exist_ok=True)
            for suffix in (".body", ".json"):
                src = self.output / "raw" / (key + suffix)
                if src.exists():
                    shutil.copyfile(src, archive.with_suffix(suffix))
            for log in self.logs:
                if log["raw_path"] == "raw/" + key + ".body":
                    log["raw_path"] = archive.with_suffix(".body").relative_to(self.output).as_posix()
        requests_before = self.requests
        try:
            result = super().fetch(url, operation, person_id)
        except FetchFailure:
            meta_path = self.output / "raw" / (key + ".json")
            if meta_path.exists() and self.requests > requests_before:
                meta = json_read(meta_path)
                if meta.get("http_status") == 429:
                    failures = (block or {}).get("failures", 0) + 1
                    current = datetime.now(timezone.utc)
                    deadline = max(current + timedelta(seconds=min(3600, 60 * 2**min(failures-1, 6))), retry_deadline(meta.get("headers", {}).get("Retry-After", ""), current))
                    self.cooldowns[host] = {"not_before": deadline.isoformat(), "failures": failures, "status": 429}
                    json_write(self.cooldown_path, self.cooldowns)
                    self.log("retry_after", url, "deferred_retry_after", deadline.isoformat(), person_id)
            raise
        decoded, _, meta = result
        target = self.cache_root / "raw"
        target.mkdir(exist_ok=True)
        # Persistent cache is a reusable snapshot, not an overwrite of old batches.
        if not (target / (key + ".json")).exists():
            shutil.copyfile(self.output / "raw" / (key + ".body"), target / (key + ".body"))
            shutil.copyfile(self.output / "raw" / (key + ".json"), target / (key + ".json"))
        self.index[self.cache_key(url)].append((self.output, meta))
        return result


def load_config(path, overrides=None):
    config = json_read(path)
    config.update({k: v for k, v in (overrides or {}).items() if v is not None})
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", config["batch_name"]):
        raise ValueError("批次名仅允许字母、数字、下划线和连字符")
    for key, low, high in (("target_total_people", 1, 1000), ("max_requests", 0, 200), ("max_catalogue_pages", 1, 100), ("max_candidates", 1, 10000), ("max_aliases_per_person", 2, 8), ("timeout", 1, 30)):
        if not isinstance(config[key], int) or not low <= config[key] <= high:
            raise ValueError(f"配置 {key} 必须在 {low}–{high} 之间")
    if config["interval"] < 2 or not config["dynasties"] or not config["source_priority"]:
        raise ValueError("串行请求间隔至少 2 秒，并须指定朝代和来源")
    return config


def catalogue_plan(config):
    seen = set()
    for item in config["catalogues"]:
        if item["title"] not in seen and item["dynasty"] in config["dynasties"]:
            seen.add(item["title"])
            yield item
    for group in config.get("volume_ranges", []):
        if group["book"] not in {"舊唐書", "新唐書", "宋史"} or not 1 <= group["start"] <= group["end"] <= 500:
            raise ValueError("正史卷次范围无效")
        for volume in range(group["start"], group["end"]+1):
            title = group["book"] + "/卷" + str(volume)
            if title not in seen and group["dynasty"] in config["dynasties"]:
                seen.add(title)
                yield {"title": title, "dynasty": group["dynasty"]}


def previous_records(config, current):
    roots = set(project_path(p) for p in config["previous_batches"])
    # Recognize completed bulk batches automatically; ignore development copies.
    for marker in project_path(config["output_root"]).glob("*/bulk_batch.json"):
        if marker.parent.resolve() != current.resolve():
            roots.add(marker.parent.resolve())
    people, locators, names = {}, defaultdict(set), defaultdict(set)
    for root in sorted(roots):
        if root.resolve() == current.resolve():
            continue
        for p in read_csv(root / "candidate_people.csv"):
            people[p["person_id"]] = p
            names[(p["canonical_name"], p["dynasty"])].add(p["person_id"])
        for a in read_csv(root / "aliases_pending.csv"):
            if a["alias_type"] == "name":
                names[(a["alias_name"], a["dynasty"])].add(a["person_id"])
        for e in read_csv(root / "evidence_pending.csv"):
            locators[(e["book_title"], e["volume"], e["chapter"])].add(e["person_id"])
    return people, locators, names, sorted(roots)


def discover_sections(page, dynasty):
    """Only same-name opening declarations inside their own actual sections."""
    book, volume = source_location(page)
    top_level = min((int(s.get("level", 9)) for s in page["sections"]), default=2)
    for section in page["sections"]:
        # Nested biographies frequently omit the family name (e.g. 孝廣).
        # Do not silently promote a given name to a canonical full name.
        if int(section.get("level", 9)) != top_level:
            continue
        name = plain(section.get("line", ""))
        if not re.fullmatch(r"[\u3400-\u9fff]{2,4}", name):
            continue
        seed = {"person_name": name, "query_name": name, "name_variants": name, "dynasty": dynasty}
        start, end, chapter, scope = body_bounds(page, seed)
        if scope != "biography":
            continue
        body = page["text"][start:end]
        declaration = None
        for line in body.splitlines()[:6]:
            match = re.match(re.escape(name) + r"[，,]?字(?P<courtesy>[\u3400-\u9fff]{1,4})[，,。；]", line)
            if match:
                declaration = (line, match["courtesy"])
                break
        if declaration is None:
            continue
        opening, courtesy = declaration
        place = re.match(re.escape(name) + r"[，,]?字" + re.escape(courtesy) + r"[，,](?P<place>[\u3400-\u9fff]{2,14})人", opening)
        pid = identifier("P-D", "维基文库", book, volume, chapter)
        yield {"candidate_id": identifier("C", book, volume, chapter), "person_id": pid, "canonical_name": name, "dynasty": dynasty, "courtesy": courtesy, "native_place_candidate": place["place"] if place else "", "book_title": book, "volume": volume, "chapter": chapter, "discovery_url": page["url"], "revision_id": page["revision"], "original_opening": opening[:400], "raw_path": page["raw_path"], "match_status": "new_candidate", "possible_existing_person_ids": "", "ambiguity": "", "selected": False}


def alias_candidate(person, name, kind, page, snippet):
    row = {field: "" for field in ALIAS_FIELDS}
    row.update(alias_id=identifier("A", person["person_id"], name, kind), person_id=person["person_id"], person_name=person["canonical_name"], dynasty=person["dynasty"], alias_name=name, alias_type=kind, normalized_name=normalize_name(name), verification_status="pending", review_status="待审核", discovery_url=page["url"], discovery_text=snippet, discovery_raw_path=page["raw_path"], evidence_count=0, remarks="正史分节及开篇同名声明发现；仅候选，身份、类别和史料仍待核验与批准")
    return row


def collect_bulk(config_path, overrides=None, *, resume=False):
    config = load_config(config_path, overrides)
    output = project_path(config["output_root"]) / config["batch_name"]
    if output.exists() and not resume:
        raise ValueError("批次已存在；续采请显式使用 --resume")
    if output.exists():
        if not (output / "checkpoint.json").exists():
            raise ValueError("目录不是可续采批次")
        for filename in ("candidate_people.csv", "aliases_pending.csv", "evidence_pending.csv"):
            if (output / filename).exists() and any(r["verification_status"] != "pending" or r["review_status"] != "待审核" for r in read_csv(output / filename)):
                raise ValueError("已有人工审核结果，不允许续采覆盖")
    previous, locators, names, roots = previous_records(config, output)
    need = max(0, config["target_total_people"] - len(previous))
    identity_config = {k: config[k] for k in ("batch_name", "target_total_people", "dynasties", "source_priority", "catalogues", "volume_ranges", "max_aliases_per_person")}
    fingerprint = digest(json.dumps(identity_config, sort_keys=True))
    output.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output / "checkpoint.json"
    checkpoint = json_read(checkpoint_path) if checkpoint_path.exists() else {"config_digest": fingerprint, "catalogues": {}, "network_requests_total": 0}
    previous_request_total = checkpoint["network_requests_total"]
    if checkpoint["config_digest"] != fingerprint:
        raise ValueError("续采不能改变批次人物目标或来源身份配置；扩容请使用新批次名")
    client = BatchClient(output, config, roots)
    if (output / "collection_log.csv").exists():
        client.logs = read_csv(output / "collection_log.csv")
    client.log("source_policy", API, "enabled", "公开 Action API；串行、maxlag=5、缓存优先；429 保存 Retry-After 并停止网络访问。https://www.mediawiki.org/wiki/API:Etiquette")
    for platform in ("中国哲学书电子化计划", "识典古籍", "搜韵"):
        client.log("source_policy", platform, "skipped", "既有受限/授权未确认来源；本轮不重新请求正文或尝试绕过")
    people, aliases, evidences, discovery = {}, {}, {}, []
    selected_names = defaultdict(list)
    inspected = 0
    for catalogue in catalogue_plan(config):
        if len(people) >= need or inspected >= config["max_catalogue_pages"] or len(discovery) >= config["max_candidates"]:
            break
        inspected += 1
        title = catalogue["title"]
        if "维基文库" not in config["source_priority"]:
            client.log("catalogue", title, "skipped", "配置未选择本版支持的维基文库接口")
            continue
        try:
            page = client.page(title)
            checkpoint["catalogues"][title] = {"status": "success", "revision_id": page["revision"], "raw_sha256": page["raw_sha256"]}
            for candidate in discover_sections(page, catalogue["dynasty"]):
                if len(discovery) >= config["max_candidates"]:
                    break
                locator = (candidate["book_title"], candidate["volume"], candidate["chapter"])
                known = locators.get(locator) or names.get((candidate["canonical_name"], candidate["dynasty"]))
                if known:
                    candidate.update(match_status="existing_locator" if locator in locators else "possible_existing_name_variant", possible_existing_person_ids="|".join(sorted(known)), ambiguity="仅提示匹配，未自动合并新身份")
                elif selected_names[(candidate["canonical_name"], candidate["dynasty"])]:
                    candidate.update(match_status="possible_same_identity_in_other_biography", ambiguity="同名同朝另传，保留目录候选但暂不重复计入扩容")
                elif len(people) < need:
                    candidate["selected"] = True
                    seed = {"person_id": candidate["person_id"], "person_name": candidate["canonical_name"], "query_name": candidate["canonical_name"], "name_variants": candidate["canonical_name"], "dynasty": candidate["dynasty"]}
                    person = {k: "" for k in PEOPLE_FIELDS}
                    person.update(person_id=seed["person_id"], canonical_name=seed["person_name"], dynasty=seed["dynasty"], native_place=candidate["native_place_candidate"], identity_description="史传开篇摘录（待审核）：" + candidate["original_opening"], verification_status="pending", review_status="待审核", discovery_url=page["url"], raw_path=page["raw_path"], remarks="朝代为目录分类提示；籍贯与身份仅为实际原文候选，不代表人工确认")
                    found = [alias_candidate(person, person["canonical_name"], "name", page, candidate["original_opening"]), alias_candidate(person, candidate["courtesy"], "courtesy", page, candidate["original_opening"])]
                    known_aliases = {a["alias_id"] for a in found}
                    for a in discover_biography(seed, page):
                        if a["alias_id"] not in known_aliases and len(found) < config["max_aliases_per_person"]:
                            found.append(a)
                            known_aliases.add(a["alias_id"])
                    rows = extract(seed, found, page)
                    opening = candidate["original_opening"]
                    first_sentence = opening.split("。", 1)[0] + "。" if "。" in opening else opening[:160]
                    scope_start, scope_end, _, _ = body_bounds(page, seed)
                    located = page["text"].find(first_sentence, scope_start, scope_end)
                    if located >= 0:
                        for evidence in rows:
                            if evidence["alias_name"] in first_sentence:
                                evidence.update(original_text=first_sentence, context=page["text"][max(scope_start,located-160):min(scope_end,located+len(first_sentence)+200)], start_offset=located, end_offset=located+len(first_sentence))
                                evidence["evidence_id"] = identifier("E", evidence["alias_id"], evidence["source_id"], first_sentence)
                    for a in found:
                        a["evidence_count"] = sum(e["alias_id"] == a["alias_id"] for e in rows)
                        aliases[a["alias_id"]] = a
                    for e in rows:
                        evidences[e["evidence_id"]] = e
                    people[person["person_id"]] = person
                    selected_names[(candidate["canonical_name"], candidate["dynasty"])].append(person["person_id"])
                discovery.append(candidate)
            print(f"{title}：本批入选 {len(people)} / {need}，目录候选 {len(discovery)}", flush=True)
        except FetchFailure as exc:
            checkpoint["catalogues"][title] = {"status": "failed_or_deferred", "message": str(exc), "time": now()}
            client.log("catalogue", title, "failed", str(exc))
        finally:
            checkpoint["last_checkpoint_at"] = now()
            checkpoint["network_requests_total"] = previous_request_total + client.requests
            json_write(checkpoint_path, checkpoint)
            write_csv(output / "collection_log.csv", LOG_FIELDS, client.logs)
    by_name = defaultdict(set)
    for p in list(previous.values()) + list(people.values()):
        by_name[p["canonical_name"]].add(p["person_id"])
    for row in discovery:
        if len(by_name[row["canonical_name"]]) > 1:
            row["ambiguity"] = "完整姓名相同、不同传记/时代的独立候选；不得按姓名合并"
    write_csv(output / "candidate_people.csv", PEOPLE_FIELDS, people.values())
    write_csv(output / "aliases_pending.csv", ALIAS_FIELDS, aliases.values())
    write_csv(output / "evidence_pending.csv", EVIDENCE_FIELDS, evidences.values())
    write_csv(output / "discovery_candidates.csv", DISCOVERY_FIELDS, discovery)
    checkpoint["network_requests_total"] = previous_request_total + client.requests
    checkpoint["selected_person_ids"] = sorted(people)
    json_write(checkpoint_path, checkpoint)
    summary = {"batch_id": "COLL-" + fingerprint[:16], "batch_name": config["batch_name"], "discovery_policy": "primary_named_sections_only_v2", "previous_candidate_people": len(previous), "new_candidate_people": len(people), "cumulative_candidate_people": len(previous)+len(people), "target_total_people": config["target_total_people"], "discovery_pool": len(discovery), "new_alias_candidates": len(aliases), "located_evidences": len(evidences), "aliases_without_evidence": sum(int(a["evidence_count"]) == 0 for a in aliases.values()), "same_full_name_groups": sum(len(pids)>1 for pids in by_name.values()), "duplicate_or_possible_existing_candidates": sum(r["match_status"] != "new_candidate" for r in discovery), "current_network_requests": client.requests, "network_requests_total": checkpoint["network_requests_total"], "cache_or_replay_operations": sum(l["status"] == "replayed" for l in client.logs), "failed_or_deferred_catalogues": sum(c["status"] != "success" for c in checkpoint["catalogues"].values()), "human_approved": 0, "formal_imported_people": 0, "verification_status": "pending"}
    json_write(output / "effective_config.json", config)
    json_write(output / "bulk_batch.json", summary)
    lines = ["# 批量采集质量报告", "", "本报告统计候选，不是已确认历史事实；本轮没有人工批准或正式入库。", "", "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```", "", "|人物|朝代提示|独立人物 ID|", "|---|---|---|"]
    lines += [f"|{p['canonical_name']}|{p['dynasty']}|{p['person_id']}|" for p in people.values()]
    lines += ["", "候选发现只接受同名分节开篇的明确姓名/字表述；附传、省略姓氏、多人物合节暂不自动补全。不同卷/同朝同名记录只提示可能重复，不据姓名合并；不同朝代同名保留独立 ID。", "", "原始 API 响应与派生正文保存在 raw/、text/；修订链接和原文偏移在 evidence_pending.csv。429 不在同次运行反复请求，Retry-After 和指数退避截止时间写入共享缓存；续采前检查截止时间。", "", "需人工重点查看：同名身份、同朝另传的潜在重复、别号/谥号主语、缺原文、失败或延后目录，以及目录分类提示的朝代与摘录籍贯。", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def expansion_review_input(collection_dir, previous_batches, output_dir):
    """New relations and old genuinely shared appellations, all still pending."""
    from app.verification import INPUTS
    output = Path(output_dir)
    if output.exists():
        raise ValueError("核验输入目录已存在，不覆盖")
    roots = [Path(collection_dir), *map(Path, previous_batches)]
    people, aliases, evidences, logs = {}, {}, {}, []
    for index, root in enumerate(roots):
        ps = {p["person_id"]: p for p in read_csv(root / INPUTS[0])}
        aa = read_csv(root / INPUTS[1])
        ee = read_csv(root / INPUTS[2])
        shared = defaultdict(set)
        for a in aa:
            shared[a["alias_name"]].add(a["person_id"])
        chosen = {a["alias_id"] for a in aa if index == 0 or len(shared[a["alias_name"]]) > 1}
        for a in aa:
            if a["alias_id"] in chosen:
                aliases[a["alias_id"]] = a
                people[a["person_id"]] = ps[a["person_id"]]
        for e in ee:
            if e["alias_id"] in chosen:
                evidences[e["evidence_id"]] = e
        logs.extend(read_csv(root / INPUTS[3]))
    output.mkdir(parents=True)
    for root in roots:
        for folder in ("raw", "text"):
            (output / folder).mkdir(exist_ok=True)
            for source in (root / folder).glob("*"):
                target = output / folder / source.name
                if target.exists() and digest(target.read_bytes()) != digest(source.read_bytes()):
                    raise ValueError("同名原始文件内容冲突")
                if not target.exists():
                    shutil.copyfile(source, target)
    write_csv(output / INPUTS[0], PEOPLE_FIELDS, people.values())
    write_csv(output / INPUTS[1], ALIAS_FIELDS, aliases.values())
    write_csv(output / INPUTS[2], EVIDENCE_FIELDS, evidences.values())
    write_csv(output / INPUTS[3], LOG_FIELDS, logs)
    return {"people": len(people), "aliases": len(aliases), "evidences": len(evidences)}
