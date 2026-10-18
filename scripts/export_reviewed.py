import argparse
import json

from app.collection_review import export_reviewed


def main():
    parser = argparse.ArgumentParser(description="仅导出人工通过且证据链完整的四表 CSV")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        result = export_reviewed(args.input_dir, args.output_dir)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"审核导出失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
