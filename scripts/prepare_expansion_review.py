import argparse
import json

from app.bulk_collection import expansion_review_input
from app.verification import prepare_batch


def main():
    parser = argparse.ArgumentParser(description="新批次及旧共享称呼的核验材料准备；不调用模型，不批准，不入库")
    parser.add_argument("--collection-dir", required=True)
    parser.add_argument("--old-batches", nargs="+", default=["data/collections/2026-10-16-delivery"])
    parser.add_argument("--input-output-dir", required=True)
    parser.add_argument("--review-output-dir", required=True)
    parser.add_argument("--main-task-id", required=True, help="同一主任务固定，不得通过换 ID 绕过历史停止或额度")
    args = parser.parse_args()
    try:
        source = expansion_review_input(args.collection_dir, args.old_batches, args.input_output_dir)
        result = prepare_batch(args.input_output_dir, args.review_output_dir, main_task_id=args.main_task_id)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"扩容核验准备失败：{exc}\n")
    print(json.dumps({"source_counts":source, "verification":result, "model_calls":0,"database_writes":0}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
