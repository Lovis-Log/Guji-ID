import argparse
import json

from app.collector import collect
from app.db import PROJECT_ROOT


def main():
    parser = argparse.ArgumentParser(description="小批量维基文库采集，只输出待审核 CSV，不写数据库")
    parser.add_argument("--seeds", default=str(PROJECT_ROOT / "data/collection_seeds.csv"))
    parser.add_argument("--output", required=True, help="必须为尚不存在的目录")
    parser.add_argument("--replay", help="从既有采集目录离线重放，不联网")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=10)
    args = parser.parse_args()
    try:
        summary = collect(args.seeds, args.output, replay=args.replay, timeout=args.timeout, interval=args.interval, max_requests=args.max_requests, max_pages=args.max_pages)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"采集失败：{exc}\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["candidate_people"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
