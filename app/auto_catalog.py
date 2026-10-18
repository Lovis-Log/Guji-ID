"""Optional read models for additive stage-six tables; old four-table DBs work."""


def has_auto_schema(c):
    return c.execute("SELECT 1 FROM sqlite_master WHERE name='automatic_evidence' AND type='table'").fetchone() is not None


def matching_variants(c, query):
    if not has_auto_schema(c):
        return []
    from app.automatic_ingestion import search_key
    return [r[0] for r in c.execute("SELECT alias_id FROM search_variants WHERE term IN (?,?)", (query, search_key(query)))]


def enrich_people(c, people, public_cte=None):
    if not has_auto_schema(c):
        return people
    for p in people:
        row = c.execute("SELECT e.external_id,r.version FROM external_people e JOIN data_releases r USING(release_id) WHERE e.person_id=?", (p["person_id"],)).fetchone()
        if row:
            p.update(verification_label="结构化来源核准", source_person_id=row[0], source_version=row[1])
            if public_cte:
                p['structured_aliases']=[dict(r) for r in c.execute(public_cte+"SELECT alias_name,alias_type FROM public_aliases WHERE person_id=? AND alias_type!='name' ORDER BY alias_type,alias_id LIMIT 3",(p['person_id'],))]
    return people


def pipeline_counts(db):
    from app.db import connect_db
    with connect_db(db) as c:
        if not has_auto_schema(c):
            return None
        result = {"structured": c.execute("SELECT count(*) FROM automatic_evidence WHERE method='A'").fetchone()[0],
                  "text": c.execute("SELECT count(*) FROM automatic_evidence WHERE method='A+'").fetchone()[0]}
        summary = c.execute("SELECT * FROM pipeline_summary WHERE singleton=1").fetchone()
        result.update(quarantined=summary["quarantined"] if summary else 0, staged=summary["staged"] if summary else 0)
        row = c.execute("SELECT b.batch_id,b.imported_at,b.counts_json FROM auto_batches b WHERE b.status='committed' ORDER BY (SELECT max(event_id) FROM auto_events e WHERE e.batch_id=b.batch_id) DESC LIMIT 1").fetchone()
        result["latest"] = dict(row) if row else None
        if row:
            import json
            result['latest']['inserted']=json.loads(row['counts_json'])
        return result
