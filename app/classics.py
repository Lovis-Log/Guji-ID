"""Read-only access to the Stage 7 classical library.

All values returned here are presentation-ready.  Internal hashes, file paths
and processing metadata stay behind the raw-record endpoints.
"""

from __future__ import annotations

import sqlite3
from urllib.parse import quote

from app.db import connect_db

CATEGORY_LABELS = {
    "direct_alias": "直接称谓证据",
    "direct_identity": "直接身份记载",
    "biography": "生平事迹",
    "literary": "文学作品",
    "later_comment": "后世评论",
}
TIER_LABELS = {"A": "A级 · 深度档案", "B": "B级 · 重要人物", "C": "C级 · 基本档案"}
STATUS_LABELS = {"verified": "已核对", "pending": "待核验", "disputed": "存在争议", "rejected": "未采用"}
COMPLETENESS_LABELS = {
    "full": "完整篇章", "partial": "部分原文",
}


def has_library(db_path) -> bool:
    with connect_db(db_path) as connection:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='classical_chapters'"
        ).fetchone()
        return bool(row)


def _reader_url(work_id: str, chapter_id: str, *, person_id: str = "", link_id: str = "") -> str:
    url = f"/classics/{quote(work_id, safe='')}/{quote(chapter_id, safe='')}"
    params = []
    if person_id:
        params.append("person=" + quote(person_id, safe=""))
    if link_id:
        params.append("evidence=" + quote(link_id, safe=""))
    if params:
        url += "?" + "&".join(params)
    if link_id:
        url += "#ev-" + quote(link_id, safe="")
    return url


def reader_link(work_id: str, chapter_id: str, *, person_id: str = "",
                link_id: str = "") -> str:
    return _reader_url(work_id, chapter_id, person_id=person_id, link_id=link_id)


def work_index(db_path) -> list[dict]:
    if not has_library(db_path):
        return []
    with connect_db(db_path) as connection:
        rows = connection.execute(
            """SELECT w.*, count(c.chapter_id) AS chapter_count,
                      coalesce(sum(c.char_count),0) AS char_count
               FROM classical_works w LEFT JOIN classical_chapters c USING(work_id)
               GROUP BY w.work_id ORDER BY w.title, w.digital_edition"""
        ).fetchall()
        return [dict(row) for row in rows]


def get_work(db_path, work_id: str) -> dict | None:
    if not has_library(db_path):
        return None
    with connect_db(db_path) as connection:
        work = connection.execute("SELECT * FROM classical_works WHERE work_id=?", (work_id,)).fetchone()
        if not work:
            return None
        chapters = connection.execute(
            """SELECT chapter_id,volume,chapter_title,sequence,char_count,
                      completeness,verification_status
               FROM classical_chapters WHERE work_id=? ORDER BY sequence,chapter_id""",
            (work_id,),
        ).fetchall()
    result = dict(work)
    result["chapters"] = [dict(row) for row in chapters]
    return result


def get_chapter(db_path, chapter_id: str) -> dict | None:
    if not has_library(db_path):
        return None
    with connect_db(db_path) as connection:
        row = connection.execute(
            """SELECT c.*, w.title AS work_title, w.author AS work_author,
                      w.dynasty AS work_dynasty, w.source_platform, w.source_url AS work_url,
                      w.digital_edition, w.license_id, w.license_url
               FROM classical_chapters c JOIN classical_works w USING(work_id)
               WHERE c.chapter_id=?""", (chapter_id,)
        ).fetchone()
        if not row:
            return None
        chapter = dict(row)
        chapter["paragraphs"] = [dict(item) for item in connection.execute(
            """SELECT paragraph_id,ordinal,scheme,text,char_start,char_end,page_label
               FROM classical_paragraphs WHERE chapter_id=? AND scheme='reading'
               ORDER BY ordinal""", (chapter_id,)).fetchall()]
        chapter["neighbours"] = [dict(item) for item in connection.execute(
            """SELECT chapter_id,volume,chapter_title,sequence FROM classical_chapters
               WHERE work_id=? AND sequence < ? ORDER BY sequence DESC LIMIT 1""",
            (chapter["work_id"], chapter["sequence"])).fetchall()]
        chapter["next"] = [dict(item) for item in connection.execute(
            """SELECT chapter_id,volume,chapter_title,sequence FROM classical_chapters
               WHERE work_id=? AND sequence > ? ORDER BY sequence LIMIT 1""",
            (chapter["work_id"], chapter["sequence"])).fetchall()]
        chapter["evidence"] = [dict(item) for item in connection.execute(
            """SELECT l.link_id,l.quote,l.quote_start,l.quote_end,l.evidence_category,
                      l.claim,l.verification_status,p.canonical_name,l.person_id
               FROM classical_evidence_links l JOIN people p USING(person_id)
               WHERE l.chapter_id=? AND l.verification_status='verified'
                 AND l.identity_match='confirmed'
               ORDER BY l.quote_start,l.quote_end""", (chapter_id,)).fetchall()]
        for item in chapter["evidence"]:
            item["category_label"] = CATEGORY_LABELS.get(item["evidence_category"], "古籍记载")
            item["status_label"] = STATUS_LABELS.get(item["verification_status"], "已核对")
            item["reader_url"] = _reader_url(chapter["work_id"], chapter_id,
                                             person_id=item["person_id"], link_id=item["link_id"])
        chapter["people"] = list({
            (item["person_id"], item["canonical_name"])
            for item in chapter["evidence"]
        })
        chapter["people"] = [{"person_id": person_id, "canonical_name": name}
                             for person_id, name in sorted(chapter["people"], key=lambda value: value[1])]
    return chapter


