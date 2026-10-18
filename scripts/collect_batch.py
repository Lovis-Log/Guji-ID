import argparse
import json

from app.bulk_collection import collect_bulk, load_config, previous_records, project_path


def main():
    parser = argparse.ArgumentParser(description="从正史目录/分节自动发现人物并批量采集；只输出待审核资料")
    parser.add_argument("--config", default="data/collection_configs/tang_song.json")
    parser.add_argument("--batch-name")
    parser.add_argument("--target-total", type=int)
    parser.add_argument("--dynasties", nargs="+")
    parser.add_argument("--sources", nargs="+")
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--max-candidates", type=int)
    parser.add_argument("--max-catalogue-pages", type=int)
    parser.add_argument("--output-root")
    parser.add_argument("--cache-dir")
    parser.add_argument("--offline", action="store_true", default=None)
    parser.add_argument("--no-cache", action="store_false", dest="use_cache", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="只显示累计扩容计划，不联网、不创建批次、不调用模型")
    args = parser.parse_args()
    overrides = {"batch_name": args.batch_name, "target_total_people": args.target_total, "dynasties": args.dynasties, "source_priority": args.sources, "max_requests": args.max_requests, "max_candidates": args.max_candidates, "max_catalogue_pages": args.max_catalogue_pages, "output_root": args.output_root, "cache_dir": args.cache_dir, "offline": args.offline, "use_cache": args.use_cache}
    try:
        if args.plan_only:
            config = load_config(args.config, overrides)
            current = project_path(config["output_root"]) / config["batch_name"]
            existing, _, _, _ = previous_records(config, current)
            result = {"batch_name": config["batch_name"], "existing_candidate_people": len(existing), "target_total_people": config["target_total_people"], "new_candidates_needed": max(0, config["target_total_people"]-len(existing)), "network_requests": 0, "model_calls": 0, "database_writes": 0}
        else:
            result = collect_bulk(args.config, overrides, resume=args.resume)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"批量采集失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
