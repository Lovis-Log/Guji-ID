"""Small, bounded Wikisource collector. No model-generated evidence; no DB writes.

Raw API responses and their derived text are retained. Rules deliberately produce
review candidates, not historical truth. Identity is always the supplied Person ID.
"""

import csv
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from app.normalization import normalize_name

API = "https://zh.wikisource.org/w/api.php"
USER_AGENT = "GujiIDCourseCollector/0.1 (educational alias evidence collection; human review)"
TZ = timezone(timedelta(hours=8))
SEED_FIELDS = ("person_id", "person_name", "query_name", "name_variants", "dynasty", "identity_hint")
PEOPLE_FIELDS = ("person_id", "canonical_name", "dynasty", "birth_year", "death_year", "native_place", "identity_description", "verification_status", "review_status", "reviewer", "reviewed_at", "discovery_url", "raw_path", "remarks")
ALIAS_FIELDS = ("alias_id", "person_id", "person_name", "dynasty", "alias_name", "alias_type", "normalized_name", "verification_status", "review_status", "reviewer", "reviewed_at", "discovery_url", "discovery_text", "discovery_raw_path", "evidence_count", "remarks")
EVIDENCE_FIELDS = ("evidence_id", "alias_id", "person_id", "person_name", "dynasty", "alias_name", "alias_type", "source_id", "book_title", "volume", "chapter", "original_text", "context", "source_url", "source_platform", "relation_type", "evidence_type", "usage_relation", "evidence_nature", "assessment", "retrieval_date", "verification_status", "review_status", "reviewer", "reviewed_at", "remarks", "revision_id", "raw_path", "raw_sha256", "text_path", "text_sha256", "start_offset", "end_offset")
LOG_FIELDS = ("time", "person_id", "operation", "url", "status", "http_status", "raw_path", "message")


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode("utf-8")).hexdigest()


def identifier(prefix, *parts):
    return prefix + "-" + digest(json.dumps(parts, ensure_ascii=False))[:24]


def write_csv(path, fields, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"CSV 表头缺失或重复：{path}")
        rows = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise ValueError(f"CSV 列数错误：{path}")
        return rows


def load_seeds(path):
    seeds = read_csv(path)
    seen = set()
    if not 1 <= len(seeds) <= 10:
        raise ValueError("每批种子人数必须在 1–10 之间；扩充前先人工检查上批质量")
    for row in seeds:
        if set(row) != set(SEED_FIELDS) or any(not row[k].strip() for k in ("person_id", "person_name", "query_name", "name_variants")):
            raise ValueError("种子表头/必填字段无效")
        if row["person_id"] in seen:
            raise ValueError("种子 Person ID 重复；同名异人请使用不同 ID")
        seen.add(row["person_id"])
        if any(":" in n or "/" in n for n in row["name_variants"].split("|")):
            raise ValueError("人物名不能带命名空间或路径")
    return seeds