def highlight_segments(text: str, ranges: list[dict], selected: str = "") -> list[dict]:
    """Merge evidence ranges into non-overlapping display segments."""
    cleaned = []
    for item in sorted(ranges, key=lambda value: (value["quote_start"], value["quote_end"])):
        start, end = int(item["quote_start"]), int(item["quote_end"])
        if start < 0 or end <= start or end > len(text):
            continue
        cleaned.append((start, end, item))
    merged: list[dict] = []
    for start, end, item in cleaned:
        if merged and start <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], end)
            merged[-1]["links"].append(item)
        else:
            merged.append({"start": start, "end": end, "links": [item]})
    segments: list[dict] = []
    cursor = 0
    for group in merged:
        if group["start"] > cursor:
            segments.append({"text": text[cursor:group["start"]], "marked": False, "links": []})
        segments.append({
            "text": text[group["start"]:group["end"]],
            "marked": True,
            "links": group["links"],
            "selected": any(item["link_id"] == selected for item in group["links"]),
            "first_id": group["links"][0]["link_id"],
        })
        cursor = group["end"]
    if cursor < len(text):
        segments.append({"text": text[cursor:], "marked": False, "links": []})
    return segments


def selected_context(chapter: dict, link_id: str) -> dict | None:
    link = next((item for item in chapter.get("evidence", []) if item["link_id"] == link_id), None)
    if not link:
        return None
    paragraph = None
    for item in chapter["paragraphs"]:
        if item["char_start"] <= link["quote_start"] and link["quote_end"] <= item["char_end"]:
            paragraph = item
            break
    context = []
    if paragraph:
        low = max(1, paragraph["ordinal"] - 1)
        high = paragraph["ordinal"] + 1
        context = [item for item in chapter["paragraphs"] if low <= item["ordinal"] <= high]
    rendered = []
    for item in context:
        local_start = max(0, link["quote_start"] - item["char_start"])
        local_end = min(len(item["text"]), link["quote_end"] - item["char_start"])
        if 0 <= local_start < local_end <= len(item["text"]):
            rendered.append({
                "label": item.get("page_label") or "",
                "before": item["text"][:local_start],
                "quote": item["text"][local_start:local_end],
                "after": item["text"][local_end:],
            })
        else:
            rendered.append({"label": item.get("page_label") or "", "before": item["text"],
                             "quote": "", "after": ""})
    return {"link": link, "paragraph": paragraph, "paragraphs": context,
            "segments": rendered}


