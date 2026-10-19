"""Read-only, evidence-bound display indexes for the archive title slip.

Two deliberately separate layers are exposed:

* ``archive_scans.json`` — verified scans of real books. An entry is shown only
  when it is tied to the exact public evidence and source already in the
  database.
* ``figure_media.json`` — AI-generated, antiquarian-style illustrations. These
  are never treated as historical evidence; the manifest must carry the
  explicit AI disclosure and each person ID is checked against a verified
  canonical name before an image is returned.

An empty index simply renders the neutral digital title slip / an empty-state
notice; nothing is inferred from a person's name.
"""

import json
from pathlib import Path

from app.catalog import PUBLIC_CTE, prepare, safe_source_url
from app.db import connect_db


INDEX_PATH = Path(__file__).with_name("archive_scans.json")
FIGURE_INDEX_PATH = Path(__file__).with_name("figure_media.json")
STATIC_ROOT = Path(__file__).with_name("static")

FIGURE_STATIC_PREFIX = "/static/figure-media/"
FIGURE_DISCLOSURE = "AI生成·拟古演绎，非真实古籍书影"


def _image_is_safe(value: str) -> bool:
    if safe_source_url(value):
        return True
    if not value.startswith("/static/archive-scans/"):
        return False
    relative = value.removeprefix("/static/")
    target = (STATIC_ROOT / relative).resolve()
    return target.is_relative_to(STATIC_ROOT.resolve()) and target.is_file()


def _figure_is_safe(value: str) -> bool:
    """AI figures must be local, explicitly listed WebP assets."""
    if not isinstance(value, str) or not value.startswith(FIGURE_STATIC_PREFIX):
        return False
    if not value.endswith(".webp"):
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


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _validated_scan(connection, person_id: str, entry):
    """Return one scan only when every identity/evidence/source check passes."""
    if not isinstance(entry, dict) or entry.get("person_id") != person_id:
        return None
    if entry.get("review_status") != "verified":
        return None
    if not all(isinstance(entry.get(key), str) and entry[key].strip() for key in
               ("evidence_id", "provider", "image_url", "source_url", "license")):
        return None
    if not _image_is_safe(entry["image_url"]) or not safe_source_url(entry["source_url"]):
        return None
    row = connection.execute(PUBLIC_CTE + """
        SELECT e.evidence_id, e.book_title, e.volume, e.chapter, e.original_url
        FROM public_evidence e JOIN public_aliases a USING (alias_id)
        WHERE a.person_id = ? AND e.evidence_id = ?
          AND e.usage_relation = 'supports' AND e.evidence_nature = 'direct'
        LIMIT 1
    """, (person_id, entry["evidence_id"])).fetchone()
    if row is None or row["original_url"] != entry["source_url"]:
        return None
    # Optional manifest fields must agree with the authoritative evidence row.
    if entry.get("book_title"):
        if not isinstance(entry["book_title"], str) or entry["book_title"].strip() != row["book_title"]:
            return None
    if entry.get("volume"):
        if not isinstance(entry["volume"], str) or entry["volume"].strip() != (row["volume"] or ""):
            return None
    return {
        "person_id": person_id,
        "evidence_id": row["evidence_id"],
        "book_title": row["book_title"],
        "volume": row["volume"],
        "chapter": row["chapter"],
        "provider": entry["provider"],
        "edition": entry.get("edition"),
        "holding": entry.get("holding"),
        "image_url": entry["image_url"],
        "source_url": entry["source_url"],
        "license": entry["license"],
        "highlight": _checked_highlight(entry.get("highlight")),
    }


def scans_for_person(db_path, person_id: str):
    """Return every usable scan for a person, or an empty list."""
    entries = _read_json(INDEX_PATH)
    if not isinstance(entries, list):
        return []
    with connect_db(db_path) as connection:
        prepare(connection)
        return [
            scan for entry in entries
            if (scan := _validated_scan(connection, person_id, entry)) is not None
        ]


def scan_for_person(db_path, person_id: str):
    """Backward-compatible single-scan helper; never infers identity from a name."""
    scans = scans_for_person(db_path, person_id)
    return scans[0] if scans else None


def _validated_figures(db_path):
    """Load AI figure entries whose person ID and canonical name match the DB."""
    data = _read_json(FIGURE_INDEX_PATH)
    if not isinstance(data, dict) or data.get("schema") != 1:
        return []
    disclosure = data.get("disclosure")
    warning = data.get("evidence_warning")
    if disclosure != FIGURE_DISCLOSURE or not isinstance(warning, str):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    figures = []
    with connect_db(db_path) as connection:
        for entry in items:
            if not isinstance(entry, dict):
                continue
            if entry.get("ai_generated") is not True or entry.get("review_status") != "verified":
                continue
            required = ("person_id", "db_name", "display_name", "image_url", "alt")
            if not all(isinstance(entry.get(key), str) and entry[key].strip() for key in required):
                continue
            if not _figure_is_safe(entry["image_url"]):
                continue
            row = connection.execute(
                "SELECT canonical_name FROM people WHERE person_id = ? AND verification_status = 'verified'",
                (entry["person_id"],),
            ).fetchone()
            if row is None or row["canonical_name"] != entry["db_name"]:
                continue
            figures.append({
                "person_id": entry["person_id"],
                "display_name": entry["display_name"],
                "image_url": entry["image_url"],
                "alt": entry["alt"],
                "ai_generated": True,
                "disclosure": disclosure,
                "evidence_warning": warning,
            })
    return figures


def figures_for_person(db_path, person_id: str):
    """Return AI illustrations bound to this exact verified person ID."""
    return [figure for figure in _validated_figures(db_path)
            if figure["person_id"] == person_id]


def figure_gallery(db_path):
    """Return every validated AI illustration for the neutral gallery state."""
    return _validated_figures(db_path)
