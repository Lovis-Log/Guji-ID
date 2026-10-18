import argparse
import json
import sqlite3

from app.batch_import import dry_run, import_approved, record_approval
from app.db import default_db_path


def main():
    parser = argparse.ArgumentParser(description="待批准 AI 核验批次的隔离 dry-run、明确人工批准与安全增量导入")
    sub = parser.add_subparsers(dest="command", required=True)
    dry = sub.add_parser("dry-run")
    dry.add_argument("--batch-dir", required=True)
    dry.add_argument("--db", default=None)
    dry.add_argument("--output-dir", required=True)
    dry.add_argument("--alias-id-file", help="可选：JSON 关系 ID 数组，只预演指定合格关系，不构成人工批准")
    approve = sub.add_parser("approve", help="只在人工明确批准该精确清单后使用，不调用模型或写数据库")
    approve.add_argument("--batch-dir", required=True)
    approve.add_argument("--decision-file", required=True)
    approve.add_argument("--output", required=True)
    apply = sub.add_parser("import")
    apply.add_argument("--batch-dir", required=True)
    apply.add_argument("--approval", required=True)
    apply.add_argument("--export-dir", required=True)
    apply.add_argument("--db", default=None)
    args = parser.parse_args()
    try:
        if args.command == "dry-run":
            from pathlib import Path
            alias_ids = json.loads(Path(args.alias_id_file).read_text(encoding="utf-8")) if args.alias_id_file else None
            result = dry_run(args.batch_dir, args.db or default_db_path(), args.output_dir, alias_ids=alias_ids)
        elif args.command == "approve":
            result = record_approval(args.batch_dir, args.decision_file, args.output)
        else:
            result = import_approved(args.batch_dir, args.approval, args.export_dir, args.db or default_db_path())
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        parser.exit(1, f"批次操作拒绝/失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
