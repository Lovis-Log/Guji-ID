"""Official CBDB acquisition into staging, with license scope and byte checks."""
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.collector import now, plain
from app.verification import json_read, json_write

LATEST = "https://raw.githubusercontent.com/cbdb-project/cbdb_sqlite/master/latest.json"
LICENSE = "https://cbdb.hsites.harvard.edu/cbdb-data-licensing-terms"
STANDALONE = "https://cbdb.hsites.harvard.edu/download-cbdb-standalone-database"
HF_API = "https://huggingface.co/api/datasets/cbdb/cbdb-sqlite"
USER_AGENT = "GujiIDCourse/0.6 (non-commercial educational data integration)"


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(2 ** 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url):
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in {"raw.githubusercontent.com", "cbdb.hsites.harvard.edu", "huggingface.co"}:
        raise ValueError("非允许的官方数据地址")
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=45) as r:
        return r.read()


def verify_hash(path, expected):
    if not re.fullmatch(r"[a-f0-9]{64}", expected) or file_hash(path) != expected:
        raise ValueError("数据包SHA-256与官方发布值不符")


def extract_database(archive, destination):
    """Extract only a single SQLite member; never trust an archive pathname."""
    if Path(archive).suffix == ".7z":
        import py7zr
        with py7zr.SevenZipFile(archive, "r") as z:
            members = [m for m in z.list() if m.filename.lower().endswith((".db", ".sqlite", ".sqlite3"))]
            if len(members) != 1 or members[0].uncompressed > 8 * 1024 ** 3 or not re.fullmatch(r"[A-Za-z0-9_. -]+", members[0].filename):
                raise ValueError("7z数据库成员缺失或路径/大小不安全")
            with tempfile.TemporaryDirectory(dir=Path(destination).parent) as tmp:
                z.extract(path=tmp, targets=[members[0].filename])
                shutil.copyfile(Path(tmp) / members[0].filename, destination)
    else:
        with zipfile.ZipFile(archive) as z:
            members = [m for m in z.infolist() if m.filename.lower().endswith((".db", ".sqlite", ".sqlite3"))]
            if len(members) != 1 or members[0].file_size > 8 * 1024 ** 3:
                raise ValueError("数据包必须有且仅有一个有界SQLite文件")
            with z.open(members[0]) as source, Path(destination).open("xb") as target:
                shutil.copyfileobj(source, target, length=2 ** 20)
    with Path(destination).open("rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            raise ValueError("解压文件不是SQLite")


def acquire_cbdb(output, version=None):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    receipt = root / "release.json"
    if receipt.exists():
        saved = json_read(receipt)
        if version is not None and saved['version'] != version:
            raise ValueError('缓存版本与请求的固定版本不符')
        verify_hash(root / saved.get("archive_filename", "cbdb.zip"), saved["archive_sha256"])
        verify_hash(root / "cbdb.sqlite3", saved["sqlite_sha256"])
        return saved
    urls = {"latest.json": LATEST,
            "repository_license.md": "https://raw.githubusercontent.com/cbdb-project/cbdb_sqlite/master/LICENSE.md",
            "repository_readme.md": "https://raw.githubusercontent.com/cbdb-project/cbdb_sqlite/master/README.md"}
    for name, url in urls.items():
        path = root / name
        if not path.exists():
            path.write_bytes(fetch(url))
    latest = json_read(root / "latest.json")
    # Source-level licensing review is separate from per-record admission.
    # Harvard permits the web reader but returns 403 to urllib in this session;
    # do not evade that restriction. Use the explicitly recorded official-page
    # review and pin its hash. If absent/invalid, fail closed.
    licensing = json_read(root / "license_review.json")
    if licensing.get("license_url") != LICENSE or licensing.get("standalone_url") != STANDALONE or licensing.get("license_id") != "CC-BY-NC-SA-4.0" or not licensing.get("noncommercial_public_derivatives_allowed"):
        raise ValueError("无法确定官方许可范围，停止下载与准入")
    cutoff, max_people = licensing["standalone_date"], licensing["max_people"]
    hf = json.loads(fetch(HF_API))
    revision = hf["sha"]
    (root / "hf_metadata.json").write_text(json.dumps(hf, ensure_ascii=False, indent=2), encoding="utf-8")
    # Choose the newest ZIP in the release month or preceding month which is
    # within the dated standalone boundary. Do not assume latest is licensed.
    months = [cutoff[:6]]
    year, month = int(cutoff[:4]), int(cutoff[4:6])
    months.append(f"{year if month > 1 else year - 1}{month - 1 if month > 1 else 12:02d}")
    candidates = []
    for period in months:
        url = HF_API + f"/tree/{revision}/history/cbdb_{period}?limit=100"
        listing = json.loads(fetch(url))
        json_write(root / f"index_{period}.json", listing)
        for item in listing:
            date = re.search(r"cbdb_(\d{8})\.zip$", item["path"])
            if date and date[1] <= cutoff and item.get("lfs", {}).get("oid"):
                candidates.append((date[1], item))
    if version is not None:
        if not re.fullmatch(r"\d{8}", version) or version > cutoff:
            raise ValueError("指定版本超出许可日期范围")
        listing = json.loads(fetch(HF_API + f"/tree/{revision}/history/CBDB_{version}?limit=100"))
        json_write(root / "historical_index.json", listing)
        candidates = [(version, item) for item in listing if item["path"].endswith(f"/CBDB_{version}.7z") and item.get("lfs", {}).get("oid")]
    if not candidates:
        raise ValueError("许可日期内没有可验证ZIP；不回退到受限最新数据")
    date, selected = max(candidates, key=lambda pair: pair[0])
    archive = root / ("cbdb.7z" if selected["path"].endswith(".7z") else "cbdb.zip")
    expected = selected["lfs"]["oid"]
    url = f"https://huggingface.co/datasets/cbdb/cbdb-sqlite/resolve/{revision}/{selected['path']}"
    if not archive.exists():
        partial = archive.with_suffix(archive.suffix + ".part")
        # Range continuation is accepted only when the server actually returns
        # 206; otherwise restart the partial file. Final hash is mandatory.
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": USER_AGENT}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        with urlopen(Request(url, headers=headers), timeout=60) as r:
            append = offset > 0 and r.status == 206
            with partial.open("ab" if append else "wb") as f:
                shutil.copyfileobj(r, f, length=2 ** 20)
        verify_hash(partial, expected)
        partial.rename(archive)
    verify_hash(archive, expected)
    db = root / "cbdb.sqlite3"
    if not db.exists():
        partial_db = root / "cbdb.sqlite3.part"
        if partial_db.exists():
            partial_db.unlink()
        extract_database(archive, partial_db)
        partial_db.rename(db)
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as c:
        people_count = c.execute("SELECT count(*) FROM BIOG_MAIN WHERE c_personid>0").fetchone()[0]
    saved = {"provider": "CBDB", "version": date, "retrieved_at": now(), "latest_metadata": latest,
             "selected_archive_path": selected["path"], "download_url": url, "hf_revision": revision,
             "archive_sha256": expected, "archive_bytes": archive.stat().st_size,
             "archive_filename": archive.name, "people_count": people_count,
             "license_eligible": people_count <= max_people,
             "sqlite_sha256": file_hash(db), "sqlite_bytes": db.stat().st_size,
             "license_id": "CC-BY-NC-SA-4.0", "license_url": LICENSE,
             "license_scope_date": cutoff, "license_scope_max_people": max_people,
             "license_snapshot_sha256": file_hash(root / "license_review.json"),
             "scope": "non-commercial course use; attribution and share-alike for derived CBDB data",
             "sqlite_hash_basis": "local digest; bytes authenticated through official HF LFS ZIP hash"}
    json_write(receipt, saved)
    return saved
