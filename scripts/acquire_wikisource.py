"""Acquire selected Wikisource history chapters with retries and checkpoints.

The script deliberately fetches a small curated list (the biographies needed by
Stage 7), sleeps between requests, and never overwrites a valid saved response.
Every saved page keeps its page title, retrieval time, byte size and SHA-256 so
that the library can be audited and rebuilt.

Run from the project root:

    python -m scripts.acquire_wikisource
    python -m scripts.acquire_wikisource --key songshi_338
    python -m scripts.acquire_wikisource --list
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT_ROOT / "data" / "classical" / "wikisource_pages.json"
RAW_ROOT = PROJECT_ROOT / "data" / "external" / "wikisource"
MANIFEST = PROJECT_ROOT / "data" / "acquisition" / "wikisource_manifest.json"
USER_AGENT = "GujiID-Stage7/0.1 (educational course project; local)"
CHECKPOINT_DELAY = 0.8


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_manifest(path: Path = MANIFEST) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"provider": "Wikisource", "license_id": "CC-BY-SA-4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "pages": {}}


def save_manifest(manifest: dict, path: Path = MANIFEST) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def fetch_title(title: str, timeout: int = 25) -> tuple[bool, bytes, str]:
    encoded = urllib.parse.quote(title, safe="")
    url = f"https://zh.wikisource.org/w/index.php?title={encoded}&action=raw"
    result = subprocess.run(
        ["curl", "-sS", "-L", "--compressed", "-m", str(timeout),
         "-A", USER_AGENT, "-w", "\n%{http_code}", url],
        capture_output=True, timeout=timeout + 10,
    )
    body, _, code = result.stdout.rpartition(b"\n")
    if result.returncode != 0:
        return False, b"", f"curl exit {result.returncode}: {result.stderr.decode('utf-8', 'replace')[:200]}"
    status = code.decode("ascii", "replace").strip()
    if status != "200":
        return False, body, f"HTTP {status}"
    return True, body, ""


def fetch_oldid(title: str) -> str | None:
    encoded = urllib.parse.quote(title, safe="")
    url = ("https://zh.wikisource.org/w/api.php?action=query&format=json&formatversion=2"
           f"&prop=revisions&rvprop=ids%7Ctimestamp&titles={encoded}")
    try:
        result = subprocess.run(
            ["curl", "-sS", "-L", "-m", "8", "-A", USER_AGENT, url],
            capture_output=True, timeout=12,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout.decode("utf-8", "replace"))
        page = payload["query"]["pages"][0]
        revision = page["revisions"][0]
        return str(revision["revid"])
    except Exception:
        return None


def acquire(page: dict, manifest: dict, manifest_path: Path = MANIFEST) -> dict:
    key = page["key"]
    destination = RAW_ROOT / f"{key}.wikitext"
    previous = manifest["pages"].get(key, {})
    if destination.exists() and previous.get("status") == "ok":
        digest = sha256_bytes(destination.read_bytes())
        if digest == previous.get("sha256"):
            previous["skipped"] = True
            return previous

    errors: list[str] = []
    chosen = None
    body = b""
    for title in page["titles"]:
        for attempt in range(1, 4):
            ok, candidate, error = fetch_title(title)
            if ok and len(candidate) > 1200 and not candidate.lstrip().startswith(b"<!DOCTYPE"):
                chosen, body = title, candidate
                break
            if ok and candidate.lstrip().startswith(b"<!DOCTYPE"):
                error = "收到 HTML 错误页"
            errors.append(f"{title} 第{attempt}次: {error or '内容过短'}")
            time.sleep(min(2 * attempt, 6))
        if chosen:
            break
        time.sleep(CHECKPOINT_DELAY)
    if not chosen:
        entry = dict(previous)
        entry.update({"key": key, "status": "failed", "last_error": " | ".join(errors[-6:]),
                      "last_attempt_at": now()})
        manifest["pages"][key] = entry
        save_manifest(manifest, manifest_path)
        return entry

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    entry = {
        "key": key,
        "work": page["work"],
        "volume": page["volume"],
        "hint": page.get("hint", ""),
        "title": chosen,
        "url": "https://zh.wikisource.org/wiki/" + urllib.parse.quote(chosen.replace(" ", "_"), safe=""),
        "raw_url": "https://zh.wikisource.org/w/index.php?title=" + urllib.parse.quote(chosen, safe="") + "&action=raw",
        "license_id": "CC-BY-SA-4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "retrieved_at": now(),
        "bytes": len(body),
        "sha256": sha256_bytes(body),
        "oldid": None,
        "status": "ok",
        "last_error": "",
        "local_path": str(destination.relative_to(PROJECT_ROOT)),
    }
    manifest["pages"][key] = entry
    save_manifest(manifest, manifest_path)
    oldid = fetch_oldid(chosen)
    if oldid:
        entry["oldid"] = oldid
        manifest["pages"][key] = entry
        save_manifest(manifest, manifest_path)
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载并固定维基文库正史篇章")
    parser.add_argument("--key", nargs="+", help="只抓取指定 key")
    parser.add_argument("--list", action="store_true", help="列出配置")
    parser.add_argument("--refresh", action="store_true", help="重新抓取已有页面")
    parser.add_argument("--manifest", default=str(MANIFEST), help="独立检查点文件")
    args = parser.parse_args(argv)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    pages = config["pages"]
    if args.list:
        for page in pages:
            print(page["key"], page["work"], page["volume"], page["titles"][0])
        return 0
    if args.key:
        wanted = set(args.key)
        pages = [page for page in pages if page["key"] in wanted]
        if not pages:
            parser.error("没有匹配的 key")
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(args.manifest)
    manifest = load_manifest(manifest_path)
    if args.refresh:
        for key in list(manifest["pages"]):
            if (RAW_ROOT / f"{key}.wikitext").exists():
                (RAW_ROOT / f"{key}.wikitext").unlink()
            manifest["pages"].pop(key, None)
    failures = 0
    for index, page in enumerate(pages, 1):
        print(f"[{index}/{len(pages)}] {page['key']} {page['work']}{page['volume']} ...",
              flush=True)
        entry = acquire(page, manifest, manifest_path)
        if entry.get("status") == "ok":
            print(f"  OK {entry['title']} bytes={entry['bytes']} oldid={entry.get('oldid')}",
                  flush=True)
        else:
            failures += 1
            print(f"  失败：{entry.get('last_error', '')}", flush=True)
        time.sleep(CHECKPOINT_DELAY)
    print(f"完成：{len(pages) - failures}/{len(pages)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
