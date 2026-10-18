"""Public, read-only catalog over the existing four tables.

Status flags alone cannot expose an alias: it needs located, verified direct
support. Every query uses the same visibility rules and bound SQL parameters.
"""

import json
from urllib.parse import urlsplit

from app.db import connect_db
from app.normalization import normalize_name
from app.auto_catalog import enrich_people, matching_variants
from app.field_quality import has_quality, enrich_fields, LABELS
from app.presentation import person_view, field_view, evidence_view, readable_text
from app.classical_passage import passage_for

ALIAS_TYPES = {"name": "姓名", "courtesy": "字", "art": "号", "posthumous": "谥号", "other": "其他称呼", "unknown": "类别未知"}
USAGE_RELATIONS = {"supports": "支持归属", "refutes": "反对归属", "mentions": "仅提及", "uncertain": "关系未确定"}
EVIDENCE_NATURES = {"direct": "直接证据", "indirect": "间接证据"}
UNKNOWN = "暂无已核实信息"

PUBLIC_CTE = """
WITH public_evidence AS (
    SELECT e.*, s.book_title, s.volume, s.chapter, s.platform,
           s.original_url, s.edition
    FROM evidences e JOIN sources s USING (source_id)
    WHERE e.verification_status = 'verified'
      AND safe_source_url(s.original_url) = 1
      AND ((length(trim(e.original_text)) > 0
            AND (length(trim(coalesce(s.volume, ''))) > 0 OR length(trim(coalesce(s.chapter, ''))) > 0))
           OR EXISTS(SELECT 1 FROM catalog_quality q WHERE q.alias_id=e.alias_id
                     AND q.source_id=e.source_id AND q.grade IN ('B','C')))
), public_aliases AS (
    SELECT a.* FROM aliases a JOIN people p USING (person_id)
    WHERE a.verification_status = 'verified'
      AND p.verification_status = 'verified'
      AND (EXISTS (
          SELECT 1 FROM public_evidence e WHERE e.alias_id = a.alias_id
          AND e.usage_relation = 'supports' AND e.evidence_nature = 'direct'
      ) OR EXISTS (SELECT 1 FROM catalog_quality q WHERE q.alias_id=a.alias_id AND q.grade IN ('B','C')))
)
"""


def safe_source_url(value):
    """Validate without fetching, guessing, normalizing, or replacing the URL."""
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        url = urlsplit(value)
        url.port
        return bool(url.scheme in {"https", "http"} and url.hostname and not url.username and not url.password)
    except ValueError:
        return False


def prepare(connection):
    connection.create_function("safe_source_url", 1, safe_source_url, deterministic=True)
    connection.create_function("catalog_name", 1, normalize_name, deterministic=True)
    connection.execute('DROP VIEW IF EXISTS temp.catalog_quality')
    if has_quality(connection):
        connection.execute('''CREATE TEMP VIEW catalog_quality AS SELECT q.* FROM relation_quality q
            JOIN sources s USING(source_id) WHERE safe_source_url(s.original_url)=1''')
    else:
        connection.execute("CREATE TEMP VIEW catalog_quality AS SELECT NULL alias_id,NULL grade,NULL source_id WHERE 0")


def year_label(value):
    if value is None:
        return UNKNOWN
    return f"公元前{abs(value)}年" if value < 0 else f"公元{value}年"


def present_person(row):
    person = dict(row)
    person["birth_label"] = year_label(person["birth_year"])
    person["death_label"] = year_label(person["death_year"])
    if person["birth_year"] is None and person["death_year"] is None:
        person["lifespan"] = UNKNOWN
    else:
        person["lifespan"] = person["birth_label"] + " 至 " + person["death_label"]
    return person


def present_alias(row):
    alias = dict(row)
    alias["type_label"] = ALIAS_TYPES[alias["alias_type"]]
    return alias


def public_counts(db_path):
    with connect_db(db_path) as connection:
        prepare(connection)
        return dict(connection.execute(PUBLIC_CTE + """
            SELECT (SELECT count(*) FROM people WHERE verification_status = 'verified') AS people,
                   (SELECT count(*) FROM public_aliases) AS aliases
        """).fetchone())


