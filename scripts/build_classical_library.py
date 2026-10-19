"""Build the Stage 7 classical library from pinned local source files.

The builder is deterministic and idempotent:
  * chapters are stored once per source edition, keyed by a stable id;
  * every quote is located in the stored chapter and checked by offset/hash;
  * a missing pattern is reported, never filled with invented text;
  * rerunning does not duplicate people, aliases, evidence or links.

Run from the project root:

    python -m scripts.build_classical_library --dry-run
    python -m scripts.build_classical_library
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import traceback
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.classical_text import (
    KANRIPO_TRANSFORM,
    WIKISOURCE_TRANSFORM,
    clean_wikitext,
    locate_quote,
    normalised_quote,
    normalize_for_search,
    parse_kanripo,
    parse_wikisource,
    sentence_context,
    sha256_text,
    verify_quote,
)
from app.db import connect_db, init_db
from app.normalization import normalize_name

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "guji.db"
WS_CONFIG = PROJECT_ROOT / "data" / "classical" / "wikisource_pages.json"
WS_MANIFEST = PROJECT_ROOT / "data" / "acquisition" / "wikisource_manifest.json"
KR_MANIFEST = PROJECT_ROOT / "data" / "acquisition" / "kanripo_manifest.json"
A_SEED = PROJECT_ROOT / "data" / "classical" / "a_people.json"
NEW_PEOPLE = PROJECT_ROOT / "data" / "classical" / "new_people.json"
REPORT_DIR = PROJECT_ROOT / "data" / "reports"
RUN_ID = "STG7-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

WS_LICENSE = "CC BY-SA 4.0"
WS_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
KR_LICENSE = "CC BY-SA 4.0"
KR_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def short_hash(*parts: object, length: int = 16) -> str:
    value = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def cc_s2t(value: str) -> str:
    try:
        from opencc import OpenCC
        return OpenCC("s2t").convert(value)
    except Exception:
        return value


def cc_t2s(value: str) -> str:
    try:
        from opencc import OpenCC
        return OpenCC("t2s").convert(value)
    except Exception:
        return value


def slug(value: str) -> str:
    return short_hash(value, length=12)


def volume_sequence(volume: str, fallback: int = 1) -> int:
    """Stable ordering key from a juan label such as 卷338 or 卷190下."""
    match = re.search(r"(\d+)", volume or "")
    if not match:
        return fallback
    value = int(match.group(1)) * 10
    if "上" in volume:
        value += 1
    elif "下" in volume:
        value += 2
    elif "中" in volume:
        value += 1
    return value or fallback


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_run(con, dry_run: bool) -> None:
    con.execute(
        "INSERT OR REPLACE INTO classical_processing_runs"
        "(run_id, started_at, status, config_sha256, stats_json) VALUES(?,?,?,?,?)",
        (RUN_ID, now(), "running",
         sha256_text(A_SEED.read_text(encoding="utf-8") + NEW_PEOPLE.read_text(encoding="utf-8")),
         "{}"),
    )


def work_id_for_ws(work: str) -> str:
    return "W-WS-" + slug(work)


def work_id_for_kr(repo: str) -> str:
    return "W-KR-" + repo


class Builder:
    def __init__(self, con, *, dry_run: bool = False):
        self.con = con
        self.dry_run = dry_run
        self.stats = Counter()
        self.gaps: list[dict] = []
        self.conflicts: list[dict] = []
        self.people_by_name: dict[str, dict] = {}
        self.people_by_norm: dict[str, dict] = {}
        self.chapters_by_key: dict[str, dict] = {}
        self.chapters_by_work: dict[str, list[dict]] = defaultdict(list)
        self.ws_pages: dict[str, dict] = {}
        self.ws_manifest_path = WS_MANIFEST
        self.ws_config: dict[str, dict] = {}
        self.kr_manifest: dict = {}
        self.source_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------ setup
    def load(self) -> None:
        for row in self.con.execute("SELECT * FROM people"):
            person = dict(row)
            self.people_by_name[person["canonical_name"]] = person
            self.people_by_norm[normalize_for_search(person["canonical_name"])] = person
        for row in self.con.execute("SELECT * FROM classical_chapters"):
            chapter = dict(row)
            self.chapters_by_work[chapter["work_id"]].append(chapter)
        for row in self.con.execute("SELECT * FROM classical_works"):
            self.source_cache[row["work_id"]] = dict(row)
        self.ws_config = {page["key"]: page for page in load_json(WS_CONFIG)["pages"]}
        if self.ws_manifest_path.exists():
            self.ws_pages = load_json(self.ws_manifest_path).get("pages", {})
        if KR_MANIFEST.exists():
            self.kr_manifest = load_json(KR_MANIFEST).get("works", {})

    def find_person(self, name: str, explicit: str | None = None) -> dict | None:
        if explicit:
            row = self.con.execute("SELECT * FROM people WHERE person_id=?", (explicit,)).fetchone()
            if row:
                return dict(row)
        if name in self.people_by_name:
            return self.people_by_name[name]
        for variant in (cc_t2s(name), cc_s2t(name)):
            row = self.people_by_norm.get(normalize_for_search(variant))
            if row:
                return row
        return None

    # --------------------------------------------------------------- chapters
    def insert_work(self, work_id: str, title: str, author: str, dynasty: str,
                    category: str, platform: str, url: str, edition: str,
                    license_id: str, license_url: str, retrieved_at: str,
                    commit: str = "", description: str = "") -> None:
        self.con.execute(
            """INSERT OR IGNORE INTO classical_works
               (work_id,title,author,dynasty,category,source_platform,source_url,
                digital_edition,license_id,license_url,license_scope,retrieved_at,
                source_commit,text_sha256,description,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (work_id, title, author, dynasty, category, platform, url, edition,
             license_id, license_url, "原文保存与展示；署名及许可随页面展示", retrieved_at,
             commit, None, description, now()),
        )
        self.stats["works"] += 1

    def insert_chapter(self, draft, *, chapter_id: str, work_id: str, source_path: str,
                       source_url: str, source_sha256: str, volume: str,
                       completeness: str, note: str) -> dict | None:
        old = self.con.execute("SELECT * FROM classical_chapters WHERE chapter_id=?", (chapter_id,)).fetchone()
        if old:
            old = dict(old)
            if old["text_sha256"] == sha256_text(draft.text):
                self.stats["chapters_unchanged"] += 1
                return old
            self.conflicts.append({"chapter_id": chapter_id, "reason": "已存在不同文本，拒绝覆盖"})
            self.stats["chapters_conflict"] += 1
            return old
        chapter = {
            "chapter_id": chapter_id,
            "work_id": work_id,
            "volume": volume,
            "chapter_title": draft.title,
            "sequence": draft.sequence,
            "source_path": source_path,
            "source_url": source_url,
            "source_sha256": source_sha256,
            "text_sha256": sha256_text(draft.text),
            "original_text": draft.text,
            "search_text": normalize_for_search(draft.text),
            "char_count": len(draft.text),
            "paragraph_count": len(draft.paragraphs),
            "page_unit_count": len(draft.pages),
            "completeness": completeness,
            "completeness_note": note or draft.note,
            "verification_status": "verified",
            "license_id": "CC-BY-SA-4.0",
            "retrieved_at": now(),
            "created_at": now(),
        }
        self.con.execute(
            """INSERT INTO classical_chapters
               (chapter_id,work_id,volume,chapter_title,sequence,source_path,source_url,
                source_sha256,text_sha256,original_text,search_text,char_count,
                paragraph_count,page_unit_count,completeness,completeness_note,
                verification_status,license_id,retrieved_at,created_at)
               VALUES(:chapter_id,:work_id,:volume,:chapter_title,:sequence,:source_path,
                :source_url,:source_sha256,:text_sha256,:original_text,:search_text,
                :char_count,:paragraph_count,:page_unit_count,:completeness,
                :completeness_note,:verification_status,:license_id,:retrieved_at,:created_at)""",
            chapter,
        )
        self.stats["chapters"] += 1
        for scheme, paragraphs in (("reading", draft.paragraphs), ("page", draft.pages)):
            for ordinal, (label, start, end) in enumerate(paragraphs, 1):
                text = draft.text[start:end]
                if not text.strip():
                    continue
                self.con.execute(
                    """INSERT OR IGNORE INTO classical_paragraphs
                       (paragraph_id,chapter_id,ordinal,scheme,text,char_start,char_end,page_label)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (f"P-{chapter_id}-{scheme}-{ordinal}", chapter_id, ordinal, scheme,
                     text, start, end, label[:200] if label else None),
                )
                self.stats["paragraphs"] += 1
        self.chapters_by_key[chapter_id] = chapter
        self.chapters_by_work[work_id].append(chapter)
        return chapter

    def ingest_wikisource(self) -> None:
        for key, page in self.ws_config.items():
            manifest = self.ws_pages.get(key)
            if not manifest or manifest.get("status") != "ok":
                self.gaps.append({"type": "wikisource_missing", "key": key,
                                  "work": page["work"], "volume": page["volume"],
                                  "reason": manifest.get("last_error") if manifest else "未抓取"})
                self.stats["ws_missing"] += 1
                continue
            path = PROJECT_ROOT / manifest["local_path"]
            if not path.exists():
                self.gaps.append({"type": "wikisource_file_missing", "key": key, "path": str(path)})
                continue
            raw_bytes = path.read_bytes()
            raw = raw_bytes.decode("utf-8")
            if hashlib.sha256(raw_bytes).hexdigest() != manifest["sha256"]:
                self.conflicts.append({"key": key, "reason": "维基文库原始文件哈希不一致"})
                self.stats["ws_hash_conflict"] += 1
                continue
            draft = parse_wikisource(raw, fallback_title=page["work"],
                                     fallback_volume=page["volume"],
                                     sequence=volume_sequence(page["volume"]))
            work_id = work_id_for_ws(page["work"])
            self.insert_work(
                work_id, page["work"], "", "", "正史",
                "维基文库", "https://zh.wikisource.org/wiki/" + page["work"],
                "维基文库转录文本 / CC BY-SA 4.0", "CC-BY-SA-4.0", WS_LICENSE_URL,
                manifest["retrieved_at"], manifest.get("oldid") or "",
                "维基文库固定页面转录；每章保留页面标题、修订号和抓取时间。",
            )
            url = manifest.get("url", "")
            oldid = manifest.get("oldid")
            if oldid:
                url = "https://zh.wikisource.org/w/index.php?oldid=" + oldid
            chapter_id = "C-WS-" + key
            chapter = self.insert_chapter(
                draft, chapter_id=chapter_id, work_id=work_id,
                source_path=manifest["local_path"], source_url=url,
                source_sha256=manifest["sha256"], volume=page["volume"],
                completeness="full",
                note="维基文库单页全文；页面内小节标题已作为段落标签保存，未混入正文。",
            )
            if chapter:
                self.ensure_source(
                    "S-WS-" + key, page["work"], page["volume"], draft.title,
                    "维基文库", url,
                    (f"维基文库修订 {oldid}；古籍转录文本 / CC BY-SA 4.0"
                     if oldid else "维基文库页面；古籍转录文本 / CC BY-SA 4.0"),
                )
        self.stats["ws_ingested"] = sum(1 for _ in self.ws_pages)

    def ingest_kanripo_for(self, repo: str, file_name: str, volume: str,
                           title_hint: str = "") -> list[dict]:
        manifest = self.kr_manifest.get(repo, {})
        if manifest.get("status") != "ok":
            self.gaps.append({"type": "kanripo_missing", "repo": repo,
                              "reason": manifest.get("last_error", "未下载")})
            return []
        path = PROJECT_ROOT / "data" / "external" / "kanripo" / repo / file_name
        if not path.exists():
            self.gaps.append({"type": "kanripo_file_missing", "repo": repo, "file": file_name})
            return []
        raw_bytes = path.read_bytes()
        raw = raw_bytes.decode("utf-8")
        digest = hashlib.sha256(raw_bytes).hexdigest()
        expected = (manifest.get("file_sha256") or {}).get(file_name)
        if expected and expected != digest:
            self.conflicts.append({"repo": repo, "file": file_name,
                                   "reason": "Kanripo 文件哈希与采集清单不一致"})
            self.stats["kr_hash_conflict"] += 1
            return []
        work_title, author, dynasty, category = {
            "KR2a0001": ("史記", "司馬遷", "漢", "正史"),
            "KR2a0007": ("漢書", "班固", "漢", "正史"),
            "KR2a0009": ("後漢書", "范曄", "南朝宋", "正史"),
            "KR2a0012": ("三國志", "陳壽", "晉", "正史"),
            "KR2a0015": ("晉書", "房玄齡等", "唐", "正史"),
            "KR2a0016": ("宋書", "沈約", "梁", "正史"),
            "KR2a0024": ("南史", "李延壽", "唐", "正史"),
            "KR2a0026": ("舊唐書", "劉昫等", "後晉", "正史"),
            "KR2a0027": ("新唐書", "歐陽修、宋祁", "宋", "正史"),
            "KR2a0029": ("舊五代史", "薛居正等", "宋", "正史"),
            "KR2a0030": ("新五代史", "歐陽修", "宋", "正史"),
            "KR2a0032": ("宋史", "脫脫等", "元", "正史"),
            "KR2a0036": ("元史", "宋濂等", "明", "正史"),
            "KR2a0038": ("明史", "張廷玉等", "清", "正史"),
        }.get(repo, (repo, "", "", "史部"))
        work_id = work_id_for_kr(repo)
        commit = manifest.get("commit", "")
        url = (f"https://github.com/kanripo/{repo}/blob/{commit}/{file_name}"
               if commit else manifest.get("source_url", ""))
        self.insert_work(
            work_id, work_title, author, dynasty, category, "Kanripo", manifest.get("source_url", ""),
            f"Kanripo {repo} / WYG 四庫全書本 / CC BY-SA 4.0", KR_LICENSE,
            KR_LICENSE_URL, manifest.get("retrieved_at", now()), commit,
            "Kanripo 漢籍庫 WYG 四庫全書轉錄；底本頁標記與排版行均保留在來源文件中。",
        )
        drafts = parse_kanripo(raw, fallback_title=title_hint or work_title,
                               fallback_volume=volume)
        file_number = re.search(r"_(\d+)\.txt$", file_name)
        base = int(file_number.group(1)) * 1000 if file_number else 0
        for draft in drafts:
            draft.sequence = base + draft.sequence
        wanted = []
        for draft in drafts:
            if title_hint and len(drafts) > 1:
                if title_hint not in draft.title and draft.title not in title_hint:
                    continue
            chapter_id = f"C-KR-{repo}-{file_name.split('_')[-1].replace('.txt','')}-{draft.sequence}"
            chapter = self.insert_chapter(
                draft, chapter_id=chapter_id, work_id=work_id,
                source_path=str(path.relative_to(PROJECT_ROOT)), source_url=url,
                source_sha256=digest, volume=volume, completeness="full",
                note="Kanripo 单卷/单篇全文；WYG 转录无现代标点，未增补标点。",
            )
            if chapter:
                source_id = "S-" + chapter_id
                self.ensure_source(
                    source_id, work_title, volume, draft.title, "Kanripo", url,
                    f"Kanripo {repo} / WYG / CC BY-SA 4.0；commit {commit[:12]}",
                )
                wanted.append(chapter)
        self.stats["kr_chapters"] += len(wanted)
        return wanted

    def ensure_source(self, source_id: str, book: str, volume: str, chapter: str,
                      platform: str, url: str, edition: str) -> None:
        self.con.execute(
            """INSERT OR IGNORE INTO sources
               (source_id,book_title,volume,chapter,platform,original_url,edition)
               VALUES(?,?,?,?,?,?,?)""",
            (source_id, book, volume, chapter, platform, url, edition),
        )
        self.source_cache[source_id] = {"source_id": source_id, "book_title": book,
                                        "volume": volume, "chapter": chapter,
                                        "platform": platform, "original_url": url,
                                        "edition": edition}

    # ------------------------------------------------------------------ people
    def add_new_people(self) -> None:
        data = load_json(NEW_PEOPLE)
        for item in data["people"]:
            name = item["key"]
            existing = self.find_person(name)
            if existing:
                self.stats["new_people_existing"] += 1
                continue
            person_id = "P-S7-" + short_hash(name, length=12)
            self.con.execute(
                """INSERT OR IGNORE INTO people
                   (person_id,canonical_name,dynasty,birth_year,death_year,native_place,
                    identity_description,verification_status)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (person_id, name, item.get("dynasty"), item.get("birth_year"),
                 item.get("death_year"), item.get("native_place"),
                 item.get("summary"), "verified"),
            )
            person = dict(self.con.execute("SELECT * FROM people WHERE person_id=?", (person_id,)).fetchone())
            self.people_by_name[name] = person
            self.people_by_norm[normalize_for_search(name)] = person
            self.add_catalog_terms(person_id, None, name)
            self.stats["new_people"] += 1
            if item.get("summary"):
                self.upsert_profile(person_id, tier="", biography=item["summary"],
                                    basis="现代整理说明；不作为古籍原文。",
                                    chronology=item.get("chronology_note", ""))

    def upsert_profile(self, person_id: str, *, tier: str, biography: str = "",
                       basis: str = "", chronology: str = "", research: str = "") -> None:
        old = self.con.execute("SELECT * FROM person_profiles WHERE person_id=?", (person_id,)).fetchone()
        if old:
            old = dict(old)
            self.con.execute(
                """UPDATE person_profiles SET tier=?, biography_modern=COALESCE(NULLIF(?,''),biography_modern),
                   biography_basis=COALESCE(NULLIF(?,''),biography_basis),
                   chronology_note=COALESCE(NULLIF(?,''),chronology_note),
                   research_note=COALESCE(NULLIF(?,''),research_note), updated_at=? WHERE person_id=?""",
                (tier or old["tier"], biography, basis, chronology, research, now(), person_id),
            )
        else:
            self.con.execute(
                """INSERT INTO person_profiles
                   (person_id,tier,biography_modern,biography_basis,chronology_note,
                    research_note,completeness_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (person_id, tier or "C", biography, basis, chronology, research,
                 "{}", now(), now()),
            )
        self.stats["profiles"] += 1

    # --------------------------------------------------------------- evidence
    def chapter_section(self, chapter: dict, headings: list[str],
                        name_forms: list[str],
                        start_markers: list[str] | None = None) -> tuple[str, int, str] | None:
        text = chapter["original_text"]
        marker_pos = None
        for marker in start_markers or []:
            position = text.find(marker)
            if position >= 0:
                marker_pos = position
                break
        rows = self.con.execute(
            """SELECT * FROM classical_paragraphs WHERE chapter_id=? AND scheme='reading'
               ORDER BY ordinal""", (chapter["chapter_id"],)
        ).fetchall()
        labels = [row["page_label"] for row in rows if row["page_label"]]
        wanted = [value.strip() for value in headings if value and value.strip()]
        for label in dict.fromkeys(labels):
            for wanted_label in wanted:
                if (wanted_label in label or label in wanted_label
                        or cc_t2s(wanted_label) in cc_t2s(label)
                        or cc_t2s(label) in cc_t2s(wanted_label)):
                    matching = [row for row in rows if row["page_label"] == label]
                    if matching:
                        start = min(row["char_start"] for row in matching)
                        end = max(row["char_end"] for row in matching)
                        # A label may cover several biographies (e.g. 周瑜、魯肅、
                        # 呂蒙).  Trim to the target person's own headword.
                        if marker_pos is not None and marker_pos < end:
                            local = marker_pos
                        else:
                            local_in_section = self.find_person_start(text[start:end], name_forms)
                            local = start + local_in_section if local_in_section is not None else start
                        boundary = self.biography_end(text, local, name_forms)
                        appendix = text.find("考證", local + 80)
                        if 0 < appendix < boundary:
                            boundary = appendix
                        return text[local:min(end, boundary)], local, label
        # A dedicated chapter (e.g. 史記 孔子世家) is itself the biography.
        # Generic juan labels such as 卷三百三十八 must not turn a multi-
        # biography juan into one person's biography.  This runs after label
        # matching so multi-person pages such as 周瑜、魯肅、呂蒙 still split.
        def generic(value: str) -> bool:
            return bool(re.fullmatch(r"卷[\d一二三四五六七八九十百千上中下]+", value)) \
                or value == (chapter.get("volume") or "")
        if any(value and not generic(value) and value in chapter["chapter_title"] for value in wanted):
            start = marker_pos if marker_pos is not None else 0
            return text[start:], start, chapter["chapter_title"]
        # A page with one body section or a dedicated chapter is itself the section.
        unique = list(dict.fromkeys(labels))
        if len(unique) <= 1:
            return text, 0, unique[0] if unique else chapter["chapter_title"]
        # Fall back to the paragraph containing the best name occurrence.  Siku
        # edition headers list the biography headword before the text proper
        # (e.g. "列傳第九十七蘇軾(子過邁迨)"), so prefer an occurrence followed
        # by 字 or a clause boundary and skip parenthesised headwords.
        position = marker_pos if marker_pos is not None else self.find_person_start(text, name_forms)
        if position is None:
            return None
        containing = next((row for row in rows
                           if row["char_start"] <= position < row["char_end"]), None)
        if not containing:
            return None
        end = self.biography_end(text, position, name_forms)
        appendix = text.find("考證", position + 80)
        if 0 < appendix < end:
            end = appendix
        return text[position:end], position, "据人物姓名定位的连续段落"

    def biography_end(self, text: str, start: int, name_forms: list[str]) -> int:
        """Find the next biography headword after ``start``.

        Kanripo's WYG transcription often introduces a biography with
        ``姓名字某``.  The next such headword is a safer boundary than a fixed
        page count, including when the next biography starts mid-page.
        """
        for match in re.finditer(r"([一-鿿]{2,4})[，,]?字[一-鿿]", text):
            group = match.group(1)
            for offset in range(0, max(1, len(group) - 1)):
                candidate = group[offset:]
                position = match.start() + offset
                if position <= start:
                    continue
                if any(form in candidate or candidate in form for form in name_forms):
                    continue
                if self.looks_like_person(candidate):
                    return position
        return len(text)

    @staticmethod
    def looks_like_person(name: str) -> bool:
        surnames = set(
            "趙钱孫李周吳鄭王馮陳褚衛蔣沈韓楊朱秦尤許何呂吕施張孔曹嚴華金魏陶姜"
            "戚謝鄒喻柏水竇章雲蘇潘葛奚范彭郎魯韋昌馬苗鳳花方俞任袁柳酆鮑史唐"
            "費廉岑薛雷賀倪湯滕殷羅畢郝鄔安常樂于時傅皮卞齊康伍余元卜顧孟平黃"
            "和穆蕭尹姚邵湛汪祁毛禹狄米貝明臧計伏成戴談宋茅龐熊紀舒屈項祝董梁"
            "杜阮藍閔席季麻強賈路婁危江童顏郭梅盛林刁鍾徐邱駱高夏蔡田樊胡凌霍"
            "虞萬支柯昝管盧莫房裘繆干解應宗丁宣鄧郁單杭洪包諸左石崔吉鈕龔程嵇"
            "邢滑裴陸榮翁荀羊於惠甄麴家封芮羿儲靳汲邴糜松井段富巫烏焦巴弓牧隗"
            "山谷車侯宓蓬全郗班仰秋仲伊宮甯仇欒暴甘鈄厲戎祖武符劉景詹束龍葉幸"
            "司韶郜黎薊薄印宿白懷蒲邰從鄂索咸籍賴卓藺屠蒙池喬陰鬱胥能蒼雙聞莘"
            "黨翟譚貢勞逄姬申扶堵冉宰酈雍卻璩桑桂濮牛壽通邊扈燕冀郟浦尚農溫別"
            "莊晏柴瞿閻充慕連茹習宦艾魚容向古易慎戈廖庾終暨居衡步都耿滿弘匡國"
            "文寇廣祿闕東歐殳沃利蔚越夔隆師鞏厙聶晁勾敖融冷訾辛闞那簡饒空曾毋"
            "沙乜養鞠須豐巢關蒯相查後荊紅游竺權逯蓋益桓公"
        )
        compounds = {"司馬", "諸葛", "歐陽", "上官", "皇甫", "尉遲", "長孫", "宇文",
                     "獨孤", "慕容", "令狐", "公孫", "夏侯", "東方", "南宮", "西門",
                     "軒轅", "鐘離", "鮮于", "閭丘", "司寇", "巫馬", "公西", "壤駟",
                     "漆雕", "樂正", "拓跋", "百里", "東郭", "呼延", "梁丘", "左丘",
                     "第五", "聞人", "澹臺", "公冶", "宗政", "濮陽", "淳于", "單于",
                     "太叔", "仲孫", "亓官", "子車", "端木", "萬俟", "赫連"}
        false_end = set("文名字氏之其所有無不曰云者也矣焉哉乎而以於為與及則乃且故因遂皆各每嘗常復又更亦已未既方正將欲能可得見聞知謂稱")
        return bool(name) and (name[0] in surnames or name[:2] in compounds) and name[-1] not in false_end

    def find_person_start(self, text: str, name_forms: list[str]) -> int | None:
        best: tuple[int, int] | None = None
        for form in name_forms:
            start = 0
            while True:
                position = text.find(form, start)
                if position < 0:
                    break
                following = text[position + len(form):position + len(form) + 8]
                preceding = text[max(0, position - 6):position]
                score = 0
                if "字" in following[:5]:
                    score += 6
                if following[:1] in "，。；：、" or following[:1].isspace():
                    score += 3
                if not preceding or preceding[-1:] in "。！？；，\n":
                    score += 2
                if "諱" in preceding:
                    score += 3
                # Skip the catalogue headword in parentheses.
                open_paren = text.rfind("(", 0, position)
                close_paren = text.rfind(")", 0, position)
                if open_paren > close_paren:
                    score -= 8
                if any(word in preceding for word in ("列傳", "列传", "巻", "卷", "目錄", "目录")):
                    score -= 6
                candidate = (score, -position)
                if best is None or candidate > best:
                    best = candidate
                start = position + len(form)
        return -best[1] if best else None

    def extract_quote(self, text: str, start: int, end: int, *, max_len: int = 520) -> str:
        left = start
        while left > 0 and text[left - 1] not in "。！？\n":
            left -= 1
        right = end
        while right < len(text) and text[right] not in "。！？\n":
            right += 1
        if right < len(text) and text[right] in "。！？":
            right += 1
        quote = text[left:right].strip()
        if len(quote) <= max_len:
            return quote
        pad = max_len // 2
        win_start = max(left, start - pad)
        win_end = min(right, end + pad)
        for boundary in ("。", "！", "？", "\n"):
            hit = text.rfind(boundary, left, start)
            if hit >= left:
                win_start = max(win_start, hit + 1)
                break
        return text[win_start:win_end].strip()

    def name_forms_for(self, key: str, person: dict, extra: list[str] | None = None) -> list[str]:
        forms = [key, person["canonical_name"], cc_s2t(key), cc_t2s(key)]
        if extra:
            forms.extend(extra)
        return list(dict.fromkeys(form for form in forms if form))

    def link_a_people(self) -> None:
        data = load_json(A_SEED)
        for key, spec in data["people"].items():
            person = self.find_person(key, spec.get("person_id"))
            if not person:
                self.gaps.append({"type": "a_person_missing", "name": key})
                self.stats["a_person_missing"] += 1
                continue
            name_alias = self.add_alias(person, person["canonical_name"], "name")
            if name_alias:
                self.add_catalog_terms(person["person_id"], name_alias["alias_id"], person["canonical_name"])
            name_forms = self.name_forms_for(key, person, spec.get("name_forms"))
            alias_names = {item["name"] for item in spec.get("aliases", [])}
            identity_forms = [form for form in name_forms if form not in alias_names]
            sources: list[tuple[dict, str, str]] = []
            # Wikisource first (punctuated reading text), then Kanripo fallback.
            for source in spec.get("sources", []):
                if source.get("ws"):
                    chapter = self.chapters_by_key.get("C-WS-" + source["ws"])
                    if chapter:
                        sources.append((chapter, source.get("heading", key), "S-WS-" + source["ws"]))
                if source.get("kr"):
                    chapters = self.ingest_kanripo_for(
                        source["kr"], source["file"], source.get("volume", ""),
                        source.get("title", ""),
                    )
                    for chapter in chapters:
                        sources.append((chapter, source.get("title", key), "S-" + chapter["chapter_id"]))
            if not sources:
                self.gaps.append({"type": "a_no_chapter", "name": key})
                continue
            used_locations: set[tuple[str, int, int]] = set()
            alias_hits: dict[str, int] = Counter()
            biography_added = 0
            for chapter, heading, source_id in sources:
                section = self.chapter_section(chapter, [heading] + spec.get("headings", []), name_forms,
                                                spec.get("start_markers", []))
                if not section:
                    self.gaps.append({"type": "section_not_found", "name": key,
                                      "chapter_id": chapter["chapter_id"]})
                    continue
                section_text, offset, label = section
                identity_from_label = False
                identity_from_marker = any(
                    marker and marker in section_text[:80]
                    for marker in spec.get("start_markers", [])
                )
                if identity_from_marker:
                    pass
                elif not any(form in section_text for form in name_forms):
                    # Wikisource section headings sometimes carry the surname
                    # while the body begins with "，字某" (e.g. 杜牧 / 歐陽修).
                    # The heading is a catalogue headword, not ancient prose,
                    # so it may confirm identity but must not enter a quote.
                    if any(form in (label or "") for form in name_forms):
                        identity_from_label = True
                    else:
                        self.conflicts.append({"name": key, "chapter_id": chapter["chapter_id"],
                                               "reason": "目标人物姓名不在候选段落内，拒绝关联"})
                        self.stats["identity_rejected"] += 1
                        continue
                # Name evidence: first sentence containing a known name form.
                name_quote = ""
                opening = section_text[:40]
                if identity_from_label or identity_from_marker:
                    name_quote = ""
                elif any(form in opening for form in identity_forms) or opening.startswith("諱"):
                    name_quote = self.extract_quote(section_text, 0, min(30, len(section_text)),
                                                    max_len=260)
                if not name_quote:
                    for form in identity_forms:
                        position = section_text.find(form)
                        if position >= 0:
                            name_quote = self.extract_quote(section_text, position,
                                                            position + len(form), max_len=260)
                            if name_quote:
                                break
                if name_quote and name_alias:
                    self.add_evidence(person, name_alias, chapter, source_id, name_quote,
                                      "direct_identity", f"本传直接以“{name_forms[0]}”称之",
                                      relation_type="本传直书", evidence_type="史传")
                for alias_spec in spec.get("aliases", []):
                    alias_name = alias_spec["name"]
                    alias_type = alias_spec["type"]
                    found = False
                    for pattern in alias_spec.get("patterns", []):
                        position = section_text.find(pattern)
                        if position < 0:
                            continue
                        quote = self.extract_quote(section_text, position, position + len(pattern), max_len=320)
                        if not quote or pattern not in quote:
                            continue
                        alias = self.add_alias(person, alias_name, alias_type)
                        if not alias:
                            continue
                        self.add_catalog_terms(person["person_id"], alias["alias_id"], alias_name)
                        result = self.add_evidence(
                            person, alias, chapter, source_id, quote, "direct_alias",
                            alias_spec.get("claim", f"本传直接记载{alias_name}"),
                            relation_type="本传直书", evidence_type="史传",
                        )
                        if result:
                            found = True
                            alias_hits[alias_name] += 1
                            used_locations.add((chapter["chapter_id"], result["start"], result["end"]))
                        break
                    if not found:
                        self.gaps.append({"type": "alias_pattern_not_found", "name": key,
                                          "alias": alias_name, "alias_type": alias_type,
                                          "chapter_id": chapter["chapter_id"]})
                # Biography segments: real paragraphs, capped and deduplicated.
                if biography_added < 8 and name_alias:
                    rows = self.con.execute(
                        """SELECT * FROM classical_paragraphs WHERE chapter_id=? AND scheme='reading'
                           AND char_start>=? AND char_end<=? ORDER BY ordinal""",
                        (chapter["chapter_id"], offset, offset + len(section_text)),
                    ).fetchall()
                    candidates = [row for row in rows if len(row["text"].strip()) >= 30]
                    if candidates:
                        step = max(1, len(candidates) // max(1, 8 - biography_added))
                        picked = candidates[::step]
                        for row in picked:
                            if biography_added >= 8:
                                break
                            quote = self.extract_quote(row["text"], 0, min(len(row["text"]), 80))
                            if not quote or len(quote) < 20:
                                continue
                            local = locate_quote(chapter["original_text"], quote)
                            if local and (chapter["chapter_id"], local[0], local[1]) in used_locations:
                                continue
                            result = self.add_evidence(
                                person, name_alias, chapter, source_id, quote, "biography",
                                f"本传所载生平片段（{label}）" if label else "本传所载生平片段",
                                relation_type="本传叙述", evidence_type="史传",
                            )
                            if result:
                                biography_added += 1
                                used_locations.add((chapter["chapter_id"], result["start"], result["end"]))
            # Profile summary.
            if spec.get("summary"):
                self.upsert_profile(person["person_id"], tier="A",
                                    biography=spec["summary"],
                                    basis="现代整理说明；每条事实另见本页古籍证据。")
            else:
                self.upsert_profile(person["person_id"], tier="A")
            self.stats["a_people"] += 1
            if not alias_hits:
                self.gaps.append({"type": "a_no_alias_evidence", "name": key})

    def add_alias(self, person: dict, name: str, alias_type: str) -> dict | None:
        row = self.con.execute(
            "SELECT * FROM aliases WHERE person_id=? AND alias_name=? AND alias_type=?",
            (person["person_id"], name, alias_type),
        ).fetchone()
        if row:
            return dict(row)
        alias_id = "A7-" + short_hash(person["person_id"], name, alias_type)
        self.con.execute(
            """INSERT OR IGNORE INTO aliases
               (alias_id,person_id,alias_name,normalized_name,alias_type,verification_status)
               VALUES(?,?,?,?,?,?)""",
            (alias_id, person["person_id"], name, normalize_name(name), alias_type, "verified"),
        )
        self.stats["aliases"] += 1
        return dict(self.con.execute("SELECT * FROM aliases WHERE alias_id=?", (alias_id,)).fetchone())

    def add_catalog_terms(self, person_id: str, alias_id: str | None, name: str) -> None:
        terms = {name, cc_t2s(name), cc_s2t(name)}
        for term in terms:
            if not term:
                continue
            self.con.execute(
                "INSERT OR IGNORE INTO catalog_terms(term,person_id,alias_id,original,method) VALUES(?,?,?,?,?)",
                (normalize_for_search(term), person_id, alias_id, name,
                 "NFC/OpenCC t2s; Stage 7 retrieval only"),
            )

    def add_evidence(self, person: dict, alias: dict, chapter: dict, source_id: str,
                     quote: str, category: str, claim: str, *, explanation: str = "",
                     relation_type: str = "本传直书", evidence_type: str = "史传",
                     method: str | None = None) -> dict | None:
        located = locate_quote(chapter["original_text"], quote)
        if not located:
            self.gaps.append({"type": "quote_not_located", "person": person["canonical_name"],
                              "alias": alias["alias_name"], "chapter_id": chapter["chapter_id"],
                              "quote": quote[:120]})
            self.stats["quote_not_located"] += 1
            return None
        start, end = located
        if not verify_quote(chapter["original_text"], quote, start, end):
            self.gaps.append({"type": "quote_verification_failed", "person": person["canonical_name"],
                              "chapter_id": chapter["chapter_id"], "quote": quote[:120]})
            self.stats["quote_verification_failed"] += 1
            return None
        quote_text = normalised_quote(chapter["original_text"], start, end)
        quote_hash = sha256_text(quote_text)
        link_id = "L-" + short_hash(person["person_id"], chapter["chapter_id"], start, end,
                                    alias["alias_id"])
        old = self.con.execute("SELECT * FROM classical_evidence_links WHERE link_id=?", (link_id,)).fetchone()
        if old:
            old = dict(old)
            return {"link_id": old["link_id"], "evidence_id": old["evidence_id"],
                    "quote": old["quote"], "start": old["quote_start"],
                    "end": old["quote_end"], "chapter_id": old["chapter_id"],
                    "anchor": f"q-{old['quote_start']}-{old['quote_end']}"}
        # Reuse an existing evidence row only when it has the same alias, source and quote.
        evidence_id = "E7-" + short_hash(alias["alias_id"], chapter["chapter_id"], start, end)
        old_e = self.con.execute("SELECT * FROM evidences WHERE evidence_id=?", (evidence_id,)).fetchone()
        line_start = chapter["original_text"].count("\n", 0, start) + 1
        line_end = chapter["original_text"].count("\n", 0, end) + 1
        paragraph = self.con.execute(
            """SELECT * FROM classical_paragraphs WHERE chapter_id=? AND scheme='reading'
               AND char_start<=? AND char_end>=? ORDER BY ordinal LIMIT 1""",
            (chapter["chapter_id"], start, end),
        ).fetchone()
        context = ""
        if paragraph:
            context_row = self.con.execute(
                """SELECT text FROM classical_paragraphs WHERE chapter_id=? AND scheme='reading'
                   AND ordinal BETWEEN ? AND ? ORDER BY ordinal""",
                (chapter["chapter_id"], max(1, paragraph["ordinal"] - 1), paragraph["ordinal"] + 1),
            ).fetchall()
            context = "\n\n".join(row["text"] for row in context_row)[:1600]
        if not context:
            context = sentence_context(chapter["original_text"], start, end, before=2, after=2) or quote_text
        notes = {
            "evidence_kind": "classical_text",
            "verification_method": method or (
                "wikisource_fixed_text_v1" if chapter["work_id"].startswith("W-WS-")
                else "kanripo_fixed_headline_v1"),
            "source_version": self.source_cache.get(source_id, {}).get("edition", ""),
            "locator": {"start": start, "end": end, "line_start": line_start,
                        "line_end": line_end, "chapter_id": chapter["chapter_id"],
                        "paragraph": paragraph["ordinal"] if paragraph else None,
                        "quote_sha256": quote_hash},
            "relation_type": relation_type,
            "evidence_type": evidence_type,
            "context": context,
            "remarks": "引文与保存篇章逐字核对；位置、段落与篇章哈希可复核。",
            "stage7_run": RUN_ID,
        }
        if not old_e:
            self.con.execute(
                """INSERT INTO evidences
                   (evidence_id,alias_id,source_id,original_text,usage_relation,
                    evidence_nature,verification_status,notes)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (evidence_id, alias["alias_id"], source_id, quote_text, "supports",
                 "direct", "verified", json.dumps(notes, ensure_ascii=False)),
            )
            self.stats["evidences"] += 1
        self.con.execute(
            """INSERT OR IGNORE INTO classical_evidence_links
               (link_id,evidence_id,person_id,alias_id,chapter_id,paragraph_id,quote,
                quote_start,quote_end,evidence_category,claim,explanation,identity_match,
                verification_status,quote_sha256,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (link_id, evidence_id, person["person_id"], alias["alias_id"],
             chapter["chapter_id"], paragraph["paragraph_id"] if paragraph else None,
             quote_text, start, end, category, claim, explanation,
             "confirmed", "verified", quote_hash, now()),
        )
        self.con.execute(
            "UPDATE aliases SET verification_status='verified' WHERE alias_id=? AND verification_status='pending'",
            (alias["alias_id"],),
        )
        self.stats["links"] += 1
        return {"link_id": link_id, "evidence_id": evidence_id, "quote": quote_text,
                "start": start, "end": end, "chapter_id": chapter["chapter_id"],
                "anchor": f"q-{start}-{end}"}


    # --------------------------------------------------------------- profiles
    def build_profiles(self) -> None:
        a_seed = load_json(A_SEED)["people"]
        a_names = set(a_seed)
        a_ids = set()
        for name in a_names:
            person = self.find_person(name, a_seed[name].get("person_id"))
            if person:
                a_ids.add(person["person_id"])
                self.upsert_profile(person["person_id"], tier="A")
        priority = {
            "陶弘景", "謝安", "謝靈運", "嵇康", "阮籍", "陸機", "王粲", "蔡邕", "蔡琰",
            "王昭君", "李靖", "魏徵", "房玄齡", "杜如晦", "狄仁傑", "郭子儀", "顏真卿",
            "李德裕", "元稹", "溫庭筠", "李煜", "柳永", "晏殊", "周邦彥", "姜夔", "楊萬里",
            "范成大", "韓琦", "富弼", "文彥博", "司馬相如", "班昭", "衛青", "霍去病",
            "張騫", "蘇武", "項羽", "劉邦", "韓信", "張良", "蕭何", "孫武", "孫臏", "吳起",
            "白起", "王翦", "蒙恬", "李斯", "韓非", "呂不韋", "管仲", "晏嬰", "伍子胥",
            "蘇秦", "張儀", "孟嘗君", "信陵君", "廉頗", "藺相如", "東方朔", "揚雄", "劉向",
            "馬援", "班超", "蔡倫", "張仲景", "董卓", "呂布", "袁紹", "孫權", "劉備",
            "司馬懿", "王導", "謝玄", "桓溫", "祖逖", "王猛", "苻堅", "楊堅", "楊廣",
            "李淵", "李隆基", "楊貴妃", "安祿山", "李光弼", "韓世忠", "張俊", "秦檜",
            "賈似道", "史彌遠", "韓侂胄", "趙匡胤", "趙構", "武則天", "上官婉兒",
        }
        rows = self.con.execute("SELECT * FROM people").fetchall()
        scored = []
        for row in rows:
            person = dict(row)
            if person["person_id"] in a_ids:
                continue
            alias_count = self.con.execute(
                "SELECT count(*) FROM aliases WHERE person_id=?", (person["person_id"],)
            ).fetchone()[0]
            evidence_count = self.con.execute(
                """SELECT count(*) FROM evidences e JOIN aliases a USING(alias_id)
                   WHERE a.person_id=?""", (person["person_id"],)
            ).fetchone()[0]
            classical_count = self.con.execute(
                "SELECT count(*) FROM classical_evidence_links WHERE person_id=?",
                (person["person_id"],),
            ).fetchone()[0]
            bonus = 12 if person["canonical_name"] in priority else 0
            score = classical_count * 8 + alias_count * 2 + evidence_count + bonus
            scored.append((score, person))
        scored.sort(key=lambda item: (-item[0], item[1]["canonical_name"], item[1]["person_id"]))
        b_ids = {person["person_id"] for _, person in scored[:140]}
        for person in rows:
            person = dict(person)
            if person["person_id"] in a_ids:
                continue
            tier = "B" if person["person_id"] in b_ids else "C"
            self.upsert_profile(person["person_id"], tier=tier)
        self.stats["a_profiles"] = len(a_ids)
        self.stats["b_profiles"] = len(b_ids)
        self.stats["c_profiles"] = len(rows) - len(a_ids) - len(b_ids)

    def update_completeness(self) -> None:
        for row in self.con.execute("SELECT person_id FROM person_profiles").fetchall():
            person_id = row["person_id"]
            alias_count = self.con.execute(
                "SELECT count(*) FROM aliases WHERE person_id=?", (person_id,)
            ).fetchone()[0]
            link_count = self.con.execute(
                "SELECT count(*) FROM classical_evidence_links WHERE person_id=?", (person_id,)
            ).fetchone()[0]
            chapter_count = self.con.execute(
                "SELECT count(DISTINCT chapter_id) FROM classical_evidence_links WHERE person_id=?",
                (person_id,),
            ).fetchone()[0]
            work_count = self.con.execute(
                """SELECT count(DISTINCT c.work_id) FROM classical_evidence_links l
                   JOIN classical_chapters c USING(chapter_id) WHERE l.person_id=?""",
                (person_id,),
            ).fetchone()[0]
            timeline = self.con.execute(
                "SELECT count(*) FROM person_timeline_events WHERE person_id=?", (person_id,)
            ).fetchone()[0]
            works = self.con.execute(
                "SELECT count(*) FROM person_literary_works WHERE person_id=?", (person_id,)
            ).fetchone()[0]
            relations = self.con.execute(
                "SELECT count(*) FROM person_relations WHERE person_id=?", (person_id,)
            ).fetchone()[0]
            disputes = self.con.execute(
                "SELECT count(*) FROM person_disputes WHERE person_id=?", (person_id,)
            ).fetchone()[0]
            profile = self.con.execute(
                "SELECT biography_modern FROM person_profiles WHERE person_id=?", (person_id,)
            ).fetchone()
            completeness = {
                "basic": True,
                "biography": bool(profile and profile["biography_modern"]),
                "aliases": alias_count,
                "classical_evidence": link_count,
                "classical_chapters": chapter_count,
                "classical_works": work_count,
                "timeline": timeline,
                "literary_works": works,
                "relations": relations,
                "disputes": disputes,
            }
            self.con.execute(
                "UPDATE person_profiles SET completeness_json=?, updated_at=? WHERE person_id=?",
                (json.dumps(completeness, ensure_ascii=False), now(), person_id),
            )

    def build_literary_works(self) -> None:
        path = PROJECT_ROOT / "data" / "classical" / "literary_works.json"
        if not path.exists():
            return
        data = load_json(path)
        a_seed = load_json(A_SEED)["people"]
        for name, items in data.get("people", {}).items():
            person = self.find_person(name, a_seed.get(name, {}).get("person_id"))
            if not person:
                self.gaps.append({"type": "literary_person_missing", "name": name})
                continue
            for item in items:
                entry_id = "LW-" + short_hash(person["person_id"], item["title"])
                self.con.execute(
                    """INSERT OR IGNORE INTO person_literary_works
                       (entry_id,person_id,title,genre,chapter_id,attribution_status,note,
                        evidence_link_id,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (entry_id, person["person_id"], item["title"], item.get("genre"),
                     None, item.get("attribution", "attributed"),
                     item.get("note", "本阶段仅列目；尚未取得可合法展示的完整文本。"),
                     None, now()),
                )
                self.stats["literary_entries"] += 1

    def build_narratives(self) -> None:
        path = PROJECT_ROOT / "data" / "classical" / "narratives.json"
        if not path.exists():
            return
        data = load_json(path)
        a_seed = load_json(A_SEED)["people"]
        for person_name, events in data.get("timeline", {}).items():
            person = self.find_person(person_name, a_seed.get(person_name, {}).get("person_id"))
            if not person:
                self.gaps.append({"type": "narrative_person_missing", "name": person_name})
                continue
            chapters = self.person_chapters(person["person_id"])
            for event in events:
                quote = event.get("quote", "")
                chapter = self.find_quote_chapter(chapters, quote)
                link_id = None
                if chapter:
                    alias = self.name_alias(person)
                    if alias:
                        result = self.add_evidence(
                            person, alias, chapter, self.chapter_source_id(chapter),
                            quote, "biography", event["event_text"],
                            relation_type="本传叙述", evidence_type="史传")
                        if result:
                            link_id = result["link_id"]
                event_id = "T-" + short_hash(person["person_id"], event["year_label"],
                                              event["event_text"])
                self.con.execute(
                    """INSERT OR IGNORE INTO person_timeline_events
                       (event_id,person_id,year_label,year_value,certainty,event_text,
                        place,evidence_link_id,source_note,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (event_id, person["person_id"], event["year_label"],
                     event.get("year_value"), event.get("certainty", "approximate"),
                     event["event_text"], event.get("place"), link_id,
                     event.get("source_note", ""), now()),
                )
                self.stats["timeline_events"] += 1
        for person_name, relations in data.get("relations", {}).items():
            person = self.find_person(person_name, a_seed.get(person_name, {}).get("person_id"))
            if not person:
                continue
            chapters = self.person_chapters(person["person_id"])
            for relation in relations:
                quote = relation.get("quote", "")
                chapter = self.find_quote_chapter(chapters, quote)
                link_id = None
                if chapter:
                    alias = self.name_alias(person)
                    if alias:
                        result = self.add_evidence(
                            person, alias, chapter, self.chapter_source_id(chapter),
                            quote, "biography",
                            f"本传载其与{relation['related_name']}的关系",
                            relation_type="本传叙述", evidence_type="史传")
                        if result:
                            link_id = result["link_id"]
                relation_id = "R-" + short_hash(person["person_id"],
                                                relation["related_name"], relation["relation_type"])
                related = self.find_person(relation["related_name"])
                self.con.execute(
                    """INSERT OR IGNORE INTO person_relations
                       (relation_id,person_id,related_person_id,related_name,relation_type,
                        evidence_link_id,note,verification_status,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (relation_id, person["person_id"],
                     related["person_id"] if related else None, relation["related_name"],
                     relation["relation_type"], link_id, relation.get("note", ""),
                     "verified" if link_id else "pending", now()),
                )
                self.stats["relations"] += 1
        for person_name, disputes in data.get("disputes", {}).items():
            person = self.find_person(person_name, a_seed.get(person_name, {}).get("person_id"))
            if not person:
                continue
            chapters = self.person_chapters(person["person_id"])
            for dispute in disputes:
                quote = dispute.get("quote", "")
                chapter = self.find_quote_chapter(chapters, quote) if quote else None
                link_id = None
                if chapter:
                    alias = self.name_alias(person)
                    if alias:
                        result = self.add_evidence(
                            person, alias, chapter, self.chapter_source_id(chapter),
                            quote, "direct_identity",
                            f"异说所据：{dispute['topic']}",
                            relation_type="异文记载", evidence_type="史传")
                        if result:
                            link_id = result["link_id"]
                dispute_id = "D-" + short_hash(person["person_id"], dispute["topic"])
                self.con.execute(
                    """INSERT OR IGNORE INTO person_disputes
                       (dispute_id,person_id,topic,account_a,account_b,note,
                        evidence_link_id,verification_status,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (dispute_id, person["person_id"], dispute["topic"],
                     dispute["account_a"], dispute.get("account_b"), dispute.get("note"),
                     link_id, "verified" if link_id else "pending", now()),
                )
                self.stats["disputes"] += 1

    def person_chapters(self, person_id: str) -> list[dict]:
        rows = self.con.execute(
            """SELECT DISTINCT c.* FROM classical_chapters c
               JOIN classical_evidence_links l USING(chapter_id)
               WHERE l.person_id=? ORDER BY c.sequence""", (person_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def find_quote_chapter(self, chapters: list[dict], quote: str) -> dict | None:
        if not quote:
            return None
        for chapter in chapters:
            if locate_quote(chapter["original_text"], quote):
                return chapter
        return None

    def name_alias(self, person: dict) -> dict | None:
        row = self.con.execute(
            "SELECT * FROM aliases WHERE person_id=? AND alias_type='name' ORDER BY alias_id LIMIT 1",
            (person["person_id"],),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def chapter_source_id(chapter: dict) -> str:
        return "S-WS-" + chapter["chapter_id"][5:] if chapter["chapter_id"].startswith("C-WS-") \
            else "S-" + chapter["chapter_id"]

    def update_work_hashes(self) -> None:
        for work_id in [row["work_id"] for row in self.con.execute("SELECT work_id FROM classical_works")]:
            rows = self.con.execute(
                "SELECT text_sha256 FROM classical_chapters WHERE work_id=? ORDER BY sequence",
                (work_id,),
            ).fetchall()
            combined = sha256_text("".join(row["text_sha256"] for row in rows))
            self.con.execute("UPDATE classical_works SET text_sha256=? WHERE work_id=?",
                             (combined, work_id))

    # ----------------------------------------------------------------- verify
    def verify(self) -> dict:
        issues = []
        chapters = self.con.execute("SELECT * FROM classical_chapters").fetchall()
        for chapter in chapters:
            chapter = dict(chapter)
            if chapter["char_count"] != len(chapter["original_text"]):
                issues.append({"type": "char_count_mismatch", "chapter_id": chapter["chapter_id"],
                               "stored": chapter["char_count"], "actual": len(chapter["original_text"])})
            if sha256_text(chapter["original_text"]) != chapter["text_sha256"]:
                issues.append({"type": "chapter_hash_mismatch", "chapter_id": chapter["chapter_id"]})
            if "\x00" in chapter["original_text"]:
                issues.append({"type": "nul_character", "chapter_id": chapter["chapter_id"]})
        links = self.con.execute(
            """SELECT l.*, c.original_text, c.char_count FROM classical_evidence_links l
               JOIN classical_chapters c USING(chapter_id)"""
        ).fetchall()
        seen = set()
        for link in links:
            link = dict(link)
            if link["quote_end"] > link["char_count"]:
                issues.append({"type": "offset_out_of_range", "link_id": link["link_id"]})
                continue
            actual = normalised_quote(link["original_text"], link["quote_start"], link["quote_end"])
            if actual != link["quote"]:
                issues.append({"type": "quote_offset_mismatch", "link_id": link["link_id"]})
            if sha256_text(actual) != link["quote_sha256"]:
                issues.append({"type": "quote_hash_mismatch", "link_id": link["link_id"]})
            key = (link["chapter_id"], link["quote_start"], link["quote_end"])
            if key in seen:
                self.stats["duplicate_quote_locations"] += 1
            seen.add(key)
        missing_source = self.con.execute(
            """SELECT count(*) FROM classical_evidence_links l
               LEFT JOIN evidences e USING(evidence_id) WHERE e.evidence_id IS NULL"""
        ).fetchone()[0]
        if missing_source:
            issues.append({"type": "evidence_missing", "count": missing_source})
        self.stats["verified_chapters"] = len(chapters)
        self.stats["verified_links"] = len(links)
        self.stats["integrity_issues"] = len(issues)
        return {"chapters": len(chapters), "links": len(links), "issues": issues}


def _write_report(builder: Builder, verification: dict, status: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    db_stats = {
        "people": builder.con.execute("SELECT count(*) FROM people").fetchone()[0],
        "profiles": builder.con.execute("SELECT count(*) FROM person_profiles").fetchone()[0],
        "tiers": dict(builder.con.execute(
            "SELECT tier,count(*) FROM person_profiles GROUP BY tier").fetchall()),
        "works": builder.con.execute("SELECT count(*) FROM classical_works").fetchone()[0],
        "chapters": builder.con.execute("SELECT count(*) FROM classical_chapters").fetchone()[0],
        "paragraphs": builder.con.execute("SELECT count(*) FROM classical_paragraphs").fetchone()[0],
        "links": builder.con.execute("SELECT count(*) FROM classical_evidence_links").fetchone()[0],
        "distinct_quote_locations": builder.con.execute(
            "SELECT count(*) FROM (SELECT DISTINCT chapter_id,quote_start,quote_end FROM classical_evidence_links)"
        ).fetchone()[0],
        "people_with_classical_links": builder.con.execute(
            "SELECT count(DISTINCT person_id) FROM classical_evidence_links").fetchone()[0],
        "aliases": builder.con.execute("SELECT count(*) FROM aliases").fetchone()[0],
        "evidences": builder.con.execute("SELECT count(*) FROM evidences").fetchone()[0],
        "sources": builder.con.execute("SELECT count(*) FROM sources").fetchone()[0],
    }
    per_person = [dict(row) for row in builder.con.execute(
        """SELECT p.person_id,p.canonical_name,pr.tier,
                  count(DISTINCT l.link_id) AS classical_links,
                  count(DISTINCT l.chapter_id) AS chapters,
                  count(DISTINCT c.work_id) AS works
           FROM people p LEFT JOIN person_profiles pr USING(person_id)
           LEFT JOIN classical_evidence_links l USING(person_id)
           LEFT JOIN classical_chapters c ON c.chapter_id=l.chapter_id
           GROUP BY p.person_id ORDER BY classical_links DESC,p.canonical_name"""
    ).fetchall()]
    report = {
        "run_id": RUN_ID,
        "generated_at": now(),
        "status": status,
        "db": db_stats,
        "builder_stats": dict(builder.stats),
        "verification": verification,
        "gaps": builder.gaps,
        "conflicts": builder.conflicts,
        "per_person": per_person,
    }
    (REPORT_DIR / "stage7_build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gaps = {
        "run_id": RUN_ID,
        "generated_at": now(),
        "missing_aliases": [gap for gap in builder.gaps
                            if gap.get("type") in {"alias_pattern_not_found", "a_no_alias_evidence"}],
        "missing_sources": [gap for gap in builder.gaps
                            if gap.get("type") in {"wikisource_missing", "kanripo_missing",
                                                   "a_no_chapter", "section_not_found"}],
        "conflicts": builder.conflicts,
    }
    (REPORT_DIR / "stage7_gap_report.json").write_text(
        json.dumps(gaps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="建设 Stage 7 古籍原文库与人物证据")
    parser.add_argument("--dry-run", action="store_true", help="只解析并报告，不写入数据库")
    parser.add_argument("--db", default=str(DB_PATH), help="数据库路径")
    parser.add_argument("--ws-manifest", default=str(WS_MANIFEST), help="维基文库检查点文件")
    args = parser.parse_args(argv)
    db_path = Path(args.db)
    init_db(db_path)
    with connect_db(db_path) as con:
        ensure_run(con, args.dry_run)
        con.execute("BEGIN IMMEDIATE")
        builder = Builder(con, dry_run=args.dry_run)
        builder.ws_manifest_path = Path(args.ws_manifest)
        status = "completed"
        try:
            builder.load()
            print("1/8 导入维基文库篇章 ...", flush=True)
            builder.ingest_wikisource()
            print("2/8 新增人物 ...", flush=True)
            builder.add_new_people()
            print("3/8 建立分级档案 ...", flush=True)
            builder.build_profiles()
            print("4/8 关联称谓与古籍证据 ...", flush=True)
            builder.link_a_people()
            print("5/8 年表、关系与争议 ...", flush=True)
            builder.build_narratives()
            print("6/8 代表作品与完整度 ...", flush=True)
            builder.build_literary_works()
            builder.update_completeness()
            builder.update_work_hashes()
            print("7/8 校验 ...", flush=True)
            verification = builder.verify()
            print("8/8 输出报告 ...", flush=True)
            con.execute(
                "UPDATE classical_processing_runs SET finished_at=?,status=?,stats_json=?,error=NULL WHERE run_id=?",
                (now(), "completed", json.dumps(dict(builder.stats), ensure_ascii=False), RUN_ID),
            )
            _write_report(builder, verification, status)
            if args.dry_run:
                con.execute("ROLLBACK")
                print("dry-run 完成，已回滚。")
            else:
                con.execute("COMMIT")
                print(f"完成：{dict(builder.stats)}")
                print(f"报告：{REPORT_DIR / 'stage7_build_report.json'}")
        except Exception as exc:
            con.execute("ROLLBACK")
            Status = "failed"
            try:
                con.execute(
                    "UPDATE classical_processing_runs SET finished_at=?,status=?,error=? WHERE run_id=?",
                    (now(), "failed", f"{type(exc).__name__}: {exc}", RUN_ID),
                )
            except Exception:
                pass
            traceback.print_exc()
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
