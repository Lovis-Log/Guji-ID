"""Merge parallel Wikisource checkpoints into one verified manifest.

Only entries whose raw file exists and whose SHA-256 matches are kept.  The
newest successful retrieval wins; failures are retained as gaps.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACQ = ROOT / "data" / "acquisition"
OUT = ACQ / "wikisource_manifest_combined.json"


def main() -> int:
    merged: dict[str, dict] = {}
    for path in sorted(ACQ.glob("wikisource_manifest*.json")):
        if path.name == OUT.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key, entry in data.get("pages", {}).items():
            if entry.get("status") != "ok":
                merged.setdefault(key, entry)
                continue
            local = ROOT / entry.get("local_path", "")
            if not local.exists():
                continue
            digest = hashlib.sha256(local.read_bytes()).hexdigest()
            if digest != entry.get("sha256"):
                continue
            previous = merged.get(key)
            if previous is None or previous.get("status") != "ok":
                merged[key] = entry
            elif (entry.get("retrieved_at") or "") >= (previous.get("retrieved_at") or ""):
                merged[key] = entry
    payload = {
        "provider": "Wikisource",
        "license_id": "CC-BY-SA-4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": dict(sorted(merged.items())),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = sum(1 for entry in merged.values() if entry.get("status") == "ok")
    print(f"合并 {len(merged)} 页，成功 {ok} 页；输出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