def identity_evidences(connection, person_ids):
    """Compare people using approved quotes, without promoting seed metadata."""
    if not person_ids:
        return {}
    placeholders = ",".join("?" for _ in person_ids)
    rows = connection.execute(PUBLIC_CTE + f"""
        SELECT a.person_id, a.alias_id, a.alias_name, a.alias_type,
               e.original_text, e.book_title, e.volume, e.chapter, e.original_url
        FROM public_aliases a JOIN public_evidence e USING(alias_id)
        WHERE a.person_id IN ({placeholders}) AND e.usage_relation='supports'
          AND e.evidence_nature='direct'
          AND CASE WHEN json_valid(e.notes) THEN coalesce(json_extract(e.notes,'$.evidence_kind'),'classical_text') ELSE 'classical_text' END != 'structured_record'
        ORDER BY a.person_id, CASE a.alias_type WHEN 'courtesy' THEN 0
            WHEN 'posthumous' THEN 1 ELSE 2 END, a.alias_id, e.evidence_id
    """, tuple(person_ids)).fetchall()
    result, seen = {}, set()
    for row in rows:
        item = dict(row)
        item['original_text']=readable_text(item['original_text'])
        if not item['original_text']:
            continue
        key = (item["person_id"], item["original_url"], item["original_text"])
        if key in seen or len(result.get(item["person_id"], [])) >= 2:
            continue
        seen.add(key)
        item["type_label"] = ALIAS_TYPES[item["alias_type"]]
        result.setdefault(item["person_id"], []).append(item)
    return result


def search_examples(db_path):
    """Only suggest terms which currently resolve to reviewed public records."""
    with connect_db(db_path) as connection:
        prepare(connection)
        shared = connection.execute(PUBLIC_CTE + """
            SELECT normalized_name AS query FROM public_aliases
            GROUP BY normalized_name ORDER BY count(DISTINCT person_id) DESC, normalized_name
            LIMIT 4
        """).fetchall()
        same = connection.execute("""SELECT canonical_name AS query FROM people
            WHERE verification_status='verified' GROUP BY canonical_name
            HAVING count(*) > 1 ORDER BY canonical_name LIMIT 2""").fetchall()
    return list(dict.fromkeys(row["query"] for row in [*same, *shared]))[:6]


def search_people(db_path, query):
    name = normalize_name(query)
    if not name:
        return []
    with connect_db(db_path) as connection:
        prepare(connection)
        if has_quality(connection):
            return quality_search(connection,name)
        variant_ids = matching_variants(connection, name)
        variant_marks = ','.join('?' for _ in variant_ids) or 'NULL'
        rows = connection.execute(PUBLIC_CTE + """
            SELECT p.* FROM people p
            WHERE p.verification_status = 'verified'
              AND (catalog_name(p.canonical_name) = ? OR EXISTS (
                  SELECT 1 FROM public_aliases a
                  WHERE a.person_id = p.person_id AND (a.normalized_name = ? OR a.alias_id IN (""" + variant_marks + """))
              ))
            ORDER BY p.canonical_name, coalesce(p.dynasty, ''), p.person_id
        """, (name, name, *variant_ids)).fetchall()
        matched = connection.execute(PUBLIC_CTE + """
            SELECT a.* FROM public_aliases a WHERE a.normalized_name = ? OR a.alias_id IN (""" + variant_marks + """)
            ORDER BY a.person_id, a.alias_type, a.alias_id
        """, (name, *variant_ids)).fetchall()
        identities = identity_evidences(connection, [row["person_id"] for row in rows])
        presented = enrich_people(connection, [present_person(row) for row in rows], PUBLIC_CTE)
    by_person = {}
    for alias in matched:
        by_person.setdefault(alias["person_id"], []).append(present_alias(alias))
    people = []
    for person in presented:
        person["matched_aliases"] = by_person.get(person["person_id"], [])
        person["canonical_match"] = normalize_name(person["canonical_name"]) == name
        person["identity_evidences"] = identities.get(person["person_id"], [])
        people.append(person_view(person))
    # Deliberately no LIMIT 1, fuzzy match, name-based grouping or auto redirect.
    return people


def get_person(db_path, person_id):
    with connect_db(db_path) as connection:
        prepare(connection)
        row = connection.execute("SELECT * FROM people WHERE person_id = ? AND verification_status = 'verified'", (person_id,)).fetchone()
        if row is None:
            return None
        aliases = connection.execute(PUBLIC_CTE + """
            SELECT a.*, (SELECT count(*) FROM public_evidence e
                         WHERE e.alias_id = a.alias_id) AS evidence_count
            FROM public_aliases a WHERE a.person_id = ?
            ORDER BY CASE a.alias_type WHEN 'name' THEN 0 WHEN 'courtesy' THEN 1
                     WHEN 'art' THEN 2 WHEN 'posthumous' THEN 3 ELSE 4 END,
                     a.alias_name, a.alias_id
        """, (person_id,)).fetchall()
        identities = identity_evidences(connection, [person_id])
        person = enrich_fields(connection,enrich_people(connection, [present_person(row)], PUBLIC_CTE))[0]
    person["aliases"] = [present_alias(alias) for alias in aliases]
    person["identity_evidences"] = identities.get(person_id, [])
    person['field_sources']={field:[field_view(f) for f in facts] for field,facts in person.get('field_sources',{}).items()}
    person=person_view(person)
    person['reading_lifespan']=person['presentation']['lifespan']
    person['primary_aliases']=[{'type_label':a['label'],'alias_name':a['value']} for a in person['presentation']['primary_aliases']]
    return person


def quality_search(c,name):
    from app.automatic_ingestion import search_key
    key=search_key(name)
    rows=c.execute('''SELECT p.*,min(CASE WHEN t.original=? THEN 0 WHEN t.term=? THEN 1
        WHEN instr(t.term,?)=1 THEN 2 ELSE 3 END) AS match_rank
        FROM catalog_terms t JOIN people p USING(person_id)
        WHERE p.verification_status='verified' AND instr(t.term,?)>0
        GROUP BY p.person_id ORDER BY match_rank,p.canonical_name,p.person_id''',(name,key,key,key)).fetchall()
    exact=[r for r in rows if r['match_rank']<2]
    fuzzy=[r for r in rows if r['match_rank']>=2][:50]
    people=enrich_fields(c,enrich_people(c,[present_person(r) for r in [*exact,*fuzzy]],PUBLIC_CTE))
    identities=identity_evidences(c,[p['person_id'] for p in people])
    for p in people:
        p['matched_aliases']=[present_alias(a) for a in c.execute(PUBLIC_CTE+'''SELECT a.* FROM public_aliases a
          WHERE person_id=? AND alias_id IN (SELECT alias_id FROM catalog_terms WHERE instr(term,?)>0)
          ORDER BY CASE WHEN alias_name=? THEN 0 ELSE 1 END,alias_type,alias_id''',(p['person_id'],key,name))]
        p['canonical_match']=search_key(p['canonical_name'])==key
        p['match_mode']='精确匹配' if p['match_rank']<2 else '模糊匹配'
        p['identity_evidences']=identities.get(p['person_id'],[])
    return [person_view(p) for p in people]