class TextParser(HTMLParser):
    """Keep paragraph boundaries; remove navigation, editorial notes and styles."""

    VOID = {"br", "hr", "img", "meta", "link", "input", "wbr", "source"}
    BLOCK = {"p", "div", "li", "dd", "dt", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
    SKIP = {"noprint", "mw-editsection", "navbox", "header", "toc", "licenseContainer", "reference", "references", "sistersitebox", "wst-header", "ws-noexport"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        skip = any(s for _, s in self.stack) or tag in {"script", "style", "sup", "figure"} or bool(set(attrs.get("class", "").split()) & self.SKIP) or attrs.get("id") in {"plainSister", "headerContainer"}
        if not skip and tag in self.BLOCK:
            self.parts.append("\n")
        if tag not in self.VOID:
            self.stack.append((tag, skip))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                skip = self.stack[i][1]
                del self.stack[i:]
                if not skip and tag in self.BLOCK:
                    self.parts.append("\n")
                break

    def handle_data(self, data):
        if not any(s for _, s in self.stack):
            self.parts.append(data)

    def text(self):
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())


def plain(html):
    parser = TextParser()
    parser.feed(html)
    return parser.text()


class NoRedirect(HTTPRedirectHandler):
    # Never follow a challenge or redirect to another host/transport implicitly.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class FetchFailure(ValueError):
    pass


class Client:
    def __init__(self, output, *, replay=None, timeout=15, interval=2.0, max_requests=100, resume=False):
        self.output = Path(output)
        self.replay = Path(replay) if replay else None
        self.timeout, self.interval, self.max_requests = timeout, interval, max_requests
        self.logs, self.cache, self.blocked = [], {}, set()
        self.requests, self.last_request = 0, 0.0
        self.opener = build_opener(NoRedirect())
        (self.output / "raw").mkdir(parents=True, exist_ok=resume)
        (self.output / "text").mkdir(exist_ok=resume)

    def log(self, operation, url, status, message="", person_id="", http_status="", raw_path=""):
        self.logs.append(dict(zip(LOG_FIELDS, (now(), person_id, operation, url, status, http_status, raw_path, message))))

    def fetch(self, url, operation, person_id=""):
        if url in self.cache:
            return self.cache[url]
        host = urlsplit(url).hostname
        if host in self.blocked:
            self.log(operation, url, "skipped_host", "本批来源已受限/网络失败，不再请求", person_id)
            raise FetchFailure("来源已停止访问")
        key = digest(url)
        body_path = Path("raw") / (key + ".body")
        meta_path = Path("raw") / (key + ".json")
        status, body, error = 0, b"", ""
        if self.replay:
            try:
                meta = json.loads((self.replay / meta_path).read_text(encoding="utf-8"))
                body = (self.replay / body_path).read_bytes()
                if meta["request_url"] != url or digest(body) != meta["sha256"]:
                    raise ValueError("缓存哈希/URL不一致")
                status, error = meta["http_status"], meta["error"]
            except (OSError, ValueError, KeyError) as exc:
                self.log(operation, url, "replay_missing", str(exc), person_id)
                raise FetchFailure("离线缓存缺失/无效，不会联网补抓") from exc
        else:
            if self.requests >= self.max_requests:
                self.log(operation, url, "budget_exhausted", "已达本批请求上限", person_id)
                raise FetchFailure("请求预算耗尽")
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.requests += 1
            self.last_request = time.monotonic()
            headers = {}
            try:
                with self.opener.open(Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,text/html"}), timeout=self.timeout) as response:
                    status = response.status
                    headers = {k: response.headers.get(k, "") for k in ("Content-Type", "ETag", "Last-Modified", "Retry-After", "Date")}
                    body = response.read(8_000_001)
                    if len(body) > 8_000_000:
                        error = "响应超过 8 MB，本批停止此来源；不作为证据"
            except HTTPError as exc:
                status, body, error = exc.code, exc.read(65536), f"HTTP {exc.code}"
                headers = {k: exc.headers.get(k, "") for k in ("Content-Type", "Retry-After", "Date")} if exc.headers else {}
            except (URLError, TimeoutError, OSError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            meta = {"request_url": url, "retrieved_at": now(), "http_status": status, "headers": headers, "sha256": digest(body), "error": error}
        # In-place replay has already checked the stored URL and byte hash.
        # Keep that archive read-only; replay to a different output still
        # materializes a separate working copy for the collection pipeline.
        if not self.replay or self.output.resolve() != self.replay.resolve():
            (self.output / body_path).write_bytes(body)
            (self.output / meta_path).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        decoded = body.decode("utf-8", errors="replace")
        challenge = any(x in decoded.lower() for x in ("checking the security of your connection", "cf-chl-", "g-recaptcha", "you do not have authorization to scrape"))
        if error or status != 200 or challenge:
            self.log(operation, url, "restricted" if challenge or status in {401, 403, 429} else "failed", error or "安全检查页，非古籍内容", person_id, status, body_path.as_posix())
            # HTTP 404 is a missing document, not a host-wide outage.
            if status != 404 and not self.replay:
                self.blocked.add(host)
            raise FetchFailure(error or "来源访问受限")
        result = (decoded, body_path.as_posix(), meta)
        self.cache[url] = result
        self.log(operation, url, "replayed" if self.replay else "fetched", "", person_id, status, body_path.as_posix())
        return result

    def api(self, params, person_id=""):
        url = API + "?" + urlencode({"format": "json", "maxlag": 5, **params})
        if self.replay and params["action"] == "parse" and not (self.replay / "raw" / (digest(url) + ".json")).exists():
            # Use already fetched API pages even if the earlier probe requested a
            # smaller prop set. Keep the original request URL, never invent one.
            for path in sorted((self.replay / "raw").glob("*.json")):
                meta = json.loads(path.read_text(encoding="utf-8"))
                actual = meta.get("request_url", "")
                query = parse_qs(urlsplit(actual).query)
                if actual.startswith(API + "?") and query.get("action") == ["parse"] and query.get("page") == [params["page"]] and meta.get("http_status") == 200:
                    url = actual
                    break
        decoded, raw, meta = self.fetch(url, params["action"], person_id)
        try:
            data = json.loads(decoded)
            if "error" in data:
                code = data["error"].get("code", "unknown")
                self.log("api_error", url, "failed", json.dumps(data["error"], ensure_ascii=False), person_id, raw_path=raw)
                if code in {"maxlag", "ratelimited", "readonly"} and not self.replay:
                    self.blocked.add(urlsplit(API).hostname)
                raise FetchFailure(f"API: {code}")
            return data, raw, meta
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            self.log("decode", url, "failed", "非预期 JSON；停止该来源", person_id, raw_path=raw)
            self.blocked.add(urlsplit(API).hostname)
            raise FetchFailure("API 响应格式错误") from exc

    def page(self, title, person_id=""):
        data, raw, meta = self.api({"action": "parse", "page": title, "prop": "text|links|revid|sections", "redirects": 1, "disableeditsection": 1}, person_id)
        try:
            parsed = data["parse"]
            text = plain(parsed["text"]["*"])
            revision = parsed["revid"]
            text_path = "text/" + digest(raw) + ".txt"
            if self.replay and self.output.resolve() == self.replay.resolve():
                existing_text = (self.output / text_path).read_text(encoding="utf-8")
                if existing_text != text:
                    raise FetchFailure("离线正文缓存与原始响应不一致")
            else:
                (self.output / text_path).write_text(text, encoding="utf-8", newline="\n")
            sections = parsed.get("sections")
            if sections is None:
                sections = [{"line": plain(m[2]), "level": m[1]} for m in re.finditer(r"<h([2-6])\b[^>]*>(.*?)</h\1>", parsed["text"]["*"], re.S)]
            return {"title": parsed["title"], "text": text, "html": parsed["text"]["*"], "links": parsed.get("links", []), "sections": sections, "revision": revision, "url": f"https://zh.wikisource.org/w/index.php?oldid={revision}", "raw_path": raw, "raw_sha256": meta["sha256"], "retrieved_at": meta["retrieved_at"], "text_path": text_path, "text_sha256": digest(text)}
        except (KeyError, TypeError) as exc:
            self.log("parse", title, "failed", "响应缺少必要字段", person_id, raw_path=raw)
            raise FetchFailure("页面缺少正文或修订号") from exc

    def search(self, query, person_id="", limit=2):
        data, _, _ = self.api({"action": "query", "list": "search", "srsearch": query, "srnamespace": 0, "srlimit": limit}, person_id)
        results = [r["title"] for r in data.get("query", {}).get("search", [])]
        self.log("search_results", query, "found" if results else "no_results", " | ".join(results), person_id)
        return results


def source_checks(client):
    # Official Action API is explicitly documented for programmatic access.
    client.log("source_policy", API, "enabled", "仅使用公开 Action API；串行、User-Agent、maxlag=5、超时、不自动重试。https://www.mediawiki.org/wiki/API:Etiquette")
    try:
        client.fetch("https://ctext.org/tools/api", "source_policy")
        client.log("source_policy", "https://ctext.org", "skipped", "未配置获授权 API；不抓取正文")
    except FetchFailure:
        pass
    try:
        text, _, _ = client.fetch("https://www.shidianguji.com/robots.txt", "robots")
        robots = RobotFileParser()
        robots.parse(text.splitlines())
        allowed = robots.can_fetch(USER_AGENT, "https://www.shidianguji.com/")
        client.log("source_policy", "https://www.shidianguji.com", "skipped", "robots 禁止通用机器人；不请求正文" if not allowed else "尚未确认官方自动接口；不请求正文")
    except FetchFailure:
        pass
    try:
        client.fetch("https://sou-yun.cn/robots.txt", "robots")
    except FetchFailure:
        pass
    client.log("source_policy", "https://sou-yun.cn", "skipped", "未确认适用官方接口及使用许可；robots 不等于授权；列入人工补查")


MARKER = re.compile(r"(?P<marker>一字|字|自號|自号|一號|一号|更號|更号|號|号|自稱|自称|諡曰|谥曰|諡|谥|世稱其|世称其|世稱|世称|外號|外号)[「『\"“]?(?P<names>[\u3400-\u9fff、]{1,24})")


def discover(seed, page):
    # Only the author introduction before the first work-list heading.
    lead_html = re.split(r'<(?:div[^>]*class="mw-heading|h2\b)', page["html"], maxsplit=1)[0]
    lead = plain(lead_html)
    rows = {}

    def add(name, kind, snippet, remarks=""):
        key = (name, kind)
        rows.setdefault(key, {"alias_id": identifier("A", seed["person_id"], name, kind), "person_id": seed["person_id"], "person_name": seed["person_name"], "dynasty": seed["dynasty"], "alias_name": name, "alias_type": kind, "normalized_name": normalize_name(name), "verification_status": "pending", "review_status": "待审核", "reviewer": "", "reviewed_at": "", "discovery_url": page["url"], "discovery_text": snippet, "discovery_raw_path": page["raw_path"], "evidence_count": 0, "remarks": remarks or "作者介绍仅为线索，非古籍证据；身份及别称类别待人工核对"})

    # Names are preserved in the source's script; no automatic simplified merge.
    if seed["query_name"] in lead:
        add(seed["query_name"], "name", lead[:500], "原文姓名与种子姓名的简繁/异体对应待核，不用于合并身份")
    for match in MARKER.finditer(lead):
        marker = match["marker"]
        kind = "courtesy" if "字" in marker else "posthumous" if marker.startswith(("諡", "谥")) else "other" if marker.startswith(("世", "外")) else "art"
        for name in match["names"].split("、"):
            if 1 <= len(name) <= 8 and name not in {"其", "其名", "曰"}:
                add(name, kind, lead[max(0, match.start()-40):match.end()+60])
    return list(rows.values())


def discover_biography(seed, page):
    """Only explicitly marked aliases inside this person's actual biography.

    No group labels are extracted. A subject may still be misread, hence all
    newly discovered records remain pending and carry an explicit warning.
    """
    start, end, _, scope = body_bounds(page, seed)
    if scope != "biography":
        return []
    body = page["text"][start:end]
    matches = []
    for name in seed["name_variants"].split("|"):
        if re.search(re.escape(name) + r"[，,]?字", body):
            matches.append((name, "name", body[:250]))
    for match in re.finditer(r"(?P<marker>自號|自号|自稱|自称|更號|更号|諡曰|谥曰|諡|谥)[「『\"“]?(?P<name>[\u3400-\u9fff]{1,8})[」』\"”，。；]", body):
        kind = "posthumous" if match["marker"].startswith(("諡", "谥")) else "art"
        matches.append((match["name"], kind, body[max(0,match.start()-100):match.end()+100]))
    rows = []
    for name, kind, snippet in matches:
        row = {field: "" for field in ALIAS_FIELDS}
        row.update(alias_id=identifier("A", seed["person_id"], name, kind), person_id=seed["person_id"], person_name=seed["person_name"], dynasty=seed["dynasty"], alias_name=name, alias_type=kind, normalized_name=normalize_name(name), verification_status="pending", review_status="待审核", discovery_url=page["url"], discovery_text=snippet, discovery_raw_path=page["raw_path"], evidence_count=0, remarks="由本人传记分节的明确标记发现；尚需人工核对主语、身份、类别，不能视为历史确认")
        rows.append(row)
    return rows


def body_bounds(page, seed):
    """Biographies are scoped to the named section, never the whole volume."""
    text = page["text"]
    variants = seed["name_variants"].split("|")
    sections = page["sections"]
    own = next((s for s in sections if plain(s.get("line", "")) in variants), None)
    if own:
        name = plain(own["line"])
        found = re.search(r"(?m)^" + re.escape(name) + r"$", text)
        if found:
            start = found.end()
            end = len(text)
            # Conservatively close at the next heading, including child/relative
            # biographies nested below this person (e.g. 子 過 under 蘇軾).
            seen = False
            for section in sections:
                if section is own:
                    seen = True
                elif seen:
                    heading = plain(section.get("line", ""))
                    nxt = re.search(r"(?m)^" + re.escape(heading) + r"$", text[start:])
                    if nxt:
                        end = start + nxt.start()
                    break
            return start, end, name, "biography"
    # Standalone works may have the author only in metadata. That is context,
    # never grounds for silently declaring a name mention an alias relationship.
    return 0, len(text), page["title"], "work"


def identity_anchor(seed, snippet):
    variants = seed["name_variants"].split("|")
    return any(v in snippet or any(re.search(r"諱" + re.escape(v[i:]) + r"[，,]姓" + re.escape(v[:i]) + r"氏", snippet) for i in range(1, len(v))) for v in variants)


def source_location(page):
    book, sep, location = page["title"].partition("/")
    if not sep:
        included = re.search(r"本作品收錄於《(.*?)》", page["html"], re.S)
        if included:
            book, _, location = plain(included[1]).partition("/")
    return book, location if "卷" in location else ""


def assess(seed, alias, snippet, scope):
    name = re.escape(alias["alias_name"])
    variants = "(?:" + "|".join(map(re.escape, seed["name_variants"].split("|"))) + ")"
    if alias["alias_type"] == "name" and re.search(variants + r"[，,]?字", snippet):
        return "explicit_relation", "supports", "direct", "史传姓名记载", "史传记载"
    if alias["alias_type"] == "courtesy" and re.search(variants + r"[，,]?字" + name + r"[，。；、]", snippet):
        return "explicit_relation", "supports", "direct", "他称（史传记载）", "史传记载"
    if alias["alias_type"] == "courtesy" and identity_anchor(seed, snippet) and re.search(r"一?字" + name + r"[，。；、]", snippet):
        return "explicit_relation", "supports", "direct", "他称（姓名与字的明确记载）", "明确关系记载"
    if scope == "biography" and re.search(r"(?:自號|自号|自稱|自称|號曰|号曰|更號|更号)[「『\"“]?" + name, snippet):
        return "contextual_relation", "uncertain", "indirect", "后世文献记载其自称", "史传记载"
    if scope == "biography" and alias["alias_type"] == "posthumous" and re.search(r"(?:諡|谥)(?:曰|為|为)?[「『\"“]?" + name, snippet):
        return "contextual_relation", "uncertain", "indirect", "后世追称（主语待核）", "史传记载"
    return "mention_only", "mentions", "indirect", "未判定；须核对作者、主语与上下文", "作品用例（对应关系待核）"


def extract(seed, aliases, page):
    start, end, chapter, scope = body_bounds(page, seed)
    # Strip textual site metadata preceding a standalone work. For biographies
    # body_bounds already starts at the person's actual heading.
    authored = False
    if scope == "work":
        authors = re.findall(r'(?:title="|href="/wiki/)(?:Author:|作者:)([^"#]+)', page["html"])
        from urllib.parse import unquote
        authored = any(unquote(a) in seed["name_variants"].split("|") for a in authors)
        if not authored and not any(v in page["text"][start:end] for v in seed["name_variants"].split("|")):
            return []
    rows = []
    # A work's title, contents list, and page metadata are not its original prose.
    # Requiring an actual paragraph also avoids passing an index off as evidence.
    prose = "\n".join(plain(m[1]) for m in re.finditer(r"<p\b[^>]*>(.*?)</p>", page["html"], re.S))
    book_title, volume = source_location(page)
    for alias in aliases:
        matches = list(re.finditer(re.escape(alias["alias_name"]), page["text"][start:end]))
        ranked = []
        for match in matches:
            pos = start + match.start()
            # Preserve a verbatim contiguous substring with auditable offsets.
            left = max(start, page["text"].rfind("\n", start, pos) + 1, pos - 100)
            right_break = page["text"].find("\n", pos, end)
            right = min(end, right_break if right_break >= 0 else end, pos + len(alias["alias_name"]) + 130)
            snippet = page["text"][left:right]
            if (not snippet.strip() or snippet.strip() in {alias["alias_name"], page["title"]}
                    or snippet not in prose or "作者：" in snippet or "作者:" in snippet):
                continue
            if scope == "work" and not authored and not identity_anchor(seed, snippet):
                continue
            assessment, usage, nature, relation, evidence_type = assess(seed, alias, snippet, scope)
            rank = {"explicit_relation": 0, "contextual_relation": 1, "mention_only": 2}[assessment]
            ranked.append((rank, left, right, snippet, assessment, usage, nature, relation, evidence_type))
        if not ranked:
            continue
        _, left, right, snippet, assessment, usage, nature, relation, evidence_type = min(ranked)
        row = {field: "" for field in EVIDENCE_FIELDS}
        source_id = identifier("S", page["url"], chapter)
        row.update(evidence_id=identifier("E", alias["alias_id"], source_id, snippet), alias_id=alias["alias_id"], person_id=seed["person_id"], person_name=seed["person_name"], dynasty=seed["dynasty"], alias_name=alias["alias_name"], alias_type=alias["alias_type"], source_id=source_id, book_title=book_title, volume=volume, chapter=chapter, original_text=snippet, context=page["text"][max(start,left-200):min(end,right+200)], source_url=page["url"], source_platform="维基文库", relation_type=relation, evidence_type=evidence_type, usage_relation=usage, evidence_nature=nature, assessment=assessment, retrieval_date=page["retrieved_at"][:10], verification_status="pending", review_status="待审核", remarks="规则提取并非历史确认；核对原文、身份、异体及作品归属；单纯出现不证明对应。" + ("史传所述自称不可当作本人原作自称。" if "记载其自称" in relation else ""), revision_id=page["revision"], raw_path=page["raw_path"], raw_sha256=page["raw_sha256"], text_path=page["text_path"], text_sha256=page["text_sha256"], start_offset=left, end_offset=right)
        rows.append(row)
    return rows


def collect(seeds_path, output, *, replay=None, timeout=15, interval=2.0, max_requests=100, max_pages=10):
    seeds = load_seeds(seeds_path)
    output = Path(output)
    if output.exists():
        raise ValueError("输出目录已存在；请换一个新目录，避免覆盖原始资料和审核决定")
    if not 1 <= max_pages <= 15 or not 1 <= max_requests <= 150 or not 1 <= timeout <= 30 or interval < 0.5:
        raise ValueError("限制：每人 1–15 页、总请求 1–150、超时 1–30 秒、间隔至少 0.5 秒")
    output.mkdir(parents=True)
    client = Client(output, replay=replay, timeout=timeout, interval=interval, max_requests=max_requests)
    people, aliases, evidences, tasks = [], [], {}, []
    source_checks(client)
    write_csv(output / "seeds.csv", SEED_FIELDS, seeds)
    for seed in seeds:
        pid = seed["person_id"]
        print(f"采集 {seed['person_name']} ({pid})", flush=True)
        try:
            author = client.page("Author:" + seed["query_name"], pid)
        except FetchFailure as exc:
            tasks.append(f"{seed['person_name']}：作者页未获取（{exc}）；种子不计为实际发现人物。")
            continue
        person = {field: "" for field in PEOPLE_FIELDS}
        person.update(person_id=pid, canonical_name=seed["person_name"], dynasty=seed["dynasty"], identity_description=seed["identity_hint"], verification_status="pending", review_status="待审核", discovery_url=author["url"], raw_path=author["raw_path"], remarks="人物来自种子与实际作者页的候选对应；朝代仅种子提示，生卒及籍贯留空")
        people.append(person)
        found = discover(seed, author)
        # Discovery must be based on a fetched author page, not a known-alias list.
        aliases.extend(found)
        biographies = [link["*"] for link in author["links"] if link.get("ns") == 0 and re.match(r"(?:宋史|舊唐書|新唐書)/卷", link["*"])]
        works = [link["*"] for link in author["links"] if link.get("ns") == 0 and any(word in link["*"] for word in ("居士傳", "先生傳", "墓誌銘"))]
        try:
            search = client.search('"' + seed["query_name"] + '" "字"', pid)
        except FetchFailure:
            search = []
        titles = list(dict.fromkeys(biographies[:2] + works[:2] + search[:1]))
        visited = set()

        def visit(title):
            if title in visited or len(visited) >= max_pages or ":" in title:
                return
            visited.add(title)
            try:
                page = client.page(title, pid)
                known = {a["alias_id"] for a in found}
                added = []
                for a in discover_biography(seed, page):
                    if a["alias_id"] not in known:
                        added.append(a)
                        known.add(a["alias_id"])
                found.extend(added)
                aliases.extend(added)
                for evidence in extract(seed, found, page):
                    evidences[evidence["evidence_id"]] = evidence
                client.log("extract", page["url"], "processed", f"已分析正文；本人物累计 {sum(e['person_id']==pid for e in evidences.values())} 条证据候选", pid)
            except FetchFailure as exc:
                tasks.append(f"{seed['person_name']} / {title}：{exc}")

        for title in titles:
            visit(title)
        # Seek missing aliases first, then weak mentions; bounded queries/pages.
        ordered = sorted(found, key=lambda a: (any(e["alias_id"] == a["alias_id"] for e in evidences.values()), a["alias_type"] == "name"))
        for alias in ordered[:4]:
            if len(visited) >= max_pages:
                break
            if any(e["alias_id"] == alias["alias_id"] and e["assessment"] == "explicit_relation" for e in evidences.values()):
                continue
            try:
                for title in client.search('"' + alias["alias_name"] + '"', pid, limit=2):
                    visit(title)
            except FetchFailure:
                break
        for alias in found:
            alias["evidence_count"] = sum(e["alias_id"] == alias["alias_id"] for e in evidences.values())
            if not alias["evidence_count"]:
                alias["remarks"] += "；存疑：未取得可定位古籍用例，须人工补查"
                tasks.append(f"{seed['person_name']} / {alias['alias_name']}：只有作者介绍线索，缺古籍证据；从 {alias['discovery_url']} 追查。")
        print(f"  {len(found)} 条称呼候选，{sum(e['person_id']==pid for e in evidences.values())} 条正文用例，访问 {len(visited)} 页", flush=True)
    evidence_rows = list(evidences.values())
    write_csv(output / "candidate_people.csv", PEOPLE_FIELDS, people)
    write_csv(output / "aliases_pending.csv", ALIAS_FIELDS, aliases)
    write_csv(output / "evidence_pending.csv", EVIDENCE_FIELDS, evidence_rows)
    write_csv(output / "collection_log.csv", LOG_FIELDS, client.logs)
    summary = quality_report(output, people, aliases, evidence_rows, client.logs, tasks)
    (output / "run.json").write_text(json.dumps({"finished_at": now(), "mode": "offline_replay" if replay else "live", "network_requests": client.requests, "seed_path": str(Path(seeds_path).resolve()), "limits": {"timeout": timeout, "interval": interval, "max_requests": max_requests, "max_pages_per_person": max_pages}, "summary": summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def quality_report(output, people, aliases, evidences, logs, tasks):
    located = {e["alias_id"] for e in evidences}
    explicit = {e["alias_id"] for e in evidences if e["assessment"] == "explicit_relation"}
    # Verify provenance by re-reading the fetched API body, not just trusting CSV.
    for e in evidences:
        raw = (output / e["raw_path"]).read_bytes()
        obj = json.loads(raw)["parse"]
        text = (output / e["text_path"]).read_text(encoding="utf-8")
        if digest(raw) != e["raw_sha256"] or digest(text) != e["text_sha256"] or plain(obj["text"]["*"]) != text or str(obj["revid"]) != str(e["revision_id"]) or text[int(e["start_offset"]):int(e["end_offset"])] != e["original_text"]:
            raise ValueError(f"溯源检查失败：{e['evidence_id']}")
    by_name = {}
    for a in aliases:
        by_name.setdefault(a["alias_name"], set()).add(a["person_id"])
    collisions = {name: pids for name, pids in by_name.items() if len(pids) > 1}
    summary = {"collection_status": "partial" if len(located) < len(aliases) or len(people) == 0 else "candidates_ready", "candidate_people": len(people), "alias_candidates": len(aliases), "name_form_candidates": sum(a["alias_type"] == "name" for a in aliases), "non_name_alias_candidates": sum(a["alias_type"] != "name" for a in aliases), "aliases_with_located_text": len(located), "aliases_with_explicit_relation": len(explicit), "aliases_without_text": len(aliases)-len(located), "aliases_needing_priority_review": len(aliases)-len(explicit), "evidence_candidates": len(evidences), "human_verified": 0, "shared_names": len(collisions), "provenance_checks": "passed", "failed_or_restricted_requests": sum(l["status"] in {"failed", "restricted"} for l in logs), "offline_cache_misses": sum(l["status"] == "replay_missing" for l in logs)}
    lines = ["# 首轮采集质量报告", "", f"生成时间：{now()}。以下均为待审核候选；人工确认 0 条；正式数据库写入 0 条。", "整理模式：" + ("离线，复用此前实际获取的响应，不联网。" if any(l["status"] == "replayed" for l in logs) else "在线采集。"), "状态：部分成功；缺失资料不补造。" if summary["collection_status"] == "partial" else "状态：候选可供审核。", "", f"实际获取作者页的候选人物：{len(people)} 位（不把全部种子自动计为发现）。", f"不同人物×原始称呼×类别：{len(aliases)} 条；其中姓名类别 {sum(a['alias_type']=='name' for a in aliases)} 条。", f"有可定位古籍文本用例：{len(located)} 条称呼；有规则识别的明确对应表述：{len(explicit)} 条。", f"正文证据候选 {len(evidences)} 条（同一称呼可有多份证据，不按证据数放大别称规模）。", f"缺古籍文本 {len(aliases)-len(located)} 条；需重点审核（缺文本或无明确关系）{len(aliases)-len(explicit)} 条。", f"原始响应/派生正文/哈希/修订号/原文偏移核验：通过 {len(evidences)} 条；这只证明可溯源，不代表历史事实正确。", f"已记录失败/受限响应 {summary['failed_or_restricted_requests']} 次；离线缓存缺失 {summary['offline_cache_misses']} 次，后者不表示发出了网络请求。", "", "## 人物与称呼", "", "|人物|候选称呼|有文本|明确对应|", "|---|---|---:|---:|"]
    for p in people:
        own = [a for a in aliases if a["person_id"] == p["person_id"]]
        lines.append(f"|{p['canonical_name']}|{'、'.join(a['alias_name'] for a in own)}|{sum(a['alias_id'] in located for a in own)}|{sum(a['alias_id'] in explicit for a in own)}|")
    lines.extend(["", "## 同名与共用称呼", ""])
    for name, pids in collisions.items():
        lines.append(f"- “{name}”出现在 {len(pids)} 个独立身份的候选称呼中：{', '.join(sorted(pids))}。共用谥号/别称不等于全名相同，不能合并人物。")
    if not collisions:
        lines.append("本批没有发现可报告的共用称呼；不虚构同名案例。")
    lines.extend(["", "完整姓名同名异人尚未在这五位人物内取得充分证据；可人工扩充独立种子后另批采集。本轮不扩大人物范围。", "", "## 人工重点核对", "", "- 作者页可能存在编辑错误；简繁/异体、时代、身份和作品归属均须人工确认。", "- mention_only 仅表示出现，contextual_relation 主语仍待核；不可直接当作关系证明。", "- 史传记载自称与本人作品自称分开处理；规则不会自动升级到已核对。", "- 下表列出缺乏明确关系的称呼，参见 evidence_pending.csv 的 context、remarks 和原始响应。", ""])
    for a in aliases:
        if a["alias_id"] not in explicit:
            lines.append(f"- {a['person_name']} / {a['alias_name']}：{'缺古籍文本' if a['alias_id'] not in located else '有名称用例，尚无明确对应表述'}（{a['alias_id']}）。")
    lines.extend(["", "## 异常与来源范围", ""])
    for log in logs:
        if log["status"] in {"failed", "restricted", "skipped", "budget_exhausted", "replay_missing"}:
            lines.append(f"- {log['status']}：{log['url']} — {log['message']}")
    lines.extend(["", "## 明确补查清单", ""] + [f"- {t}" for t in tasks] + ["- 中国哲学书电子化计划：若机构有授权 API，再单独配置并评估；本版不支持该接口。", "- 识典古籍：本轮遵守 robots 禁止访问；由人工依网站许可检索。", "- 搜韵：人工从人物页核对别称线索，确认授权接口前本版不抓取正文。", "", "## 审核后导入", "", "在三份候选 CSV 中填写 review_status=通过、reviewer 和带时区 reviewed_at；候选原 verification_status 保持 pending。证据的 usage_relation、evidence_nature 必须由审核人确认；已通过别称需至少一条已通过的 supports/direct 证据。先运行 export_reviewed 生成与旧导入器完全兼容的四表，再运行 import_reviewed 导入；没有审核通过链时不会写入数据库。", "", "原始古籍与维基文库编辑文本需分别判断版本及许可；保留来源、修订号及页面署名/历史入口。维基文库编辑文本参见 https://zh.wikisource.org/wiki/Wikisource:著作权 。", ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary
