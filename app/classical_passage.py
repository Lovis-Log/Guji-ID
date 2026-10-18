"""Readable, source-bound classical passages without changing evidence facts."""
import hashlib
import json
import re
from functools import lru_cache

from app.db import PROJECT_ROOT

JTS_ROOT = PROJECT_ROOT / 'data/external/wikisource-jts190b'
PAGE_BREAK = re.compile(r'<pb:[A-Za-z0-9_-]+>')


def _readable(value):
    # Kanripo uses ¶ for physical line ends and <pb:...> for page breaks.
    # Joining those layout marks adds no words or editorial punctuation.
    return PAGE_BREAK.sub('', value).replace('¶\n', '').replace('¶', '').strip()


@lru_cache(maxsize=1)
def _jts_text():
    manifest = json.loads((JTS_ROOT / 'validated_quotes.json').read_text(encoding='utf-8'))
    raw = (JTS_ROOT / manifest['raw_path']).read_bytes()
    text_bytes = (JTS_ROOT / manifest['text_path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['raw_sha256']:
        raise ValueError('原始古籍响应哈希不一致')
    if hashlib.sha256(text_bytes).hexdigest() != manifest['text_sha256']:
        raise ValueError('古籍正文缓存哈希不一致')
    return manifest, text_bytes.decode('utf-8')


def _context(item, quote, notes):
    context = notes.get('context')
    if isinstance(context, str) and context.count(quote) == 1:
        return context, '已保存的原文上下文'
    if notes.get('verification_method') == 'wikisource_fixed_text_v1':
        manifest, text = _jts_text()
        loc = notes.get('locator') or {}
        case = next((r for r in manifest['cases'] if r['alias_id'] == item.get('alias_id')
                     and r['quote'] == quote), None)
        if (case and loc.get('start') == case['start'] and loc.get('end') == case['end']
                and loc.get('raw_sha256') == manifest['raw_sha256']
                and loc.get('text_sha256') == manifest['text_sha256']
                and text[case['start']:case['end']] == quote):
            return text, '维基文库固定修订的正文节录'
    return quote, '已核对的原文摘录'


def _slice_context(context, quote, name):
    pos = context.find(quote)
    if pos < 0:
        return quote
    # Headword biographies start exactly at the named person's sentence; a
    # preceding biography must never enter the public passage.
    if quote.startswith(name):
        start = pos
    else:
        start = max(0, pos - 65)
        for boundary in ('。', '！', '？', '\n'):
            hit = context.find(boundary, start, pos)
            if hit >= 0:
                start = max(start, hit + 1)
    minimum = pos + len(quote)
    target = min(len(context), max(minimum + 80, start + 170))
    ceiling = min(len(context), max(minimum + 180, start + 280))
    end = -1
    for boundary in ('。', '！', '？'):
        hit = context.find(boundary, target, ceiling)
        if hit >= 0 and (end < 0 or hit < end):
            end = hit
    if end >= 0:
        end += 1
    else:
        end = ceiling
        # Physical transcription lines must not be cut in half when possible.
        next_line = context.find('\n', end, min(len(context), end + 30))
        if next_line >= 0:
            end = next_line
    return context[start:max(end, minimum)].strip()


def _highlight(quote, alias_name, alias_type):
    if alias_type == 'posthumous' and alias_name:
        for marker in ('諡', '谥'):
            marker_pos = quote.find(marker)
            alias_pos = quote.find(alias_name, marker_pos)
            if marker_pos >= 0 and 0 <= alias_pos - marker_pos <= 10:
                start = max(quote.rfind('。', 0, marker_pos), quote.rfind('！', 0, marker_pos),
                            quote.rfind('？', 0, marker_pos)) + 1
                end = quote.find('。', alias_pos)
                return quote[start:end + 1] if end >= 0 else quote[start:]
    if '。' in quote:
        return quote[:quote.index('。') + 1]
    if alias_type == 'courtesy' and alias_name:
        marker = '字' + alias_name
        pos = quote.find(marker)
        if pos >= 0:
            return quote[:pos + len(marker)]
    return quote.split('¶')[0].split('\n')[0]


def passage_for(item, *, name, alias_name, alias_type):
    """Return verified contiguous prose and exact highlighted source span.

    None means no defensible longer passage, so callers keep the short quote.
    The template escapes all three parts independently before inserting <mark>.
    """
    quote = item.get('original_text') or ''
    if not quote:
        return None
    try:
        notes = json.loads(item.get('notes') or '{}')
    except (TypeError, ValueError):
        return None
    if not isinstance(notes, dict) or notes.get('evidence_kind') == 'structured_record':
        return None
    try:
        context, provenance = _context(item, quote, notes)
    except (OSError, ValueError, KeyError):
        return None
    passage = _slice_context(context, quote, name)
    selected = _highlight(quote, alias_name, alias_type)
    if not selected or selected not in passage:
        return None
    readable_passage = _readable(passage)
    readable_selected = _readable(selected)
    if not readable_selected or readable_passage.count(readable_selected) != 1:
        return None
    start = readable_passage.index(readable_selected)
    return {'before': readable_passage[:start], 'highlight': readable_selected,
            'after': readable_passage[start + len(readable_selected):],
            'text': readable_passage, 'provenance': provenance,
            'layout_normalized': '<pb:' in passage or '¶' in passage}