def evidence_notes(value):
    """Existing notes can be prose or collector JSON; never infer self-naming.

    Whitelist public explanatory fields; don't expose raw paths/review internals.
    'direct' establishes an alias relation and never means 'author named self'.
    """
    result = {"relation_type": UNKNOWN, "evidence_type": UNKNOWN, "context": "", "remarks": "", "remarks_label": "核对说明", "evidence_kind": "classical_text", "verification_method": "human", "source_version": "", "record_key": "", "source_locator": "", "source_lines": ""}
    if not value:
        return result
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        result["remarks"] = value
        return result
    if isinstance(parsed, dict):
        for key in ("relation_type", "evidence_type", "context", "remarks"):
            if isinstance(parsed.get(key), str) and parsed[key].strip():
                result[key] = parsed[key]
        if isinstance(parsed.get("reviewer_model"), str) and parsed["reviewer_model"].strip():
            result["remarks_label"] = "机器辅助说明（来自既有核验记录）"
        if parsed.get('evidence_kind') == 'structured_record' and (parsed.get('verification_method') == 'A' or parsed.get('quality_grade') in {'B','C'}):
            result.update(evidence_kind='structured_record', verification_method='A',
                          source_version=parsed.get('source_version',''), record_key=json.dumps(parsed.get('record_key',{}),ensure_ascii=False), remarks_label='结构化记录说明')
            result['quality_grade']=parsed.get('quality_grade','B')
            if result['quality_grade']=='C':result['verification_method']='stage65_supplement'
        elif parsed.get('verification_method') in ('A+','kanripo_fixed_headline_v1'):
            result['verification_method'] = parsed['verification_method']
            loc = parsed.get('locator',{})
            if all(isinstance(loc.get(k),int) and loc[k] >= 0 for k in ('start','end','line_start','line_end')):
                result['source_locator'] = f"正文字符 {loc['start']}–{loc['end']}（零起点，右端不含）；行 {loc['line_start']}–{loc['line_end']}"
                result['source_lines'] = f"#L{loc['line_start']}-L{loc['line_end']}"
        elif parsed.get('verification_method') == 'wikisource_fixed_text_v1' and parsed.get('evidence_kind') == 'classical_text':
            result['verification_method'] = 'wikisource_fixed_text_v1'
            result['quality_grade'] = 'A'
            result['source_version'] = str(parsed.get('source_version') or '')
            loc = parsed.get('locator',{})
            if all(isinstance(loc.get(k),int) and loc[k] >= 0 for k in ('start','end','line_start','line_end')):
                result['source_locator'] = f"正文第{loc['line_start']}–{loc['line_end']}行；字符 {loc['start']}–{loc['end']}（零起点，右端不含）"
                # The source URL already has the verified section anchor; do not add a second '#'.
    else:
        result["remarks"] = value
    return result


def get_alias(db_path, alias_id):
    with connect_db(db_path) as connection:
        prepare(connection)
        row = connection.execute(PUBLIC_CTE + "SELECT * FROM public_aliases WHERE alias_id = ?", (alias_id,)).fetchone()
        if row is None:
            return None
        person = connection.execute("SELECT * FROM people WHERE person_id = ?", (row["person_id"],)).fetchone()
        evidences = connection.execute(PUBLIC_CTE + """
            SELECT * FROM public_evidence WHERE alias_id = ?
            ORDER BY CASE usage_relation WHEN 'supports' THEN 0 WHEN 'refutes' THEN 1
                     WHEN 'mentions' THEN 2 ELSE 3 END, evidence_id
        """, (alias_id,)).fetchall()
    alias = present_alias(row)
    alias["person"] = present_person(person)
    alias["evidences"] = []
    for evidence in evidences:
        item = dict(evidence)
        passage = passage_for(item,name=alias['person']['canonical_name'],
            alias_name=alias['alias_name'],alias_type=alias['alias_type'])
        item["usage_label"] = USAGE_RELATIONS[item["usage_relation"]]
        item["nature_label"] = EVIDENCE_NATURES[item["evidence_nature"]]
        item['raw_notes']=item['notes']
        item.update(evidence_notes(item.pop("notes")))
        item['reading_context']=readable_text(item.get('context'))
        item['reading_remarks']=readable_text(item.get('remarks'))
        presented=evidence_view(item,alias,alias['person'])
        if presented['presentation']['kind']=='classical_text':
            presented['passage']=passage
        alias["evidences"].append(presented)
    alias["has_non_support"] = any(e["usage_relation"] != "supports" for e in alias["evidences"])
    alias["verification_label"] = "结构化来源核准" if alias["evidences"] and all(e['evidence_kind']=='structured_record' for e in alias["evidences"]) else "别称归属已核对"
    alias["has_classical"] = any(e['evidence_kind']=='classical_text' for e in alias["evidences"])
    with connect_db(db_path) as c:
        if has_quality(c):
            q=c.execute('SELECT grade FROM relation_quality WHERE alias_id=?',(alias_id,)).fetchone()
            if q:alias['verification_label']=LABELS[q[0]]
    return alias
