"""Read-only, evidence-bound display index for licensed archival scans.

This file does not add historical claims. An empty index intentionally renders
the neutral digital title slip until an editor verifies a real scan.
"""

import json
from pathlib import Path

from app.catalog import PUBLIC_CTE, prepare, safe_source_url
from app.db import connect_db


INDEX_PATH = Path(__file__).with_name("archive_scans.json")
STATIC_ROOT = Path(__file__).with_name("static")


def _image_is_safe(value: str) -> bool:
    if safe_source_url(value):
        return True
    if not value.startswith("/static/archive-scans/"):
        return False
    relative = value.removeprefix("/static/")
    target = (STATIC_ROOT / relative).resolve()
    return target.is_relative_to(STATIC_ROOT.resolve()) and target.is_file()


def _checked_highlight(value):
    if not isinstance(value, dict) or value.get("reviewed") is not True:
        return None
    try:
        x, y, width, height = (float(value[key]) for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return None
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        return None
    return {"x": x * 100, "y": y * 100, "width": width * 100, "height": height * 100}


def scan_for_person(db_path, person_id: str):
    """Return the first usable scan, or None; never infer identity from a name."""
    try:
        entries = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(entries, list):
        return None
    with connect_db(db_path) as connection:
        prepare(connection)
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("person_id") != person_id:
                continue
            if entry.get("review_status") != "verified":
                continue
            if not all(isinstance(entry.get(key), str) and entry[key].strip() for key in
                       ("evidence_id", "provider", "image_url", "source_url", "license")):
                continue
            if not _image_is_safe(entry["image_url"]) or not safe_source_url(entry["source_url"]):
                continue
            row = connection.execute(PUBLIC_CTE + """
                SELECT e.evidence_id, e.book_title, e.volume, e.chapter, e.original_url
                FROM public_evidence e JOIN public_aliases a USING (alias_id)
                WHERE a.person_id = ? AND e.evidence_id = ?
                  AND e.usage_relation = 'supports' AND e.evidence_nature = 'direct'
                LIMIT 1
            """, (person_id, entry["evidence_id"])).fetchone()
            if row is None or row["original_url"] != entry["source_url"]:
                continue
            return {
                "person_id": person_id,
                "evidence_id": row["evidence_id"],
                "book_title": row["book_title"],
                "volume": row["volume"],
                "chapter": row["chapter"],
                "provider": entry["provider"],
                "image_url": entry["image_url"],
                "source_url": entry["source_url"],
                "license": entry["license"],
                "highlight": _checked_highlight(entry.get("highlight")),
            }
    return None
