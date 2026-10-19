"""Build the deployable, path-sanitized SQLite snapshot.

The live database records local backup paths in three internal audit columns.
Those paths are not needed to serve the site and must not leak into a published
artifact, so this script replaces them with a portable ``<project>/...`` form.

Usage:
    python -m scripts.build_public_db
    python -m scripts.build_public_db --source data/guji.db --target data/guji-public.db
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_COLUMNS = (
    ("auto_batches", "backup_json"),
    ("auto_events", "details_json"),
    ("quality_batches", "backup_json"),
)


def scrub(value: object, prefixes: tuple[str, ...]) -> object:
    if isinstance(value, dict):
        return {key: scrub(item, prefixes) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, prefixes) for item in value]
    if isinstance(value, str):
        for prefix in prefixes:
            if value.startswith(prefix):
                relative = value[len(prefix):].lstrip("\\/")
                return "<project>/" + relative.replace("\\", "/")
    return value


def sanitize_paths(connection: sqlite3.Connection, prefixes: tuple[str, ...]) -> int:
    changed = 0
    for table, column in JSON_COLUMNS:
        rows = connection.execute(
            f'SELECT rowid, "{column}" FROM {table} WHERE "{column}" IS NOT NULL'
        ).fetchall()
        for rowid, text in rows:
            try:
                payload = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                continue
            cleaned = scrub(payload, prefixes)
            if cleaned == payload:
                continue
            connection.execute(
                f'UPDATE {table} SET "{column}" = ? WHERE rowid = ?',
                (
                    json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    rowid,
                ),
            )
            changed += 1
    return changed


def build(source: Path, target: Path) -> dict:
    if source.resolve() == target.resolve():
        raise ValueError("source and target must differ")
    if not source.is_file():
        raise FileNotFoundError(source)

    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(target) + suffix).unlink(missing_ok=True)

    with sqlite3.connect(source) as source_connection:
        with sqlite3.connect(target) as target_connection:
            source_connection.backup(target_connection)
            prefixes = (str(ROOT), str(ROOT).replace("\\", "/"))
            changed = sanitize_paths(target_connection, prefixes)
            target_connection.commit()

            integrity = target_connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = target_connection.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != "ok" or foreign_keys:
                raise ValueError(f"invalid snapshot: integrity={integrity}, foreign_keys={foreign_keys}")

            target_connection.execute("VACUUM")

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    checksum_path = target.with_name(target.name + ".sha256")
    relative_target = target.resolve().relative_to(ROOT).as_posix()
    checksum_path.write_text(f"{digest}  {relative_target}\n", encoding="utf-8", newline="\n")

    with sqlite3.connect(target) as connection:
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("people", "aliases", "sources", "evidences",
                          "classical_works", "classical_chapters",
                          "classical_evidence_links")
        }
    return {
        "target": str(target),
        "size": target.stat().st_size,
        "sha256": digest,
        "sanitized_rows": changed,
        "counts": counts,
        "checksum": str(checksum_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data/guji.db")
    parser.add_argument("--target", type=Path, default=ROOT / "data/guji-public.db")
    args = parser.parse_args()
    result = build(args.source, args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
