"""Pure, conservative reading views over existing catalog records.

The input dictionaries remain intact for existing callers. Only ``presentation``
is intended for the reading UI; raw payloads belong in the separate technical API.
This module performs no I/O, admission, identity matching, or historical inference.
"""

import json
import re
from datetime import date


FIELD_LABELS = {
    "canonical_name": "姓名", "c_name_chn": "姓名", "dynasty": "朝代",
    "birth_year": "生年", "c_birthyear": "生年", "death_year": "卒年",
    "c_deathyear": "卒年", "native_place": "籍贯",
    "identity_description": "人物简介", "c_surname_chn": "姓",
    "c_mingzi_chn": "名", "c_alt_name_chn": "称呼", "alias_name": "称呼",
    "address_name": "地址", "address_type": "地址性质", "c_pages": "页码",
    "c_title_chn": "书目", "volume": "卷次", "chapter": "篇目",
}
ALIAS_LABELS = {"name": "姓名", "courtesy": "字", "art": "号",
                "posthumous": "谥号", "other": "其他称呼", "unknown": "类别待考"}
# Same documented, checked code set used by the existing CBDB importer. Other
# codes (dynasty, ethnicity, reign era, address index, etc.) are never guessed.
ALIAS_CODES = {3: "别名、曾用名", 4: "字", 5: "室名、別號", 6: "諡號",
               7: "行第", 8: "封爵", 9: "小名", 10: "小字", 11: "賜號",
               13: "俗名", 14: "廟號", 15: "尊號", 19: "法號",
               20: "道號", 21: "洗名/教名"}
TABLE_LABELS = {"BIOG_MAIN": "人物基本资料", "ALTNAME_DATA": "别称记录",
                "BIOG_ADDR_DATA": "人物地址记录", "BIOG_ADDR_CODES": "地址性质",
                "ADDR_CODES": "地址资料", "author_page": "作者资料页"}
KIND_LABELS = {"classical_text": "古籍原文", "structured_database": "结构化数据库",
               "auxiliary": "辅助资料", "unknown": "来源类型待确认"}
RANGES = {-1: "之前", 1: "之后", 2: "约", 300: "元豐五年前", 301: "元豐五年後"}
EMPTY = {"", "0", "0000", "null", "none", "n/a", "unknown", "未知",
         "未詳", "不詳", "未详", "不详", "暂无已核实信息"}
CONTAINERS = ("source_record", "record_json", "person", "raw", "row", "fields",
              "key", "record_key", "rows", "record", "locator_json", "locator_context")


def _decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            pass
    return value


def _dict(value):
    value = _decoded(value)
    return dict(value) if hasattr(value, "keys") else {}


def _text(value):
    """Allow prose/scalars, never serialized containers or internal file paths."""
    if isinstance(value, (dict, list, tuple, set, bool)) or value is None:
        return ""
    value = str(value).strip()
    if value.lower() in EMPTY or isinstance(_decoded(value), (dict, list)):
        return ""
    if value.startswith(("{", "[")):
        return ""
    if re.search(r"(?:[A-Za-z]:[\\/]|(?<![A-Za-z0-9_])(?:raw|work|data|outputs|text)[\\/]|file://|/(?:tmp|home|Users|workspace|mnt)/)", value):
        return ""
    return "".join(ch for ch in value if ord(ch) >= 32 or ch in "\n\t")


def readable_text(value):
    """Public prose filter for legacy context/remarks, without JSON or local paths.

    This does not HTML-escape prose; template autoescaping remains necessary.
    """
    return _text(value)


def _nodes(value, depth=0):
    """Walk known payload wrappers only, so unrelated technical metadata stays out."""
    value = _decoded(value)
    if depth > 8:
        return []
    if isinstance(value, list):
        return [node for child in value[:100] for node in _nodes(child, depth + 1)]
    if not isinstance(value, dict):
        return []
    result = [value]
    for key in CONTAINERS:
        if key in value:
            result.extend(_nodes(value[key], depth + 1))
    return result


