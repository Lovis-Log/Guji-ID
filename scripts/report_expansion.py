import argparse
import json

from app.expansion_report import expansion_report


def main():
    parser = argparse.ArgumentParser(description="只读汇总新旧候选、AI 状态、正式数据和真实身份案例")
    parser.add_argument("--old-dir", default="data/collections/2026-10-16-delivery")
    parser.add_argument("--new-dir", default="data/collections/stage4-tang-song-20")
    parser.add_argument("--review-dir", default="data/verifications/stage4-tang-song-20-reviewed")
    parser.add_argument("--db", default="data/guji.db")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        result = expansion_report(args.old_dir, args.new_dir, args.review_dir, args.db, args.output_dir)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"汇总失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
