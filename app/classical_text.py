"""Deterministic parsing and location helpers for classical source texts.

The functions here never invent characters.  Kanripo transcribes one physical
line per paragraph-like fragment and marks line ends with ``¶``; joining those
marks removes layout only.  Wikisource wikitext is cleaned of templates, links
and presentational markup while paragraph breaks are kept.

Every transformation returns both the readable text and enough metadata for a
later integrity check (source file hash, character offsets, page/paragraph
labels, and a versioned transform name).
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass, field

KANRIPO_TRANSFORM = "kanripo_layout_v1"
WIKISOURCE_TRANSFORM = "wikisource_wikitext_v1"

PAGE_MARK = re.compile(r"<pb:([^>]+)>")
KANRIPO_HEADING = re.compile(r"^(\d+\.\d+)\s*《(.+?)》\s*$", re.M)
WIKI_HEADING = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.M)
SENTENCE_END = "。！？；"
CLOSERS = "」』】）)〕》"


@dataclass
class ChapterDraft:
    volume: str
    title: str
    sequence: int
    text: str
    pages: list[tuple[str, int, int]] = field(default_factory=list)
    paragraphs: list[tuple[str, int, int]] = field(default_factory=list)
    transform: str = KANRIPO_TRANSFORM
    note: str = ""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def normalize_for_search(value: str) -> str:
    """Retrieval-only form: NFC, no layout whitespace, OpenCC t2s when present.

    The original text is never modified; callers store this separately.
    """
    compact = re.sub(r"\s+", "", nfc(value))
    try:
        from opencc import OpenCC
        compact = OpenCC("t2s").convert(compact)
    except Exception:  # OpenCC absence must not break reading
        pass
    return compact


def _strip_kanripo_header(raw: str) -> str:
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines) and (lines[index].startswith("#") or not lines[index].strip()):
        index += 1
    return "\n".join(lines[index:])


def _page_pieces(line: str) -> list[tuple[str | None, str]]:
    """Split a physical line at page marks, preserving text order."""
    pieces: list[tuple[str | None, str]] = []
    position = 0
    current: str | None = None
    for match in PAGE_MARK.finditer(line):
        pieces.append((current, line[position:match.start()]))
        current = match.group(1)
        position = match.end()
    pieces.append((current, line[position:]))
    return pieces


def parse_kanripo(raw: str, *, fallback_title: str, fallback_volume: str,
                  start_sequence: int = 1) -> list[ChapterDraft]:
    """Parse one Kanripo file into one or more complete chapter drafts.

    Files containing several titled chapters (e.g. Shiji's grouped 列傳 files)
    are split at ``5.1《...》`` headings.  Per-juan files yield one draft.
    """
    body = _strip_kanripo_header(raw)
    headings = list(KANRIPO_HEADING.finditer(body))
    sections: list[tuple[str, str, int, str]] = []
    if len(headings) >= 1:
        # A grouped file: everything before the first heading is front matter.
        for index, match in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
            title = match.group(2).strip()
            number = match.group(1)
            try:
                sequence = int(number.split(".")[-1])
            except ValueError:
                sequence = start_sequence + index
            sections.append((title, number, sequence, body[match.end():end]))
    else:
        sections.append((fallback_title, "", start_sequence, body))

    drafts: list[ChapterDraft] = []
    for title, number, sequence, segment in sections:
        page_texts: list[tuple[str, str]] = []
        current_label = ""
        current_parts: list[str] = []
        for line in segment.split("\n"):
            for label, piece in _page_pieces(line):
                if label is not None:
                    if current_parts:
                        page_texts.append((current_label, "".join(current_parts)))
                    current_label = label
                    current_parts = []
                piece = piece.replace("¶", "")
                if piece.strip():
                    current_parts.append(piece)
        if current_parts:
            page_texts.append((current_label, "".join(current_parts)))

        # Drop empty page units but keep the source page label of real units.
        text_parts: list[str] = []
        pages: list[tuple[str, int, int]] = []
        cursor = 0
        for label, page in page_texts:
            page = page.strip()
            if not page:
                continue
            if text_parts:
                cursor += 2  # the "\n\n" separator
            pages.append((label, cursor, cursor + len(page)))
            text_parts.append(page)
            cursor += len(page)
        text = "\n\n".join(text_parts)
        if not text:
            continue
        drafts.append(ChapterDraft(
            volume=fallback_volume,
            title=title or fallback_title,
            sequence=sequence,
            text=text,
            pages=pages,
            paragraphs=[(f"底本页 {label}" if label else "底本页", start, end)
                        for label, start, end in pages],
            transform=KANRIPO_TRANSFORM,
            note="按 Kanripo 转录的底本页合并排版行；未增删字句。",
        ))
    return drafts


def _remove_nested(text: str, opener: str, closer: str) -> str:
    """Remove balanced template/table constructs, tolerating nesting."""
    result: list[str] = []
    depth = 0
    index = 0
    while index < len(text):
        if text.startswith(opener, index):
            depth += 1
            index += len(opener)
            continue
        if text.startswith(closer, index):
            if depth:
                depth -= 1
                index += len(closer)
                continue
        if depth == 0:
            result.append(text[index])
        index += 1
    return "".join(result)


def clean_wikitext(raw: str) -> dict:
    """Convert Wikisource wikitext to readable prose without adding words.

    Templates, references, tables, category/file links and presentational
    markup are removed.  Wikilink labels are kept.  Blank-line paragraph
    boundaries are preserved.
    """
    value = raw.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = re.sub(r"<ref[^>]*/>", "", value, flags=re.I)
    value = re.sub(r"<ref[^>]*>.*?</ref>", "", value, flags=re.S | re.I)
    value = re.sub(r"<noinclude>.*?</noinclude>", "", value, flags=re.S | re.I)
    value = re.sub(r"<includeonly>.*?</includeonly>", "", value, flags=re.S | re.I)
    value = re.sub(r"<pages[^>]*/>", "", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = _remove_nested(value, "{{", "}}")
    value = _remove_nested(value, "{|", "|}")
    value = re.sub(r"\[\[(?:File|Image|檔案|文件|Category|分類):[^\]|]*(\|[^\]]*)?\]\]",
                   "", value, flags=re.I)
    value = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[(?:https?://\S+)\s+([^\]]+)\]", r"\1", value)
    value = re.sub(r"\[(?:https?://\S+)\]", "", value)
    value = re.sub(r"'''?", "", value)
    value = re.sub(r"^[ \t]*[#*:;]+[ \t]?", "", value, flags=re.M)
    value = re.sub(r"^----+$", "", value, flags=re.M)
    value = html.unescape(value)
    value = re.sub(r"[ \t　]+", "", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return {
        "text": value.strip(),
        "transform": WIKISOURCE_TRANSFORM,
        "note": "维基文库 wikitext 清理：移除模板、注释、表格与链接标记，保留正文和段落。",
    }


def wikisource_section(raw: str) -> str:
    """Extract the human-readable section label from the header template."""
    match = re.search(r"\|\s*section\s*=\s*([^\n]+)", raw)
    if not match:
        return ""
    value = match.group(1).split("|")[0]
    value = re.sub(r"'''?", "", value)
    value = re.sub(r"-{(.*?)}-", r"\1", value)
    value = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("}}", "").strip()
    return html.unescape(value).strip()


def parse_wikisource(raw: str, *, fallback_title: str, fallback_volume: str,
                     sequence: int = 1) -> ChapterDraft:
    """Parse a Wikisource chapter page into stored text without editorial headings.

    Section headings (``==李白==`` etc.) are editorial navigation.  They are
    removed from the stored ancient text and recorded as labels on the
    paragraphs that follow them, so biography boundaries remain usable without
    pretending the headings are part of the original.
    """
    cleaned = clean_wikitext(raw)["text"]
    title = wikisource_section(raw) or fallback_title
    lines = cleaned.split("\n")
    body_parts: list[str] = []
    body_cursor = 0
    sections: list[tuple[str, int, int]] = []
    current_label = ""
    current_start: int | None = None
    for line in lines:
        heading = re.fullmatch(r"={1,6}\s*(.+?)\s*={1,6}", line.strip())
        if heading:
            if current_label and current_start is not None and current_start < body_cursor:
                sections.append((current_label, current_start, body_cursor))
            current_label = heading.group(1).strip()
            current_start = body_cursor
            continue
        if body_parts:
            body_parts.append("\n")
            body_cursor += 1
        body_parts.append(line)
        body_cursor += len(line)
    if current_label and current_start is not None and current_start < body_cursor:
        sections.append((current_label, current_start, body_cursor))
    raw_text = "".join(body_parts)
    left = len(raw_text) - len(raw_text.lstrip())
    text = raw_text.strip()
    sections = [(name, max(0, start - left), min(len(text), max(0, end - left)))
                for name, start, end in sections]
    sections = [(name, start, end) for name, start, end in sections if end > start]
    paragraphs: list[tuple[str, int, int]] = []
    for block, start, end in paragraphs_from_blank_lines(text):
        label = ""
        for name, section_start, section_end in sections:
            if section_start <= start < section_end:
                label = name
                break
        paragraphs.append((label, start, end))
    return ChapterDraft(
        volume=fallback_volume,
        title=title,
        sequence=sequence,
        text=text,
        pages=[(name, start, end) for name, start, end in sections],
        paragraphs=paragraphs,
        transform=WIKISOURCE_TRANSFORM,
        note="维基文库转录：移除模板、注释与编辑性标题，保留正文与段落；标题作为段落标签保存。",
    )


def paragraphs_from_blank_lines(text: str) -> list[tuple[str, int, int]]:
    """Return real paragraph spans from blank-line separated Wikisource text."""
    result: list[tuple[str, int, int]] = []
    cursor = 0
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            cursor += len(block) + 2
            continue
        start = text.find(block, cursor)
        if start < 0:
            start = cursor
        end = start + len(block)
        result.append((block.strip(), start, end))
        cursor = end
    # Trim leading/trailing whitespace inside each paragraph while keeping the
    # offsets consistent with the stored text.
    cleaned: list[tuple[str, int, int]] = []
    for block, start, end in result:
        left = len(block) - len(block.lstrip())
        right = len(block) - len(block.rstrip())
        cleaned.append((block.strip(), start + left, end - right))
    return cleaned


def _compact_with_map(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    mapping: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        chars.append(char)
        mapping.append(index)
    return "".join(chars), mapping


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Locate a quote in text, tolerating only layout whitespace differences.

    Returns offsets into the stored ``text``.  The comparison also applies NFC
    normalization; no traditional/simplified conversion is used for the final
    stored quote.
    """
    target = nfc(quote).strip()
    if not target:
        return None
    direct = nfc(text).find(target)
    if direct >= 0:
        return direct, direct + len(target)
    compact_text, mapping = _compact_with_map(nfc(text))
    compact_quote = re.sub(r"\s+", "", target)
    position = compact_text.find(compact_quote)
    if position < 0:
        return None
    start = mapping[position]
    end = mapping[position + len(compact_quote) - 1] + 1
    return start, end


def normalised_quote(text: str, start: int, end: int) -> str:
    return re.sub(r"\s+", "", nfc(text)[start:end])


def sentence_context(text: str, start: int, end: int, *, before: int = 2, after: int = 2,
                     limit: int = 1200) -> str:
    """Expand a located quote to complete sentences around it."""
    if not text:
        return ""
    left = start
    seen = 0
    index = start - 1
    while index >= 0 and seen < before:
        if text[index] in "。！？":
            left = index + 1
            seen += 1
        index -= 1
    right = end
    seen = 0
    index = end
    while index < len(text) and seen < after:
        if text[index] in "。！？":
            right = index + 1
            seen += 1
        index += 1
    while right < len(text) and text[right] in CLOSERS:
        right += 1
    snippet = text[left:right]
    if len(snippet) > limit:
        snippet = snippet[:limit]
    return snippet.strip()


def context_paragraphs(paragraphs: list[tuple[str, int, int]], start: int, end: int,
                       *, span: int = 1) -> list[tuple[str, int, int]]:
    """Return the paragraph containing a quote plus neighbours."""
    index = None
    for position, (_, p_start, p_end) in enumerate(paragraphs):
        if p_start <= start and end <= p_end:
            index = position
            break
    if index is None:
        return []
    low = max(0, index - span)
    high = min(len(paragraphs), index + span + 1)
    return paragraphs[low:high]


def verify_quote(text: str, quote: str, start: int, end: int) -> bool:
    if start < 0 or end > len(text) or end <= start:
        return False
    if normalised_quote(text, start, end) != re.sub(r"\s+", "", nfc(quote)):
        return False
    return locate_quote(text, quote) is not None