def person_library(db_path, person_id: str) -> dict | None:
    if not has_library(db_path):
        return None
    with connect_db(db_path) as connection:
        profile = connection.execute(
            "SELECT * FROM person_profiles WHERE person_id=?", (person_id,)
        ).fetchone()
        if not profile:
            return None
        profile = dict(profile)
        links = [dict(row) for row in connection.execute(
            """SELECT l.link_id,l.quote,l.quote_start,l.quote_end,l.evidence_category,
                      l.claim,l.explanation,l.verification_status,l.identity_match,
                      c.chapter_id,c.volume,c.chapter_title,c.work_id,c.original_text,
                      w.title AS work_title,w.author AS work_author,w.dynasty AS work_dynasty,
                      w.source_platform,w.digital_edition,w.license_id
               FROM classical_evidence_links l
               JOIN classical_chapters c USING(chapter_id)
               JOIN classical_works w USING(work_id)
               WHERE l.person_id=? AND l.verification_status='verified'
                 AND l.identity_match='confirmed'
               ORDER BY CASE w.source_platform WHEN '维基文库' THEN 0 ELSE 1 END,
                        w.title,c.sequence,l.quote_start,l.link_id""",
            (person_id,)).fetchall()]
        for item in links:
            item["category_label"] = CATEGORY_LABELS.get(item["evidence_category"], "古籍记载")
            item["status_label"] = STATUS_LABELS.get(item["verification_status"], "已核对")
            item["reader_url"] = _reader_url(item["work_id"], item["chapter_id"],
                                             person_id=person_id, link_id=item["link_id"])
            item["quote_preview"] = item["quote"][:220] + ("…" if len(item["quote"]) > 220 else "")
            item["quote_is_excerpt"] = len(item["quote"]) > 220
        works: dict[str, dict] = {}
        for item in links:
            key = item["work_id"]
            entry = works.setdefault(key, {
                "work_id": key, "title": item["work_title"], "author": item["work_author"],
                "dynasty": item["work_dynasty"], "edition": item["digital_edition"],
                "platform": item["source_platform"], "license": item["license_id"],
                "chapters": {},
            })
            entry["chapters"].setdefault(item["chapter_id"], {
                "chapter_id": item["chapter_id"], "volume": item["volume"],
                "title": item["chapter_title"], "count": 0,
                "reader_url": _reader_url(item["work_id"], item["chapter_id"], person_id=person_id),
            })["count"] += 1
        timeline = [dict(row) for row in connection.execute(
            """SELECT e.*,l.link_id,l.quote,l.evidence_category,
                      c.chapter_id,c.work_id,w.title AS work_title
               FROM person_timeline_events e
               LEFT JOIN classical_evidence_links l ON l.link_id=e.evidence_link_id
               LEFT JOIN classical_chapters c ON c.chapter_id=l.chapter_id
               LEFT JOIN classical_works w ON w.work_id=c.work_id
               WHERE e.person_id=? ORDER BY coalesce(e.year_value, 999999), e.year_label""",
            (person_id,)).fetchall()]
        for item in timeline:
            if item.get("link_id") and item.get("work_id"):
                item["reader_url"] = _reader_url(item["work_id"], item["chapter_id"],
                                                 person_id=person_id, link_id=item["link_id"])
        literary = [dict(row) for row in connection.execute(
            """SELECT * FROM person_literary_works WHERE person_id=? ORDER BY title""",
            (person_id,)).fetchall()]
        relations = [dict(row) for row in connection.execute(
            """SELECT * FROM person_relations WHERE person_id=? ORDER BY relation_type,related_name""",
            (person_id,)).fetchall()]
        disputes = [dict(row) for row in connection.execute(
            """SELECT * FROM person_disputes WHERE person_id=? ORDER BY topic""",
            (person_id,)).fetchall()]
        counts = {
            "links": len(links),
            "distinct_quotes": len({(item["chapter_id"], item["quote_start"], item["quote_end"])
                                    for item in links}),
            "chapters": len({item["chapter_id"] for item in links}),
            "works": len(works),
        }
    profile["tier_label"] = TIER_LABELS.get(profile["tier"], profile["tier"])
    profile["completeness"] = {}
    try:
        import json
        profile["completeness"] = json.loads(profile.get("completeness_json") or "{}")
    except ValueError:
        profile["completeness"] = {}
    return {
        "profile": profile,
        "links": links,
        "works": list(works.values()),
        "timeline": timeline,
        "literary": literary,
        "relations": relations,
        "disputes": disputes,
        "counts": counts,
    }


def reader_data(db_path, work_id: str, chapter_id: str, *, person_id: str = "",
                selected: str = "") -> dict | None:
    work = get_work(db_path, work_id)
    if not work:
        return None
    chapter = get_chapter(db_path, chapter_id)
    if not chapter or chapter["work_id"] != work_id:
        return None
    ranges = chapter["evidence"]
    if person_id:
        ranges = [item for item in ranges if item["person_id"] == person_id]
    segments = highlight_segments(chapter["original_text"], ranges, selected=selected)
    context = selected_context(chapter, selected) if selected else None
    return {"work": work, "chapter": chapter, "segments": segments,
            "context": context, "selected": selected, "person_id": person_id}
