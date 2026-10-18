"""Archive already obtained public API responses; this command never networks.

Manifest: [{"path": ..., "source_url": ..., "retrieved_at": ..., "timestamp_basis": ...}]
This is useful when a permitted earlier fetch succeeded but a later run is limited.
It does not imply permission to resume fetching a restricted source.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.collector import API, digest, now


def archive(manifest_path, output):
    manifest_path, output = Path(manifest_path), Path(output)
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    if output.exists():
        raise ValueError("归档输出目录已存在")
    prepared = []
    for row in rows:
        url = row["source_url"]
        query = parse_qs(urlsplit(url).query)
        if not url.startswith(API + "?") or query.get("action") != ["parse"]:
            raise ValueError("此归档器仅接收实际获取的公开维基文库 parse 响应")
        if datetime.fromisoformat(row["retrieved_at"]).utcoffset() is None:
            raise ValueError("获取时间须带时区；不明确时请勿伪造时间")
        raw = (manifest_path.parent / row["path"]).read_bytes()
        parsed = json.loads(raw)["parse"]
        if not all(k in parsed for k in ("title", "text", "revid")):
            raise ValueError("缺原始正文或修订号")
        prepared.append((row, raw))
    (output / "raw").mkdir(parents=True)
    for row, raw in prepared:
        key = digest(row["source_url"])
        (output / "raw" / (key + ".body")).write_bytes(raw)
        meta = {"request_url": row["source_url"], "retrieved_at": row["retrieved_at"], "http_status": 200, "headers": {}, "sha256": digest(raw), "error": "", "archived_at": now(), "timestamp_basis": row["timestamp_basis"], "provenance_note": "归档已观察到的成功响应；早期探测未保存 HTTP 响应头"}
        (output / "raw" / (key + ".json")).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(prepared)


def main():
    parser = argparse.ArgumentParser(description="把此前实际获取的 API 原始响应归档为离线重放输入；不联网")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        print(f"已归档 {archive(args.manifest, args.output)} 份实际响应")
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f"归档失败：{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
