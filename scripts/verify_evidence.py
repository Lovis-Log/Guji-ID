import argparse
import json

from app.verification import invoke_reviewer, prepare_batch, summarize_batch, validate_review


def main():
    parser = argparse.ArgumentParser(description="现有批次的确定性核验与显式外部只读核验；不写数据库")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--input-dir", required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--main-task-id", default=None, help="同一主任务保持固定；不能用于绕过既有停止状态")
    call = commands.add_parser("call", help="只在用户明确授权本次调用后使用；不自动重试")
    call.add_argument("--batch-dir", required=True)
    summarize = commands.add_parser("summarize", help="解析真实调用结果或明确报告失败，不调用模型")
    summarize.add_argument("--batch-dir", required=True)
    summarize.add_argument("--allow-free-text-narration", action="store_true", help="显式允许保留外部中文叙述原文；未知身份码保守降级，不猜测类别")
    check = commands.add_parser("check", help="复核已封存的输入、回执和输出哈希；不调用模型")
    check.add_argument("--batch-dir", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare_batch(args.input_dir, args.output_dir, **({"main_task_id": args.main_task_id} if args.main_task_id else {}))
        elif args.command == "call":
            result = invoke_reviewer(args.batch_dir)
        elif args.command == "summarize":
            result = summarize_batch(args.batch_dir, allow_free_text_narration=args.allow_free_text_narration)
        else:
            batch, tasks, results = validate_review(args.batch_dir)
            result = {"batch_id": batch["batch_id"], "tasks": len(tasks), "results": len(results), "sealed_files": "passed", "source_snapshot": "passed", "model_calls": 0, "database_writes": 0}
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"核验失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
