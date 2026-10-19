"""Acquire pinned Kanripo classical-text repositories for the Stage 7 library.

Only public repositories listed by the Kanripo organisation are used.  The
script is resumable: an existing clone is verified and kept, a failed clone is
retried into a staging directory and never replaces a valid local copy.

Run from the project root:

    python -m scripts.acquire_kanripo --all
    python -m scripts.acquire_kanripo --work KR2a0001 KR2a0032
    python -m scripts.acquire_kanripo --list
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = PROJECT_ROOT / "data" / "external" / "kanripo"
MANIFEST = PROJECT_ROOT / "data" / "acquisition" / "kanripo_manifest.json"
LICENSE_ID = "CC-BY-SA-4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
LICENSE_BASIS = "https://github.com/kanripo (organisation licence statement: CC BY-SA 4.0)"

# Stable catalogue identifiers from Kanripo KR-Catalog / KR/KR2a.txt.
WORKS = {
    "KR2a0001": ("史記", "司馬遷", "漢", "正史"),
    "KR2a0007": ("漢書", "班固", "漢", "正史"),
    "KR2a0009": ("後漢書", "范曄", "南朝宋", "正史"),
    "KR2a0012": ("三國志", "陳壽", "晉", "正史"),
    "KR2a0015": ("晉書", "房玄齡等", "唐", "正史"),
    "KR2a0016": ("宋書", "沈約", "梁", "正史"),
    "KR2a0017": ("南齊書", "蕭子顯", "梁", "正史"),
    "KR2a0018": ("梁書", "姚思廉", "唐", "正史"),
    "KR2a0019": ("陳書", "姚思廉", "唐", "正史"),
    "KR2a0020": ("魏書", "魏收", "北齊", "正史"),
    "KR2a0021": ("北齊書", "李百藥", "唐", "正史"),
    "KR2a0022": ("周書", "令狐德棻等", "唐", "正史"),
    "KR2a0023": ("隋書", "魏徵等", "唐", "正史"),
    "KR2a0024": ("南史", "李延壽", "唐", "正史"),
    "KR2a0025": ("北史", "李延壽", "唐", "正史"),
    "KR2a0026": ("舊唐書", "劉昫等", "後晉", "正史"),
    "KR2a0027": ("新唐書", "歐陽修、宋祁", "宋", "正史"),
    "KR2a0028": ("新唐書糾謬", "吳縝", "宋", "史評"),
    "KR2a0029": ("舊五代史", "薛居正等", "宋", "正史"),
    "KR2a0030": ("新五代史", "歐陽修", "宋", "正史"),
    "KR2a0032": ("宋史", "脫脫等", "元", "正史"),
    "KR2a0033": ("遼史", "脫脫等", "元", "正史"),
    "KR2a0035": ("金史", "脫脫等", "元", "正史"),
    "KR2a0036": ("元史", "宋濂等", "明", "正史"),
    "KR2a0038": ("明史", "張廷玉等", "清", "正史"),
    "KR2a0039": ("清史稿", "趙爾巽等", "民國", "正史"),
}
DEFAULT_WORKS = (
    "KR2a0001", "KR2a0007", "KR2a0009", "KR2a0012", "KR2a0015",
    "KR2a0016", "KR2a0024", "KR2a0026", "KR2a0027", "KR2a0029",
    "KR2a0030", "KR2a0032", "KR2a0036", "KR2a0038", "KR2a0039",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(args: list[str], cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
        timeout=timeout, encoding="utf-8", errors="replace",
    )


def _git_head(path: Path) -> str:
    result = _run(["git", "rev-parse", "HEAD"], cwd=path, timeout=30)
    return result.stdout.strip() if result.returncode == 0 else ""


def _clone(repo: str, destination: Path, attempts: int = 4) -> None:
    staging = destination.with_name(destination.name + ".incoming")
    if staging.exists():
        shutil.rmtree(staging)
    last_error = ""
    for attempt in range(1, attempts + 1):
        if staging.exists():
            shutil.rmtree(staging)
        result = _run(
            ["git", "clone", "--depth", "1", "--quiet",
             f"https://github.com/kanripo/{repo}.git", str(staging)],
            timeout=600,
        )
        if result.returncode == 0 and _git_head(staging):
            staging.rename(destination)
            return
        last_error = (result.stderr or result.stdout or "").strip()[-500:]
        time.sleep(2 * attempt)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    raise RuntimeError(f"{repo} 克隆失败（已重试 {attempts} 次）：{last_error}")


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"provider": "Kanripo", "license_id": LICENSE_ID,
            "license_url": LICENSE_URL, "license_basis_url": LICENSE_BASIS,
            "works": {}}


def save_manifest(manifest: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def acquire(repo: str, manifest: dict) -> dict:
    title, author, dynasty, category = WORKS[repo]
    destination = SOURCE_ROOT / repo
    created = False
    error = ""
    if not destination.exists():
        try:
            _clone(repo, destination)
            created = True
        except Exception as exc:  # keep the previous manifest entry on failure
            error = str(exc)
            entry = manifest["works"].setdefault(repo, {})
            entry.update({"title": title, "status": "failed", "last_error": error,
                          "last_attempt_at": _now()})
            save_manifest(manifest)
            return entry
    head = _git_head(destination)
    files = sorted(p.name for p in destination.glob("*.txt"))
    entry = {
        "repo": repo,
        "title": title,
        "author": author,
        "dynasty": dynasty,
        "category": category,
        "license_id": LICENSE_ID,
        "license_url": LICENSE_URL,
        "license_basis_url": LICENSE_BASIS,
        "source_url": f"https://github.com/kanripo/{repo}",
        "commit": head,
        "retrieved_at": _now(),
        "status": "ok",
        "files": files,
        "file_count": len(files),
        "total_bytes": sum((destination / name).stat().st_size for name in files),
        "created_this_run": created,
        "last_error": "",
    }
    # Verify that every listed text file is readable UTF-8 and record content
    # hashes.  A later build compares its stored text against these hashes.
    hashes = {}
    for name in files:
        path = destination / name
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            entry["status"] = "invalid_encoding"
            entry["last_error"] = f"{name} 不是 UTF-8"
            continue
        hashes[name] = _sha256(path)
    entry["file_sha256"] = hashes
    manifest["works"][repo] = entry
    save_manifest(manifest)
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载并固定 Kanripo 正史文本")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="下载 Stage 7 默认书目")
    group.add_argument("--work", nargs="+", help="指定 Kanripo 编号")
    group.add_argument("--list", action="store_true", help="列出可用书目")
    args = parser.parse_args(argv)

    if args.list:
        for repo, (title, author, dynasty, category) in WORKS.items():
            mark = "*" if repo in DEFAULT_WORKS else " "
            print(f"{mark} {repo} {title} {dynasty} {author} [{category}]")
        return 0

    repos = list(DEFAULT_WORKS) if args.all else args.work
    unknown = [repo for repo in repos if repo not in WORKS]
    if unknown:
        parser.error("未知书目：" + "、".join(unknown))
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    failures = 0
    for index, repo in enumerate(repos, 1):
        print(f"[{index}/{len(repos)}] {repo} {WORKS[repo][0]} ...", flush=True)
        entry = acquire(repo, manifest)
        if entry.get("status") != "ok":
            failures += 1
            print(f"  失败：{entry.get('last_error', '')}", flush=True)
        else:
            print(f"  OK commit={entry['commit'][:12]} files={entry['file_count']} "
                  f"bytes={entry['total_bytes']}", flush=True)
    print(f"完成：{len(repos) - failures}/{len(repos)}；清单：{MANIFEST}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