def _get(nodes, *keys):
    for key in keys:
        for node in nodes:
            value = node.get(key)
            if _text(value):
                return value
    return None


def _add(items, label, value):
    value = _text(value)
    item = {"label": label, "value": value}
    if value and item not in items:
        items.append(item)


def _year(value, qualifier=None, compact=False):
    value = _decoded(value)
    if isinstance(value, bool):
        return ""
    try:
        year = int(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if str(value).strip() != str(year) or year == 0:
        return ""
    if isinstance(qualifier, (int, float)) or (isinstance(qualifier, str) and re.fullmatch(r"-?\d+", qualifier)):
        if isinstance(qualifier, bool) or float(qualifier) != int(qualifier) or int(qualifier) not in (0, *RANGES):
            return ""
        qualifier = RANGES.get(int(qualifier), "")
    qualifier = _text(qualifier)
    label = (f"前{abs(year)}" if year < 0 else str(year)) if compact else (
        f"公元前{abs(year)}年" if year < 0 else f"公元{year}年")
    return f"约{label}" if qualifier == "约" else label + qualifier


def _version(value):
    value = _text(value)
    match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", value)
    if match:
        try:
            parsed = date(*map(int, match.groups()))
            return f"{value}（{parsed.year}年{parsed.month}月{parsed.day}日）"
        except ValueError:
            # A release identifier need not be a valid calendar date.
            return value
    match = re.fullmatch(r"(?:Wikisource\s+)?oldid=(\d+)", value)
    if match:
        return "修订号 " + match[1]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", value):
        try:
            parsed = date.fromisoformat(value[:10])
            return f"{parsed.year}年{parsed.month}月{parsed.day}日"
        except ValueError:
            return ""
    return value


def _payload(item):
    for key in ("source_record", "record_json", "original_text"):
        value = _decoded(item.get(key))
        if isinstance(value, (dict, list)):
            return value
    return {}


def _kind(item, payload, notes):
    nodes = _nodes(payload) + _nodes(item.get("locator_context"))
    tables = {node.get("table") for node in nodes if isinstance(node.get("table"), str)}
    title = _text(item.get("book_title") or item.get("title"))
    grade = item.get("grade") or item.get("quality_grade") or notes.get("quality_grade")
    if grade == "C" or "author_page" in tables or "作者资料" in title or "作者頁" in title:
        return "auxiliary"
    if item.get("source_kind") in KIND_LABELS:
        return item["source_kind"]
    declared_kinds = set(item.get("evidence_kinds") or [])
    if declared_kinds:
        if len(declared_kinds) == 1 and next(iter(declared_kinds)) in KIND_LABELS:
            return next(iter(declared_kinds))
        return "unknown"
    grades = set(item.get("quality_grades") or [])
    if grades == {"C"}:
        return "auxiliary"
    if (tables & {"BIOG_MAIN", "ALTNAME_DATA", "BIOG_ADDR_DATA"}
            or "CBDB" in title.upper() or "CBDB" in _text(item.get("platform")).upper()
            or _get(nodes, "provider") == "CBDB"):
        return "structured_database"
    declared = item.get("evidence_kind") or notes.get("evidence_kind")
    if declared in {"structured_record", "structured_database"} or grade == "B":
        return "unknown"
    raw_text = item.get("original_text")
    if isinstance(raw_text, str) and raw_text.lstrip().startswith(("{", "[")):
        # A familiar book title cannot turn an unfamiliar/malformed payload into
        # a primary-source quotation.
        return "unknown"
    quote = _text(raw_text) or _text(_get(_nodes(payload), "original_text"))
    if quote and (declared == "classical_text" or grade == "A" or
                  (title and (_text(item.get("volume")) or _text(item.get("chapter"))))):
        return "classical_text"
    # A located book source can be classified without pretending it has a quote.
    if not payload and title and (_text(item.get("volume")) or _text(item.get("chapter"))):
        return "classical_text"
    return "unknown"


def _bibliography(payload):
    result = []
    for node in _nodes(payload):
        value = _decoded(node.get("bibliography"))
        refs = value if isinstance(value, list) else [value]
        for ref in refs:
            for row in _nodes(ref):
                _add(result, "书目", row.get("c_title_chn") or row.get("book_title") or row.get("title"))
                _add(result, "数据库书目编号", row.get("c_textid"))
                _add(result, "数据库所记页码", row.get("c_pages") or row.get("pages"))
    return result


def _locator(item, payload, notes):
    result = []
    nodes = (_nodes(item.get("locator_json")) + _nodes(item.get("locator_context"))
             + _nodes(item.get("record_key")) + _nodes(notes.get("record_key"))
             + _nodes(notes.get("locator")) + _nodes(payload))
    table = _get(nodes, "table") or item.get("chapter")
    if isinstance(table, str):
        labels = [TABLE_LABELS[t.strip()] for t in table.split("/") if t.strip() in TABLE_LABELS]
        _add(result, "记录类别", "、".join(dict.fromkeys(labels)))
    _add(result, "数据库人物编号", _get(nodes, "c_personid"))
    _add(result, "数据库地址编号", _get(nodes, "c_addr_id"))
    column = _get(nodes, "column")
    if column in FIELD_LABELS:
        _add(result, "定位字段", FIELD_LABELS[column])
    volume = item.get("volume") or _get(nodes, "volume")
    is_version = bool(re.fullmatch(r"\d{8}", _text(volume)) or "oldid=" in _text(volume))
    _add(result, "卷次", volume if not TABLE_LABELS.get(item.get("chapter")) and not is_version else None)
    # Database version numbers are not book volumes.
    if any(entry["label"] == "数据库人物编号" for entry in result) or "CBDB" in _text(item.get("book_title")).upper():
        result = [entry for entry in result if entry["label"] != "卷次"]
    chapter = _text(item.get("chapter") or _get(nodes, "chapter"))
    if chapter and not any(token in chapter for token in TABLE_LABELS):
        _add(result, "篇目", chapter)
    version = item.get("source_version") or notes.get("source_version") or (volume if is_version else None)
    if version:
        _add(result, "来源版本", _version(version))
    _add(result, "页面修订号", _get(nodes, "revision_id"))
    for node in nodes:
        start, end = node.get("line_start"), node.get("line_end")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end:
            _add(result, "正文行号", f"第{start}—{end}行")
        start, end = node.get("text_start"), node.get("text_end")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end:
            _add(result, "正文字符位置", f"{start}—{end}（从零计，末位不含）")
    return result


def _record_facts(payload):
    nodes = _nodes(payload)
    result = []
    table = _get(nodes, "table")
    # Addresses have their own name, never infer native place from index codes.
    if table == "BIOG_ADDR_DATA":
        for node in nodes:
            _add(result, "地址", node.get("address_name"))
            _add(result, "地址性质", node.get("address_type") or {1: "籍贯（基本地址）", 7: "本贯"}.get(node.get("c_addr_type")))
        return result
    _add(result, "姓名", _get(nodes, "c_name_chn", "canonical_name"))
    _add(result, "朝代", _get(nodes, "dynasty", "c_dynasty_chn"))
    for column, range_column in (("c_birthyear", "c_by_range"), ("c_deathyear", "c_dy_range")):
        value = _get(nodes, column)
        if value:
            _add(result, FIELD_LABELS[column], _year(value, next(
                (node[range_column] for node in nodes if node.get(column) == value and range_column in node), None)))
    alias = _get(nodes, "c_alt_name_chn", "alias_name", "alias")
    if alias:
        code = _get(nodes, "c_alt_name_type_code")
        try:
            is_integer = isinstance(code, int) and not isinstance(code, bool)
            is_integer_text = isinstance(code, str) and re.fullmatch(r"\d+", code)
            label = ALIAS_CODES.get(int(code)) if is_integer or is_integer_text else None
        except (TypeError, ValueError):
            label = None
        # Explicit source label remains readable even when no mapped code exists.
        explicit = _text(_get(nodes, "type_label", "c_name_type_desc_chn"))
        label = label or (explicit if re.search(r"[\u3400-\u9fff]", explicit) else "称呼")
        _add(result, label, alias)
    _add(result, "数据库所记页码", _get(nodes, "c_pages"))
    return result


def _base(item):
    payload = _payload(item)
    notes = _dict(item.get("raw_notes") or item.get("notes"))
    kind = _kind(item, payload, notes)
    view = {"title": _text(item.get("book_title") or item.get("title")),
            "kind": kind, "kind_label": KIND_LABELS[kind], "summary": "",
            "facts": _record_facts(payload), "locator": _locator(item, payload, notes),
            "bibliography": _bibliography(payload), "original_text": "", "notice": ""}
    view["version"] = next((entry["value"] for entry in view["locator"]
                            if entry["label"] == "来源版本"), "")
    if kind == "structured_database":
        view["summary"] = "数据库记录的资料；书目是数据库所引来源。"
        view["notice"] = "此处展示结构化记录，尚未据此取得古籍原句；书目记录不等同于原典核验。"
    elif kind == "auxiliary":
        view["summary"] = "作者资料页等辅助来源的记载。"
        view["notice"] = "辅助资料不作为古籍原文，也不代表 CBDB 数据库记录。"
        text = _text(_get(_nodes(payload), "text", "record"))
        if text:
            _add(view["facts"], "资料记载", text)
    elif kind == "classical_text":
        view["original_text"] = _text(item.get("original_text")) or _text(_get(_nodes(payload), "original_text"))
        view["summary"] = "有卷篇定位的古籍记载。"
    else:
        view["summary"] = "现有记录尚不能转换为确定的来源说明。"
        view["notice"] = "来源类型或记录结构待确认；不据此推断人物事实。"
        declared = item.get("evidence_kind") or notes.get("evidence_kind")
        if declared in {"structured_record", "structured_database"}:
            view["kind_label"] = "结构化来源"
            view["summary"] = "来源标记为结构化记录，具体记录结构尚待确认。"
            view["notice"] = "结构化来源尚不能转换为确定的资料说明；不据此推断人物事实，也不展示为古籍原文。"
    return view


def field_view(fact):
    """Copy one fact and attach a readable value and its source card."""
    result = dict(fact)
    view = _base(result)
    field = result.get("field")
    label = FIELD_LABELS.get(field, "资料项")
    value = result.get("value", _decoded(result.get("value_json")))
    value = (_year(value, result.get("qualifier")) if field in
             {"birth_year", "death_year", "c_birthyear", "c_deathyear"} else _text(value))
    visible = bool(value and field in FIELD_LABELS)
    view.update(label=label, value=value if visible else "", visible=visible)
    # A field card already presents its value in the heading. Its payload may
    # contain the entire person's BIOG_MAIN row, which is not context for every
    # individual field. Keep only genuinely complementary, relevant explanation.
    relevant = {
        "native_place": {"地址性质"},
        "identity_description": {"资料记载"},
    }.get(field, {label})
    view["facts"] = [entry for entry in view["facts"]
                     if visible and entry["label"] in relevant and entry["value"] != value]
    if visible:
        view["summary"] = f"{label}：{value}"
    else:
        view["summary"] = "此资料项尚无可可靠转换的展示值。"
    result["presentation"] = view
    return result


def evidence_view(evidence, alias=None, person=None):
    """Copy an evidence; prose and database payloads have distinct reading views."""
    result = dict(evidence)
    view = _base(result)
    alias, person = _dict(alias), _dict(person)
    if view["kind"] == "structured_database":
        name = _text(person.get("canonical_name"))
        alias_name = _text(alias.get("alias_name"))
        alias_label = ALIAS_LABELS.get(alias.get("alias_type"), "称呼")
        # Only describe the recorded relation; never manufacture a self-naming claim.
        if name and alias_name:
            if result.get("usage_relation") == "supports":
                if alias.get("alias_type") == "name" and name == alias_name:
                    view["summary"] = f"数据库以“{name}”为姓名收录此人。"
                else:
                    view["summary"] = f"数据库记载“{alias_name}”为{name}的{alias_label}。"
            else:
                view["summary"] = f"这条数据库记录涉及{name}与称呼“{alias_name}”。"
            name_labels = {"姓名", "关联人物"}
            alias_labels = {"称呼", alias_label, *ALIAS_CODES.values()}
            view["facts"] = [entry for entry in view["facts"] if not (
                (entry["label"] in name_labels and entry["value"] == name)
                or (entry["label"] in alias_labels and entry["value"] == alias_name))]
        elif view["facts"]:
            view["summary"] = "；".join(f"{entry['label']}：{entry['value']}" for entry in view["facts"][:4])
    result["presentation"] = view
    return result


def source_view(source):
    """Copy a source with optional record_json/source_record and locator_context."""
    result = dict(source)
    view = _base(result)
    _add(view["facts"], "收录平台", result.get("platform"))
    edition = _text(result.get("edition"))
    if re.fullmatch(r"\d{8}", edition):
        edition = _version(edition)
    else:
        edition = re.sub(r"(?<!\d)(\d{8})(?!\d)", lambda match: _version(match[1]), _version(edition))
    _add(view["facts"], "版本说明", edition)
    result["presentation"] = view
    return result


def person_view(person):
    """Copy a person, retaining identities even when dates are absent."""
    result = dict(person)
    facts = []
    name = _text(result.get("canonical_name"))
    for field in ("canonical_name", "dynasty", "native_place"):
        _add(facts, FIELD_LABELS[field], result.get(field))
    dates = []
    for field in ("birth_year", "death_year"):
        selected = next((entry for entry in (result.get("field_sources") or {}).get(field, []) if entry.get("selected")), {})
        qualifier = selected.get("qualifier")
        value = _year(result.get(field), qualifier)
        _add(facts, FIELD_LABELS[field], value)
        dates.append(_year(result.get(field), qualifier, compact=True))
    lifespan = "—".join([dates[0] or "生年待考", dates[1] or "卒年待考"]) if any(dates) else "生卒待考"
    aliases = []
    for alias in result.get("aliases", result.get("structured_aliases", [])) or []:
        if alias.get("alias_type") in {"courtesy", "art"}:
            _add(aliases, ALIAS_LABELS[alias["alias_type"]], alias.get("alias_name"))
    description = _text(result.get("identity_description"))
    # Existing generated provenance strings are metadata, not biographies.
    if re.search(r"CBDB人物ID|结构化来源核准|非古籍原文核验", description):
        description = ""
    visible_labels = {entry["label"] for entry in facts}
    missing_fields = [label for label in ("朝代", "籍贯", "生年", "卒年")
                      if label not in visible_labels]
    sources = []
    source_keys = set()
    for entries in (result.get("field_sources") or {}).values():
        for entry in entries:
            if entry.get("selected") not in (1, True, "1"):
                continue
            source = _base(entry)
            source_id = _text(entry.get("source_id"))
            source_key = (source_id, source["title"], source["version"])
            if not source_id or not source["title"] or source_key in source_keys:
                continue
            source_keys.add(source_key)
            sources.append({"title": source["title"], "version": source["version"],
                            "kind_label": source["kind_label"], "source_id": source_id})
    result["presentation"] = {"name": name, "description": description, "summary": description or " · ".join(
        value for value in (_text(result.get("dynasty")), lifespan) if value),
        "facts": facts, "lifespan": lifespan, "primary_aliases": aliases,
        "missing_fields": missing_fields, "sources": sources[:3]}
    return result
