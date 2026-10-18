"""Independent repeatable automatic expansion CLI. No model calls by default."""
import argparse
import json
from pathlib import Path
from app.automatic_ingestion import inspect_source, stage_records, run_pipeline, rollback_batch
from app.db import default_db_path
from app.verification import json_read, json_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["stage", "dry-run", "run", "rollback"])
    parser.add_argument("--source-dir", default="data/external/cbdb-20250520")
    parser.add_argument("--staging", default="data/ingestion/stage6.sqlite3")
    parser.add_argument("--db", default=None)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=1200)
    parser.add_argument("--target", type=int, default=400)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-aliases", type=int, default=2)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument("--batch-id")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if args.command == "rollback":
        if not args.batch_id:
            parser.error("rollback需要--batch-id")
        result = rollback_batch(args.db or default_db_path(), args.batch_id, output)
    else:
        source_dir = Path(args.source_dir)
        release = json_read(source_dir / "release.json")
        source = source_dir / "cbdb.sqlite3"
        if args.command == "stage":
            json_write(output / "source_schema.json", inspect_source(source, release))
            result = stage_records(source, release, args.staging, args.limit)
            json_write(output / "staging_result.json", result)
        else:
            result = run_pipeline(source, release, args.staging, args.db or default_db_path(), output,
                target_people=args.target, batch_size=args.batch_size, max_aliases=args.max_aliases,
                max_batches=args.max_batches, dry_run=args.command == "dry-run")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
